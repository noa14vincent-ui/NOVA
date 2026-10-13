
import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# --------------------------------------------------
# NOVA — configuration
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nova")

app = FastAPI(title="NOVA", version="5.2.0")
BASE_DIR = Path(__file__).resolve().parent

def clean_env(name, default=""):
    return os.getenv(name, default).strip()

API_KEY = clean_env("GEMINI_API_KEY")
MODEL = clean_env("GEMINI_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODEL = clean_env("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

client = genai.Client(api_key=API_KEY) if API_KEY else None

http = requests.Session()
http.headers.update({"User-Agent": "NOVA/5.2"})


# --------------------------------------------------
# Requêtes
# --------------------------------------------------

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    fast: bool = False
    device: str = "web"
    memory_context: str = ""
    history: list[dict] = Field(default_factory=list)


# --------------------------------------------------
# Utilitaires
# --------------------------------------------------

def safe_text(value, limit=1000):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def parse_context(raw):
    empty = {"souvenirs": [], "agenda": []}

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            return empty

        memories = []
        for item in data.get("souvenirs", [])[:50]:
            if isinstance(item, str):
                item = {"text": item, "category": "autre"}

            if isinstance(item, dict):
                text = safe_text(item.get("text", ""), 240)
                if text:
                    memories.append({
                        "text": text,
                        "category": safe_text(
                            item.get("category", "autre"), 30
                        )
                    })

        agenda = []
        for item in data.get("agenda", [])[:50]:
            if isinstance(item, dict):
                title = safe_text(item.get("title", ""), 100)
                if title:
                    agenda.append({
                        "title": title,
                        "date": safe_text(item.get("date", ""), 50),
                        "note": safe_text(item.get("note", ""), 150)
                    })

        return {"souvenirs": memories, "agenda": agenda}

    except (ValueError, TypeError):
        return empty


def parse_json(text):
    if not text:
        return None

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                return result if isinstance(result, dict) else None
            except ValueError:
                return None

    return None


def is_retryable_error(exc):
    """Reconnaît les erreurs temporaires et les modèles indisponibles."""
    text = str(exc).lower()

    retry_terms = (
        "503",
        "unavailable",
        "high demand",
        "429",
        "resource_exhausted",
        "500",
        "internal server error",
        "504",
        "deadline exceeded",
        "not found",
        "404",
        "unknown model",
        "model not found",
        "unsupported model",
    )

    return any(term in text for term in retry_terms)


# --------------------------------------------------
# Gemini : tentatives et modèle de secours
# --------------------------------------------------

def generate_with_retry(prompt):
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Clé GEMINI_API_KEY absente des variables Render."
        )

    models = [MODEL]

    if FALLBACK_MODEL and FALLBACK_MODEL != MODEL:
        models.append(FALLBACK_MODEL)

    last_error = None

    for model_index, model_name in enumerate(models):
        # Deux tentatives pour le modèle principal.
        # Une tentative pour le modèle de secours.
        attempts = 2 if model_index == 0 else 1

        for attempt in range(attempts):
            try:
                log.info(
                    "Appel Gemini : modèle=%s, tentative=%s",
                    model_name,
                    attempt + 1
                )

                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.6,
                    ),
                )

                if not getattr(response, "text", None):
                    raise RuntimeError(
                        "Gemini a renvoyé une réponse vide."
                    )

                log.info("Réponse Gemini reçue avec %s", model_name)
                return response

            except Exception as exc:
                last_error = exc
                log.warning(
                    "Échec Gemini sur %s : %s",
                    model_name,
                    str(exc)[:500]
                )

                # Une clé invalide ou un accès refusé ne sera pas
                # réparé en changeant de modèle.
                error_text = str(exc).lower()
                if any(term in error_text for term in (
                    "401",
                    "unauthenticated",
                    "invalid api key",
                    "api key not valid",
                    "403",
                    "permission_denied",
                    "permission denied",
                )):
                    break

                if not is_retryable_error(exc):
                    # On passe au modèle de secours si le modèle
                    # principal n'est pas accessible.
                    break

                if attempt < attempts - 1:
                    time.sleep(2)

        log.warning("Passage au modèle suivant si disponible.")

    log.error(
        "Tous les modèles Gemini ont échoué : %s",
        str(last_error)[:500] if last_error else "Erreur inconnue"
    )

    raise HTTPException(
        status_code=503,
        detail=(
            "Gemini est momentanément indisponible, ou aucun des "
            "modèles configurés n'est accessible. Réessaie plus tard "
            "ou vérifie GEMINI_MODEL dans Render."
        )
    )


# --------------------------------------------------
# Conversation
# --------------------------------------------------

