
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


# ==================================================
# NOVA — CONFIGURATION
# ==================================================

APP_VERSION = "5.4.0"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nova")


def read_env(name: str, default: str = "") -> str:
    """Lit une variable Render en supprimant les espaces inutiles."""
    return os.getenv(name, default).strip()


API_KEY = read_env("GEMINI_API_KEY")
PRIMARY_MODEL = read_env(
    "GEMINI_MODEL", "gemini-3.5-flash-lite"
)
FALLBACK_MODEL = read_env("GEMINI_FALLBACK_MODEL")
EXTRA_MODELS = read_env("GEMINI_EXTRA_MODELS")

# Le client est créé uniquement si la clé est présente.
client = None

if API_KEY:
    try:
        client = genai.Client(api_key=API_KEY)
        logger.info("Client Gemini initialisé.")
    except Exception:
        logger.exception("Impossible d'initialiser le client Gemini.")
else:
    logger.error(
        "GEMINI_API_KEY absente au démarrage du serveur."
    )


# ==================================================
# APPLICATION FASTAPI
# ==================================================

app = FastAPI(
    title="NOVA API",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ==================================================
# MODÈLES DE DONNÉES
# ==================================================

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    fast: bool = False
    device: str = "unknown"
    memory_context: Any = ""
    history: list[Any] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    memory_updates: list[Any] = Field(default_factory=list)


# ==================================================
# CLÉ API ET MODÈLES
# ==================================================

def get_api_key() -> str:
    """
    Relit la variable d'environnement à chaque vérification.
    Utile pour diagnostiquer une configuration Render incorrecte.
    """
    return read_env("GEMINI_API_KEY")


def get_client():
    """
    Réutilise le client existant si la clé n'a pas changé.
    Recrée le client si la clé a été ajoutée ou modifiée
    dans l'environnement avant un redémarrage.
    """
    global client, API_KEY

    current_key = get_api_key()

    if not current_key:
        return None

    if client is None or current_key != API_KEY:
        client = genai.Client(api_key=current_key)
        API_KEY = current_key

    return client


def get_model_candidates() -> list[str]:
    """Retourne les modèles configurés, sans doublons."""
    candidates = [
        PRIMARY_MODEL,
        FALLBACK_MODEL,
        *EXTRA_MODELS.split(","),
    ]

    result = []

    for model in candidates:
        model = model.strip()

        if model and model not in result:
            result.append(model)

    return result


def is_retryable_error(exc: Exception) -> bool:
    """Détecte les erreurs potentiellement temporaires."""
    message = str(exc).lower()

    markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "unavailable",
        "resource_exhausted",
        "deadline_exceeded",
        "internal server error",
        "temporarily",
        "timeout",
        "timed out",
        "connection reset",
        "overloaded",
        "service unavailable",
    )

    return any(marker in message for marker in markers)


def is_permanent_error(exc: Exception) -> bool:
    """Détecte les erreurs qui nécessitent une correction."""
    message = str(exc).lower()

    markers = (
        "api key not valid",
        "invalid api key",
        "permission_denied",
        "unauthenticated",
        "api_key_invalid",
        "does not exist",
        "not found",
    )

    return any(marker in message for marker in markers)


# ==================================================
# GÉNÉRATION GEMINI AVEC SECOURS
# ==================================================

def generate_with_fallback(
    contents: str,
    fast: bool = False,
) -> str:

    active_client = get_client()

    if active_client is None:
        raise RuntimeError(
            "GEMINI_API_KEY absente ou vide dans l'environnement."
        )

    models = get_model_candidates()

    if not models:
        raise RuntimeError(
            "Aucun modèle Gemini n'est configuré."
        )

    attempts_per_model = 1 if fast else 2
    errors = []

    for model in models:
        for attempt in range(attempts_per_model):
            try:
                logger.info(
                    "Gemini : modèle=%s, tentative=%s/%s",
                    model,
                    attempt + 1,
                    attempts_per_model,
                )

                response = active_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.6,
                        response_mime_type="application/json",
                    ),
                )

                answer = getattr(response, "text", None)

                if not answer or not answer.strip():
                    raise RuntimeError(
                        "Le modèle a renvoyé une réponse vide."
                    )

                logger.info(
                    "Réponse Gemini obtenue avec le modèle %s.",
                    model,
                )

                return answer.strip()

            except Exception as exc:
                errors.append((model, str(exc)))

                logger.warning(
                    "Échec Gemini sur %s : %s",
                    model,
                    exc,
                )

                if is_permanent_error(exc):
                    logger.warning(
                        "Erreur permanente sur %s ; "
                        "passage au modèle suivant.",
                        model,
                    )
                    break

                if not is_retryable_error(exc):
                    break

                if attempt + 1 < attempts_per_model:
                    delay = min(2 ** attempt, 6)
                    delay += random.uniform(0, 0.8)

                    logger.info(
                        "Nouvelle tentative dans %.1f secondes.",
                        delay,
                    )
                    time.sleep(delay)

        logger.warning(
            "Le modèle %s n'a pas fonctionné ; "
            "essai du prochain modèle.",
            model,
        )

    # Ne jamais inclure la clé API dans le message envoyé au client.
    logger.error(
        "Tous les modèles ont échoué : %s",
        " | ".join(
            f"{model}: {message[:250]}"
            for model, message in errors[-5:]
        ),
    )

    raise RuntimeError(
        "Aucun modèle Gemini configuré n'a pu répondre."
    )


