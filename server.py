
import os
import json
import time
import random
import logging
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova")

APP_VERSION = "5.3.0"

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

PRIMARY_MODEL = os.getenv(
    "GEMINI_MODEL", "gemini-3.5-flash-lite"
).strip()

FALLBACK_MODEL = os.getenv(
    "GEMINI_FALLBACK_MODEL", ""
).strip()

EXTRA_MODELS = os.getenv(
    "GEMINI_EXTRA_MODELS", ""
).strip()

if not API_KEY:
    logger.error("GEMINI_API_KEY n'est pas configurée.")

client = genai.Client(api_key=API_KEY) if API_KEY else None

app = FastAPI(title="NOVA API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------
# MODÈLES DE DONNÉES
# --------------------------------------------------

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    fast: bool = False
    device: str = "unknown"
    memory_context: Any = ""
    history: list[Any] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    memory_updates: list[Any] = Field(default_factory=list)


# --------------------------------------------------
# MODÈLES GEMINI ET BASCULEMENT AUTOMATIQUE
# --------------------------------------------------

def get_model_candidates() -> list[str]:
    raw = [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        *EXTRA_MODELS.split(","),
    ]

    models = []
    for name in raw:
        name = name.strip()
        if name and name not in models:
            models.append(name)

    return models


def is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()

    markers = (
        "429", "500", "502", "503", "504",
        "unavailable", "resource_exhausted",
        "deadline_exceeded", "internal server error",
        "temporarily", "timeout", "timed out",
        "connection reset", "overloaded",
    )

    return any(marker in message for marker in markers)


def generate_with_fallback(contents: str, fast: bool = False) -> str:
    if client is None:
        raise RuntimeError("La clé API Gemini n'est pas configurée.")

    models = get_model_candidates()
    if not models:
        raise RuntimeError("Aucun modèle Gemini n'est configuré.")

    # Le mode rapide évite les longues séries de tentatives.
    attempts_per_model = 1 if fast else 2
    errors = []

    for model in models:
        for attempt in range(attempts_per_model):
            try:
                logger.info(
                    "Appel Gemini : modèle=%s, tentative=%s",
                    model, attempt + 1,
                )

                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.6,
                        response_mime_type="application/json",
                    ),
                )

                text = getattr(response, "text", None)
                if not text or not text.strip():
                    raise RuntimeError("Réponse vide du modèle.")

                logger.info("Réponse obtenue avec %s", model)
                return text.strip()

            except Exception as exc:
                errors.append(f"{model}: {exc}")
                logger.warning(
                    "Échec du modèle %s : %s", model, exc
                )

                # Les erreurs de clé, de permission ou de modèle
                # introuvable ne se corrigent pas en réessayant
                # le même modèle.
                message = str(exc).lower()
                permanent_markers = (
                    "api key not valid",
                    "invalid api key",
                    "permission_denied",
                    "unauthenticated",
                    "not found",
                    "does not exist",
                )

                if any(marker in message for marker in permanent_markers):
                    break

                if not is_retryable_error(exc):
                    break

                if attempt + 1 < attempts_per_model:
                    delay = min(2 ** attempt, 6) + random.uniform(0, 0.8)
                    time.sleep(delay)

        logger.warning("Passage au modèle suivant après %s", model)

    logger.error(
        "Tous les modèles ont échoué : %s",
        " | ".join(errors[-5:]),
    )

    raise RuntimeError(
        "Les modèles IA sont momentanément indisponibles. "
        "Réessaie dans quelques instants."
    )


# --------------------------------------------------
# CONSTRUCTION DU CONTEXTE DU CHAT
# --------------------------------------------------

