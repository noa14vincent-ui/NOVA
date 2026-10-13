
import os
import json
import re
import time
from datetime import datetime
from functools import lru_cache

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google import genai

app = FastAPI(title="NOVA", version="5.0.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "index.html")

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL", GEMINI_MODEL)

client = genai.Client(api_key=API_KEY) if API_KEY else None

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "NOVA-Assistant/5.0"})

MARKETS = {
    "CAC 40": "^FCHI",
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "Dow Jones": "^DJI",
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Apple": "AAPL",
    "Tesla": "TSLA",
    "NVIDIA": "NVDA",
    "Microsoft": "MSFT",
}


class ChatRequest(BaseModel):
    text: str = Field(default="", max_length=12000)
    fast: bool = False
    device: str = "web"
    memory_context: str = ""
    history: list[dict] = Field(default_factory=list)


class WeatherRequest(BaseModel):
    city: str = Field(default="Grenoble", max_length=100)


def clean_text(value, limit=500):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def parse_memory_context(raw):
    """Accepte le JSON de mémoire envoyé par l'interface."""
    if not raw:
        return {"souvenirs": [], "agenda": []}

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {"souvenirs": [], "agenda": []}

    if not isinstance(data, dict):
        return {"souvenirs": [], "agenda": []}

    memories = []
    for item in data.get("souvenirs", [])[:60]:
        if isinstance(item, str):
            text = clean_text(item, 300)
            category = "autre"
        elif isinstance(item, dict):
            text = clean_text(item.get("text", ""), 300)
            category = clean_text(item.get("category", "autre"), 30)
        else:
            continue

        if text:
            memories.append({"text": text, "category": category})

    agenda = []
    for item in data.get("agenda", [])[:60]:
        if not isinstance(item, dict):
            continue
        title = clean_text(item.get("title", ""), 120)
        if title:
            agenda.append({
                "title": title,
                "date": clean_text(item.get("date", ""), 50),
                "type": clean_text(item.get("type", "evenement"), 30),
                "note": clean_text(item.get("note", ""), 200),
            })

    return {"souvenirs": memories, "agenda": agenda}


def extract_json(text):
    if not text:
        return None

    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    return None


def normalize_history(history):
    result = []
    for item in history[-12:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = clean_text(item.get("content", ""), 1500)

        if role in ("user", "assistant") and content:
            result.append({"role": role, "content": content})

    return result


def ask_gemini(text, fast=False, memory_context="", history=None):
    if client is None:
        raise RuntimeError(
            "Clé Gemini manquante. Ajoute GEMINI_API_KEY dans les variables "
            "d'environnement du service Render."
        )

    memories = parse_memory_context(memory_context)
    recent_history = normalize_history(history or [])
    model = GEMINI_FAST_MODEL if fast else GEMINI_MODEL

    system_prompt = """
Tu es NOVA, un assistant personnel en français, professionnel, chaleureux,
clair et proactif. Adapte ton ton et tes explications à la conversation.
Ne prétends jamais avoir réalisé une action que tu n'as pas réellement faite.

Les données de mémoire et d'agenda sont des informations fournies par
l'application. Utilise-les seulement lorsqu'elles sont pertinentes. Ne
fabrique aucune information personnelle. Si tu ne sais pas, dis-le clairement.

Réponds STRICTEMENT avec un objet JSON valide ayant cette forme :
{
  "reply": "Ta réponse en français",
  "memory_updates": [
    {"text": "Information utile à retenir", "category": "preferences"}
  ]
}

memory_updates doit être une liste vide si rien ne mérite d'être proposé.
Propose seulement des informations personnelles utiles, explicitement données
par l'utilisateur et susceptibles d'être utiles plus tard : préférences,
projets, objectifs ou habitudes. Ne propose pas de mémoriser des mots de passe,
clés secrètes, données bancaires ou informations sensibles. Ne transforme
jamais une supposition en fait. La mémoire est proposée à l'utilisateur :
elle ne sera pas enregistrée sans confirmation.

Catégories autorisées : profil, preferences, projets, objectifs, autre.
Ne renvoie aucun texte hors du JSON.
""".strip()

    conversation = []
    for item in recent_history:
        role = "Utilisateur" if item["role"] == "user" else "NOVA"
        conversation.append(f"{role}: {item['content']}")

    prompt = f"""
{system_prompt}

Date et heure actuelles du serveur : {datetime.now().astimezone().isoformat()}

MEMOIRE DISPONIBLE :
{json.dumps(memories, ensure_ascii=False)}

AGENDA DISPONIBLE :
{json.dumps(memories["agenda"], ensure_ascii=False)}

HISTORIQUE RECENT :
{chr(10).join(conversation) if conversation else "(aucun)"}

NOUVEAU MESSAGE UTILISATEUR :
{text}
""".strip()

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "temperature": 0.65,
            "response_mime_type": "application/json",
        },
    )

    raw = getattr(response, "text", None) or ""
    parsed = extract_json(raw)

    if not parsed:
        # Repli si le modèle ne respecte pas le format JSON.
        return {"reply": raw.strip() or "Je n'ai pas réussi à formuler une réponse.", "memory_updates": []}

    reply = clean_text(parsed.get("reply", ""), 8000)
    updates = []
    allowed_categories = {
        "profil", "preferences", "projets", "objectifs", "autre"
    }

    raw_updates = parsed.get("memory_updates", [])
    if isinstance(raw_updates, list):
        for item in raw_updates[:5]:
            if not isinstance(item, dict):
                continue

            memory_text = clean_text(item.get("text", ""), 240)
            category = clean_text(item.get("category", "autre"), 30).lower()

            if not memory_text:
                continue
            if category not in allowed_categories:
                category = "autre"
            if re.search(
                r"(password|mot de passe|api[_ -]?key|clé secrète|"
                r"numéro de carte|code bancaire)",
                memory_text,
                re.I,
            ):
                continue

            updates.append({
                "text": memory_text,
                "category": category,
            })

    return {
        "reply": reply or "Je n'ai pas reçu de réponse exploitable.",
        "memory_updates": updates,
    }