# ==================================================
# PROMPT ET MÉMOIRE
# ==================================================

def build_prompt(data: ChatRequest) -> str:
    history = data.history[-12:]

    instructions = """
Tu es NOVA, une assistante IA utile, claire et chaleureuse.
Réponds dans la langue utilisée par l'utilisateur.
Utilise le contexte mémoire seulement lorsqu'il est pertinent.
Ne prétends jamais avoir réalisé une action que tu n'as pas faite.
Ne révèle jamais de clés API ou d'informations secrètes.
Retourne uniquement un objet JSON valide avec cette structure :
{
  "reply": "Réponse destinée à l'utilisateur",
  "memory_updates": []
}
memory_updates doit être une liste.
N'ajoute aucun bloc Markdown autour du JSON.
"""

    payload = {
        "instructions": instructions,
        "device": data.device,
        "fast_mode": data.fast,
        "memory_context": data.memory_context,
        "history": history,
        "message": data.text,
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
    )


def parse_ai_response(raw: str) -> ChatResponse:
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

    # Préserve une réponse textuelle même si le JSON est incorrect.
    if cleaned:
        return ChatResponse(
            reply=cleaned,
            memory_updates=[],
        )

    raise RuntimeError("La réponse de Gemini est vide.")


# ==================================================
# ROUTES PRINCIPALES
# ==================================================

@app.get("/")
def home():
    if os.path.isfile("index.html"):
        return FileResponse("index.html")

    return {
        "app": "NOVA",
        "version": APP_VERSION,
        "status": "online",
    }


@app.get("/api")
def api_root():
    return {
        "app": "NOVA",
        "version": APP_VERSION,
        "message": "API opérationnelle",
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
    """
    Vérifie la présence de la clé sans jamais la révéler.
    Cette route ne garantit pas que Gemini accepte la clé :
    seul un véritable appel permet de vérifier l'accès.
    """
    key_present = bool(get_api_key())

    return {
        "status": "ok",
        "version": APP_VERSION,
        "configured": key_present and get_client() is not None,
        "api_key_present": key_present,
        "models_configured": len(get_model_candidates()),
        "primary_model": PRIMARY_MODEL,
    }


# ==================================================
# ROUTE CHAT
# ==================================================

@app.post("/api/chat", response_model=ChatResponse)
def chat(data: ChatRequest):
    if not data.text.strip():
        raise HTTPException(
            status_code=400,
            detail="Le message est vide.",
        )

    if get_client() is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "La clé API Gemini est absente du serveur. "
                "Vérifie GEMINI_API_KEY dans Render."
            ),
        )

    try:
        prompt = build_prompt(data)

        raw = generate_with_fallback(
            contents=prompt,
            fast=data.fast,
        )

        return parse_ai_response(raw)

    except Exception:
        logger.exception("Erreur pendant le traitement du chat.")

        raise HTTPException(
            status_code=503,
            detail=(
                "NOVA ne peut pas répondre pour le moment. "
                "Vérifie les logs du serveur et réessaie."
            ),
        )


# ==================================================
# ROUTE MÉTÉO
# ==================================================

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
            "https://wttr.in/"
            + requests.utils.quote(city, safe=""),
            params={"format": "j1"},
            timeout=8,
            headers={"User-Agent": "NOVA-Weather/1.0"},
        )

        response.raise_for_status()
        result = response.json()

        current = result["current_condition"][0]

        description = current.get(
            "weatherDesc", [{}]
        )[0].get("value", "Inconnue")

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
        logger.warning(
            "Erreur météo pour %s : %s",
            city,
            exc,
        )

        raise HTTPException(
            status_code=502,
            detail="La météo est momentanément indisponible.",
        )


# ==================================================
# FIN DU SERVEUR
# ==================================================