def build_prompt(data: ChatRequest) -> str:
    history = data.history[-12:] if data.history else []

    instructions = """
Tu es NOVA, une assistante IA utile, claire et chaleureuse.
Réponds dans la langue utilisée par l'utilisateur.
Utilise le contexte mémoire uniquement s'il est pertinent.
Ne prétends jamais avoir effectué une action que tu n'as pas faite.
Ne révèle jamais de clés API ou de secrets.
Retourne exclusivement un objet JSON valide au format :
{
  "reply": "Ta réponse ici",
  "memory_updates": []
}
memory_updates doit être une liste. N'ajoute pas de Markdown autour du JSON.
"""

    payload = {
        "instructions": instructions,
        "device": data.device,
        "fast_mode": data.fast,
        "memory_context": data.memory_context,
        "history": history,
        "message": data.text,
    }

    return json.dumps(payload, ensure_ascii=False)


def parse_ai_response(raw: str) -> ChatResponse:
    # Supporte aussi une réponse enveloppée dans un bloc Markdown.
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)

        if isinstance(parsed, dict):
            reply = parsed.get("reply", "")
            updates = parsed.get("memory_updates", [])

            if not isinstance(reply, str):
                reply = str(reply)

            if not isinstance(updates, list):
                updates = []

            if reply.strip():
                return ChatResponse(
                    reply=reply.strip(),
                    memory_updates=updates,
                )

    except (json.JSONDecodeError, TypeError):
        pass

    # Si le modèle n'a pas respecté le JSON, préserver son texte.
    if cleaned:
        return ChatResponse(reply=cleaned, memory_updates=[])

    raise RuntimeError("La réponse IA est vide.")


# --------------------------------------------------
# ROUTES WEB
# --------------------------------------------------

@app.get("/")
def home():
    if os.path.isfile("index.html"):
        return FileResponse("index.html")

    return {
        "app": "NOVA",
        "version": APP_VERSION,
        "status": "online",
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": "NOVA",
        "version": APP_VERSION,
    }


@app.get("/api/status")
def status():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "configured": client is not None,
        "models_configured": len(get_model_candidates()),
    }


# --------------------------------------------------
# CHAT
# --------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
def chat(data: ChatRequest):
    if not data.text.strip():
        raise HTTPException(
            status_code=400,
            detail="Le message est vide.",
        )

    if client is None:
        raise HTTPException(
            status_code=503,
            detail="La clé API Gemini n'est pas configurée sur le serveur.",
        )

    try:
        prompt = build_prompt(data)
        raw = generate_with_fallback(
            contents=prompt,
            fast=data.fast,
        )
        return parse_ai_response(raw)

    except Exception as exc:
        logger.exception("Erreur dans /api/chat")

        # Ne pas transmettre au navigateur les détails internes,
        # les identifiants ou les informations sensibles.
        raise HTTPException(
            status_code=503,
            detail=(
                "NOVA ne peut pas répondre pour le moment. "
                "Réessaie dans quelques instants."
            ),
        ) from exc


# --------------------------------------------------
# MÉTÉO
# --------------------------------------------------

@app.get("/api/weather")
def weather(city: str = "Grenoble"):
    city = city.strip()[:100]

    if not city:
        raise HTTPException(
            status_code=400,
            detail="Ville manquante.",
        )

    try:
        response = requests.get(
            "https://wttr.in/" + requests.utils.quote(city, safe=""),
            params={"format": "j1"},
            timeout=8,
            headers={"User-Agent": "NOVA-Weather/1.0"},
        )
        response.raise_for_status()
        result = response.json()

        current = result["current_condition"][0]
        description = current.get("weatherDesc", [{}])[0].get(
            "value", "Inconnue"
        )

        return {
            "city": city,
            "temperature": current.get("temp_C"),
            "feels_like": current.get("FeelsLikeC"),
            "description": description,
            "humidity": current.get("humidity"),
            "wind_kph": current.get("windspeedKmph"),
            "source": "wttr.in",
        }

    except Exception as exc:
        logger.warning("Erreur météo pour %s : %s", city, exc)
        raise HTTPException(
            status_code=502,
            detail="La météo est momentanément indisponible.",
        ) from exc


# --------------------------------------------------
# DÉMARRAGE
# --------------------------------------------------

@app.get("/api")
def api_root():
    return {
        "app": "NOVA",
        "version": APP_VERSION,
        "message": "API opérationnelle",
    }