def ask_gemini(payload):
    context = parse_context(payload.memory_context)

    history = []
    for item in payload.history[-10:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = safe_text(item.get("content", ""), 1200)

        if role in ("user", "assistant") and content:
            history.append({
                "role": role,
                "content": content
            })

    prompt = f"""
Tu es NOVA, un assistant personnel intelligent, chaleureux,
précis et naturel.

Règles :
- Réponds en français sauf demande contraire.
- Utilise les souvenirs quand ils sont pertinents.
- Ne prétends pas connaître une information personnelle absente
  des souvenirs ou de la conversation.
- Ne prétends pas avoir réalisé une action externe que tu n'as
  pas réellement effectuée.
- Ne demande jamais à mémoriser des mots de passe, clés API,
  informations bancaires ou secrets.
- Les suggestions de mémoire doivent être pertinentes et utiles.
- Ne suggère pas de mémoire pour une question ordinaire sans
  information personnelle durable.

Réponds exclusivement avec un objet JSON valide de cette forme :
{{
  "reply": "Ta réponse à l'utilisateur",
  "memory_updates": [
    {{"text": "Souvenir proposé", "category": "preferences"}}
  ]
}}

Si aucune mémoire n'est utile, utilise "memory_updates": [].
Maximum 3 suggestions de mémoire.
Catégories autorisées : profil, preferences, projets, objectifs, autre.

Date actuelle :
{datetime.now().astimezone().isoformat()}

Souvenirs de l'utilisateur :
{json.dumps(context["souvenirs"], ensure_ascii=False)}

Agenda de l'utilisateur :
{json.dumps(context["agenda"], ensure_ascii=False)}

Historique récent :
{json.dumps(history, ensure_ascii=False)}

Message actuel :
{payload.text}
""".strip()

    response = generate_with_retry(prompt)
    result = parse_json(response.text)

    if not result:
        log.error("Réponse JSON Gemini invalide.")
        raise HTTPException(
            status_code=502,
            detail="Gemini a renvoyé une réponse illisible. Réessaie."
        )

    reply = result.get("reply", "")
    if not isinstance(reply, str) or not reply.strip():
        raise HTTPException(
            status_code=502,
            detail="Gemini a renvoyé une réponse vide."
        )

    updates = []
    allowed_categories = {
        "profil", "preferences", "projets", "objectifs", "autre"
    }

    for item in result.get("memory_updates", [])[:3]:
        if not isinstance(item, dict):
            continue

        text = safe_text(item.get("text", ""), 240)
        category = safe_text(
            item.get("category", "autre"), 30
        ).lower()

        if not text:
            continue

        lowered = text.lower()
        sensitive_terms = (
            "mot de passe",
            "password",
            "api_key",
            "clé secrète",
            "numéro de carte",
            "code bancaire",
        )

        if any(term in lowered for term in sensitive_terms):
            continue

        updates.append({
            "text": text,
            "category": (
                category
                if category in allowed_categories
                else "autre"
            )
        })

    return {
        "reply": reply.strip(),
        "memory_updates": updates
    }


# --------------------------------------------------
# Pages et état du serveur
# --------------------------------------------------

@app.get("/")
@app.get("/index.html")
def index():
    path = BASE_DIR / "index.html"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Le fichier index.html est introuvable."
        )

    return FileResponse(path)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "NOVA",
        "version": "5.2.0",
        "gemini_configured": bool(API_KEY),
        "primary_model": MODEL,
        "fallback_model": FALLBACK_MODEL or None,
    }


@app.get("/api/status")
def status():
    return health()


# --------------------------------------------------
# Chat
# --------------------------------------------------

@app.post("/api/chat")
def chat(payload: ChatRequest):
    text = safe_text(payload.text, 12000)

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Écris un message avant d'envoyer."
        )

    payload.text = text
    return ask_gemini(payload)


# --------------------------------------------------
# Météo
# --------------------------------------------------

@app.get("/api/weather")
def weather(city: str = "Grenoble"):
    city = safe_text(city, 100)

    if not city:
        raise HTTPException(
            status_code=400,
            detail="Indique une ville."
        )

    try:
        geo_response = http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": city,
                "count": 1,
                "language": "fr",
                "format": "json",
            },
            timeout=10,
        )
        geo_response.raise_for_status()

        places = geo_response.json().get("results") or []

        if not places:
            raise HTTPException(
                status_code=404,
                detail=f"Ville introuvable : {city}"
            )

        place = places[0]

        weather_response = http.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": (
                    "temperature_2m,apparent_temperature,"
                    "relative_humidity_2m,weather_code,wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_max,temperature_2m_min,weather_code"
                ),
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=12,
        )
        weather_response.raise_for_status()
        data = weather_response.json()

        return {
            "city": place["name"],
            "country": place.get("country", ""),
            "current": data.get("current", {}),
            "daily": data.get("daily", {}),
            "timezone": data.get("timezone", ""),
        }

    except HTTPException:
        raise

    except requests.RequestException as exc:
        log.exception("Le service météo a échoué.")
        raise HTTPException(
            status_code=502,
            detail="Le service météo est momentanément indisponible."
        ) from exc