@app.get("/")
@app.get("/index.html")
def index():
    if not os.path.isfile(INDEX_FILE):
        raise HTTPException(status_code=404, detail="index.html introuvable")
    return FileResponse(INDEX_FILE)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "NOVA",
        "version": "5.0.0",
        "gemini_configured": bool(API_KEY),
        "model": GEMINI_MODEL,
    }


@app.get("/api/status")
def status():
    return {
        "status": "online",
        "gemini": "configured" if client else "missing_api_key",
        "model": GEMINI_MODEL,
        "time": datetime.now().astimezone().isoformat(),
    }


@lru_cache(maxsize=100)
def geocode_city(city):
    response = HTTP.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "fr", "format": "json"},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None

    place = results[0]
    return {
        "name": place.get("name", city),
        "country": place.get("country", ""),
        "latitude": place["latitude"],
        "longitude": place["longitude"],
    }


@app.get("/api/weather")
def weather(city: str = "Grenoble"):
    city = clean_text(city, 100) or "Grenoble"

    try:
        place = geocode_city(city)
        if not place:
            raise HTTPException(status_code=404, detail="Ville introuvable")

        response = HTTP.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": (
                    "temperature_2m,relative_humidity_2m,"
                    "apparent_temperature,is_day,precipitation,weather_code,"
                    "wind_speed_10m"
                ),
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
            "country": place["country"],
            "current": data.get("current", {}),
            "daily": data.get("daily", {}),
            "timezone": data.get("timezone", ""),
        }
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="Le service météo est temporairement indisponible.",
        ) from exc


@app.get("/api/market")
def market(symbol: str = ""):
    symbol = clean_text(symbol, 40)
    if symbol and symbol not in MARKETS:
        raise HTTPException(status_code=400, detail="Symbole non autorisé")

    selected = {symbol: MARKETS[symbol]} if symbol else MARKETS
    results = []

    for name, ticker in selected.items():
        try:
            response = HTTP.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"range": "1d", "interval": "5m"},
                timeout=8,
            )
            response.raise_for_status()
            chart = response.json().get("chart", {})
            rows = chart.get("result") or []
            if not rows:
                continue

            meta = rows[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            previous = meta.get("chartPreviousClose") or meta.get("previousClose")
            change = (
                ((price - previous) / previous) * 100
                if price is not None and previous
                else None
            )
            results.append({
                "name": name,
                "symbol": ticker,
                "price": price,
                "currency": meta.get("currency", ""),
                "change_percent": change,
            })
        except (requests.RequestException, ValueError, TypeError):
            continue

    return {"markets": results}


@app.post("/api/chat")
def chat(payload: ChatRequest):
    text = clean_text(payload.text, 12000)
    if not text:
        raise HTTPException(status_code=400, detail="Message vide")

    # Réponse locale pour les salutations simples.
    normalized = text.lower().strip(" .!?")
    greetings = {
        "bonjour": "Bonjour ! Que puis-je faire pour toi ?",
        "salut": "Salut ! Je suis là. Qu'est-ce qu'on fait aujourd'hui ?",
        "coucou": "Coucou ! Comment puis-je t'aider ?",
        "merci": "Avec plaisir !",
    }

    if normalized in greetings:
        return {"reply": greetings[normalized], "memory_updates": []}

    try:
        result = ask_gemini(
            text=text,
            fast=payload.fast,
            memory_context=payload.memory_context,
            history=payload.history,
        )
        return result
    except Exception as exc:
        print(f"[NOVA/Gemini] {type(exc).__name__}: {exc}")
        if not API_KEY:
            detail = "La clé Gemini GEMINI_API_KEY n'est pas configurée sur Render."
            raise HTTPException(status_code=503, detail=detail) from exc
        raise HTTPException(
            status_code=502,
            detail="Gemini n'a pas pu répondre. Vérifie les logs Render.",
        ) from exc


@app.post("/api/tts")
def tts_unavailable():
    return {
        "ok": False,
        "message": "Utilise la synthèse vocale intégrée au navigateur.",
    }
