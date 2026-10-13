
import os
import json
import logging
from datetime import datetime
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nova")

app = FastAPI(title="NOVA", version="5.1.0")
BASE_DIR = Path(__file__).resolve().parent

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
client = genai.Client(api_key=API_KEY) if API_KEY else None

http = requests.Session()
http.headers["User-Agent"] = "NOVA/5.1"


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    fast: bool = False
    device: str = "web"
    memory_context: str = ""
    history: list[dict] = Field(default_factory=list)


def safe_text(value, limit=1000):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def parse_context(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            return {"souvenirs": [], "agenda": []}
        memories = []
        for item in data.get("souvenirs", [])[:50]:
            if isinstance(item, str):
                item = {"text": item, "category": "autre"}
            if isinstance(item, dict):
                text = safe_text(item.get("text", ""), 250)
                if text:
                    memories.append({
                        "text": text,
                        "category": safe_text(item.get("category", "autre"), 30)
                    })
        agenda = []
        for item in data.get("agenda", [])[:50]:
            if isinstance(item, dict) and safe_text(item.get("title", "")):
                agenda.append({
                    "title": safe_text(item.get("title", ""), 100),
                    "date": safe_text(item.get("date", ""), 50),
                    "note": safe_text(item.get("note", ""), 150)
                })
        return {"souvenirs": memories, "agenda": agenda}
    except (ValueError, TypeError):
        return {"souvenirs": [], "agenda": []}


def parse_json(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].removesuffix("```").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                return result if isinstance(result, dict) else None
            except ValueError:
                pass
    return None


def ask_gemini(payload):
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Clé GEMINI_API_KEY manquante dans les variables Render."
        )

    context = parse_context(payload.memory_context)
    history = []
    for item in payload.history[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = safe_text(item.get("content", ""), 1200)
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})

    prompt = f"""
Tu es NOVA, un assistant personnel utile, fiable et naturel.
Réponds en français sauf si l'utilisateur demande une autre langue.
Utilise les souvenirs uniquement lorsqu'ils sont pertinents.
Ne fabrique pas de faits personnels et ne prétends pas avoir réalisé
une action externe que tu n'as pas effectuée.
Les souvenirs suggérés ne doivent être enregistrés qu'après confirmation
de l'utilisateur. Ne propose jamais de mots de passe, clés API ou données
bancaires à mémoriser.

Retourne exclusivement un JSON valide :
{{
  "reply": "Réponse à l'utilisateur",
  "memory_updates": [
    {{"text": "Information utile", "category": "preferences"}}
  ]
}}

memory_updates doit être une liste vide si aucune information personnelle
utile n'est explicitement donnée. Catégories : profil, preferences,
projets, objectifs, autre. Maximum 3 suggestions.

Date actuelle : {datetime.now().astimezone().isoformat()}

Souvenirs disponibles :
{json.dumps(context["souvenirs"], ensure_ascii=False)}

Agenda :
{json.dumps(context["agenda"], ensure_ascii=False)}

Historique récent :
{json.dumps(history, ensure_ascii=False)}

Nouveau message :
{payload.text}
""".strip()

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.6,
            ),
        )
    except Exception as exc:
        log.exception("Échec de l'appel Gemini")
        raise HTTPException(
            status_code=502,
            detail=f"Erreur Gemini ({type(exc).__name__}). Vérifie le modèle et les logs Render."
        ) from exc

    result = parse_json(getattr(response, "text", "") or "")
    if not result:
        log.error("Gemini a renvoyé une réponse JSON invalide")
        raise HTTPException(
            status_code=502,
            detail="Gemini a renvoyé une réponse illisible. Réessaie."
        )

    reply = safe_text(result.get("reply", ""), 8000)
    if not reply:
        raise HTTPException(status_code=502, detail="Gemini a renvoyé une réponse vide.")

    updates = []
    allowed = {"profil", "preferences", "projets", "objectifs", "autre"}
    for item in result.get("memory_updates", [])[:3]:
        if not isinstance(item, dict):
            continue
        text = safe_text(item.get("text", ""), 240)
        category = safe_text(item.get("category", "autre"), 30).lower()
        if not text:
            continue
        if any(secret in text.lower() for secret in (
            "mot de passe", "password", "api_key", "clé secrète",
            "numéro de carte", "code bancaire"
        )):
            continue
        updates.append({
            "text": text,
            "category": category if category in allowed else "autre"
        })

    return {"reply": reply, "memory_updates": updates}


@app.get("/")
@app.get("/index.html")
def index():
    path = BASE_DIR / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="index.html introuvable")
    return FileResponse(path)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "NOVA",
        "version": "5.1.0",
        "gemini_configured": bool(API_KEY),
        "model": MODEL
    }


@app.get("/api/status")
def status():
    return health()


@app.post("/api/chat")
def chat(payload: ChatRequest):
    text = safe_text(payload.text, 12000)
    if not text:
        raise HTTPException(status_code=400, detail="Message vide.")
    payload.text = text
    return ask_gemini(payload)


@app.get("/api/weather")
def weather(city: str = "Grenoble"):
    city = safe_text(city, 100)
    if not city:
        raise HTTPException(status_code=400, detail="Indique une ville.")

    try:
        geo = http.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "fr", "format": "json"},
            timeout=10,
        )
        geo.raise_for_status()
        places = geo.json().get("results") or []
        if not places:
            raise HTTPException(status_code=404, detail="Ville introuvable.")

        place = places[0]
        response = http.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "city": place["name"],
            "country": place.get("country", ""),
            "current": data.get("current", {}),
            "daily": data.get("daily", {}),
            "timezone": data.get("timezone", "")
        }
    except HTTPException:
        raise
    except requests.RequestException as exc:
        log.exception("Erreur météo")
        raise HTTPException(status_code=502, detail="Service météo indisponible.") from exc
