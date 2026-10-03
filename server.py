import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from apps import router as apps_router, try_open

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:
    PARIS = None


# ============================================================
# NOVA 4.3 — serveur
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()

# Google exige un délai minimum de 10 s.
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "15000"))
GEMINI_MAX_OUTPUT = int(os.getenv("GEMINI_MAX_OUTPUT", "600"))

app = FastAPI(title="NOVA", version="4.3.0")
app.include_router(apps_router)

gemini_client = None
if genai and GEMINI_API_KEY:
    try:
        retry_options = types.HttpRetryOptions(
            attempts=1,
            initial_delay=0.1,
            max_delay=0.2,
            jitter=0.0,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        )
        http_options = types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=retry_options,
        )
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=http_options,
        )
        print("✅ Gemini initialisé :", GEMINI_MODEL)
    except Exception as error:
        print("❌ Gemini non initialisé :", repr(error))


# ============================================================
# MÉMOIRE COURTE
# ============================================================

session_history: list[dict[str, str]] = []
MAX_HISTORY = 8


def remember(role: str, text: str) -> None:
    session_history.append({"role": role, "text": text[:1200]})
    if len(session_history) > MAX_HISTORY:
        del session_history[:-MAX_HISTORY]


def history_text() -> str:
    if not session_history:
        return ""
    lines = []
    for item in session_history[-6:]:
        speaker = "Utilisateur" if item["role"] == "user" else "NOVA"
        lines.append(f"{speaker}: {item['text']}")
    return "\n".join(lines)


# ============================================================
# RÉPONSES LOCALES — RAPIDES
# ============================================================

LOCAL_RESPONSES = {
    "salut": [
        "Salut ! Je suis là.",
        "Salut ! Prête à t'aider.",
        "Bonjour ! Qu'est-ce qu'on fait ?",
        "Hey ! NOVA est opérationnelle.",
        "Salut ! Je t'écoute.",
        "Bonjour ! Je suis prête.",
        "Salut ! On peut commencer.",
        "Hey ! Dis-moi ce que tu veux faire.",
        "Bonjour ! Que puis-je faire pour toi ?",
        "Salut ! Système NOVA opérationnel.",
    ],
    "merci": [
        "Avec plaisir.",
        "De rien !",
        "Avec plaisir, toujours.",
        "Pas de souci.",
        "Je t'en prie.",
        "C'est fait.",
        "Avec plaisir !",
        "Pas de problème.",
        "Tout est bon.",
        "Je suis là pour ça.",
    ],
    "humeur": [
        "Tout fonctionne correctement de mon côté.",
        "Je suis opérationnelle et prête à aider.",
        "Systèmes actifs. On peut continuer.",
        "Je fonctionne normalement.",
        "Tout est au vert.",
        "NOVA est prête.",
        "Je suis en forme numérique aujourd'hui.",
        "Mes systèmes répondent correctement.",
        "Tout semble stable.",
        "Prête pour la suite.",
    ],
    "probleme": [
        "Explique-moi le problème et on va le découper étape par étape.",
        "Dis-moi ce qui ne fonctionne pas.",
        "Je peux t'aider à diagnostiquer ça.",
        "On va chercher la cause.",
        "Décris-moi ce qui se passe.",
        "On va résoudre ça méthodiquement.",
        "Donne-moi le message d'erreur.",
        "Je t'écoute. Qu'est-ce qui bloque ?",
        "On peut vérifier chaque étape.",
        "Commence par me dire ce qui s'est passé.",
    ],
    "idee": [
        "Bonne idée. Développons-la.",
        "Intéressant. On peut en faire un vrai projet.",
        "Raconte-moi ton idée.",
        "Je peux t'aider à la structurer.",
        "On peut commencer par définir l'objectif.",
        "Décris-moi ce que tu imagines.",
        "Ça peut devenir intéressant.",
        "On va transformer l'idée en étapes concrètes.",
        "Commence par le résultat que tu veux obtenir.",
        "Je suis prête à explorer l'idée avec toi.",
    ],
    "projet": [
        "Parlons du projet. Quelle est la prochaine étape ?",
        "On peut organiser le projet étape par étape.",
        "Dis-moi où tu en es.",
        "Je peux t'aider à structurer la suite.",
        "Quel est le prochain objectif ?",
        "On peut découper le projet en petites tâches.",
        "Montre-moi ce qui fonctionne déjà.",
        "Décris-moi le résultat final recherché.",
        "On peut commencer par l'architecture.",
        "Je suis prête à travailler dessus.",
    ],
    "question": [
        "Oui, je t'écoute.",
        "Pose-moi ta question.",
        "Vas-y.",
        "Je t'écoute.",
        "Quelle est ta question ?",
        "Dis-moi ce que tu veux savoir.",
        "Je peux essayer de t'expliquer.",
        "Vas-y, je suis prête.",
        "Pose ta question.",
        "Je regarde ça avec toi.",
    ],
    "defaut": [
        "Je t'écoute.",
        "D'accord. Continue.",
        "Compris.",
        "Je suis là.",
        "Vas-y.",
        "Dis-m'en plus.",
        "Je te suis.",
        "Continue.",
        "D'accord.",
        "Je suis prête.",
    ],
}

_response_index: dict[str, int] = {key: 0 for key in LOCAL_RESPONSES}


def local_response(category: str) -> str:
    bank = LOCAL_RESPONSES.get(category, LOCAL_RESPONSES["defaut"])
    index = _response_index.get(category, 0)
    answer = bank[index % len(bank)]
    _response_index[category] = index + 1
    return answer


def local_conversation(text: str) -> str:
    t = text.lower().strip()

    if re.search(r"\b(salut|bonjour|bonsoir|hello|hey|coucou)\b", t):
        return local_response("salut")

    if re.search(r"\b(merci|thanks|thank you)\b", t):
        return local_response("merci")

    if re.search(r"\b(comment ça va|comment ca va|ça va|ca va|comment vas[- ]tu)\b", t):
        return local_response("humeur")

    if re.search(r"\b(problème|probleme|bug|erreur|ça marche pas|ca marche pas|bloqué|bloque)\b", t):
        return local_response("probleme")

    if re.search(r"\b(idée|idee|j'ai pensé|j ai pense|imagine)\b", t):
        return local_response("idee")

    if re.search(r"\b(projet|développer|developper|coder|programmation)\b", t):
        return local_response("projet")

    if t.endswith("?") or re.search(r"^(pourquoi|comment|qui|quoi|quel|quelle|où|ou|quand|combien)\b", t):
        return local_response("question")

    return local_response("defaut")


# ============================================================
# MÉTÉO
# ============================================================

WEATHER_CODES = {
    0: "Ciel dégagé",
    1: "Principalement dégagé",
    2: "Partiellement nuageux",
    3: "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine légère",
    53: "Bruine",
    55: "Bruine forte",
    61: "Pluie légère",
    63: "Pluie",
    65: "Pluie forte",
    71: "Neige légère",
    73: "Neige",
    75: "Neige forte",
    80: "Averses légères",
    81: "Averses",
    82: "Fortes averses",
    95: "Orage",
    96: "Orage avec grêle",
    99: "Orage avec forte grêle",
}

CITY_ALIASES = {
    "paris": "Paris",
    "grenoble": "Grenoble",
    "lyon": "Lyon",
    "marseille": "Marseille",
    "toulouse": "Toulouse",
    "bordeaux": "Bordeaux",
    "lille": "Lille",
    "nice": "Nice",
    "nantes": "Nantes",
    "strasbourg": "Strasbourg",
    "montpellier": "Montpellier",
    "rennes": "Rennes",
    "reims": "Reims",
    "dijon": "Dijon",
    "angers": "Angers",
    "tours": "Tours",
    "clermont": "Clermont-Ferrand",
    "clermont ferrand": "Clermont-Ferrand",
}


def extract_city(text: str) -> str:
    t = text.lower()
    for key in sorted(CITY_ALIASES, key=len, reverse=True):
        if key in t:
            return CITY_ALIASES[key]
    return "Grenoble"


def get_weather(city: str):
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "fr", "format": "json"},
            timeout=4,
        )
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            return None

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]

        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
            timeout=4,
        )
        weather.raise_for_status()
        current = weather.json().get("current", {})

        return {
            "city": place.get("name", city),
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind": current.get("wind_speed_10m"),
            "code": current.get("weather_code"),
            "description": WEATHER_CODES.get(current.get("weather_code"), "Conditions inconnues"),
        }
    except Exception as error:
        print("❌ Erreur météo:", repr(error))
        return None


# ============================================================
# MARCHÉS
# ============================================================

MARKETS = {
    "apple": "AAPL",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "google": "GOOGL",
    "meta": "META",
}


def get_market(symbol: str):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        response = requests.get(
            url,
            params={"range": "1d", "interval": "5m"},
            timeout=5,
            headers={"User-Agent": "NOVA/4.3"},
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        previous = meta.get("previousClose")

        change = None
        change_percent = None
        if price is not None and previous:
            change = price - previous
            change_percent = (change / previous) * 100

        return {
            "symbol": symbol,
            "price": price,
            "previous": previous,
            "change": change,
            "change_percent": change_percent,
            "currency": meta.get("currency", "USD"),
        }
    except Exception as error:
        print("❌ Erreur marché:", repr(error))
        return None


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(text: str, fast: bool = False) -> str | None:
    if gemini_client is None:
        print("⚠️ Gemini indisponible : client non initialisé")
        return None

    hist = history_text()
    if len(hist) > 3500:
        hist = hist[-3500:]

    style = (
        "Réponds très brièvement, naturellement et directement. "
        "Maximum 3 phrases, sauf si l'utilisateur demande une explication détaillée."
        if fast
        else
        "Réponds naturellement, clairement et directement. "
        "Évite les longues introductions et reste concis."
    )

    prompt = f"""
Tu es NOVA, une assistante IA française intégrée dans une application personnelle.
{style}
Tu peux être chaleureuse et naturelle, mais ne prétends jamais avoir effectué une action que le serveur n'a pas réellement effectuée.
Réponds en français sauf si l'utilisateur demande une autre langue.

Conversation récente :
{hist}

Nouvelle demande de l'utilisateur :
{text}
""".strip()

    started = time.perf_counter()

    try:
        print(f"🧠 Gemini → {GEMINI_MODEL} | timeout={GEMINI_TIMEOUT_MS}ms")

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.55,
                max_output_tokens=GEMINI_MAX_OUTPUT,
                candidate_count=1,
            ),
        )

        answer = (response.text or "").strip()
        elapsed = time.perf_counter() - started
        print(f"✅ Gemini OK en {elapsed:.2f}s")

        if not answer:
            print("⚠️ Gemini a renvoyé une réponse vide")
            return None

        return answer[:3500]

    except Exception as error:
        elapsed = time.perf_counter() - started
        print(f"❌ Gemini erreur après {elapsed:.2f}s")
        print(f"   Type : {type(error).__name__}")
        print(f"   Message : {error!s}")
        return None


# ============================================================
# REQUÊTES
# ============================================================

class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    fast: bool = False
    device: str = "iphone"


def wants_weather(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(météo|meteo|temps|température|temperature)\b", t))


def wants_market(text: str) -> bool:
    t = text.lower()
    return bool(
        re.search(
            r"\b(bourse|action|actions|marché|marche|cours|nvidia|apple|tesla|microsoft|amazon|google|meta)\b",
            t,
        )
    )


def wants_time(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(quelle heure|il est quelle heure|heure actuelle|heure)\b", t))


@app.get("/")
def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"service": "NOVA", "version": "4.3.0"})


@app.get("/index.html")
def index_html():
    return root()


@app.get("/manifest.webmanifest")
def manifest():
    path = BASE_DIR / "manifest.webmanifest"
    if path.exists():
        return FileResponse(path, media_type="application/manifest+json")
    return JSONResponse({"name": "NOVA"})


@app.get("/sw.js")
def service_worker():
    path = BASE_DIR / "sw.js"
    if path.exists():
        return FileResponse(path, media_type="application/javascript")
    return JSONResponse({"error": "sw.js absent"}, status_code=404)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "NOVA",
        "version": "4.3.0",
        "gemini_configured": gemini_client is not None,
        "gemini_model": GEMINI_MODEL,
        "gemini_timeout_ms": GEMINI_TIMEOUT_MS,
        "gemini_max_output_tokens": GEMINI_MAX_OUTPUT,
    }


@app.get("/api/status")
def status():
    return {
        "status": "online",
        "service": "NOVA",
        "version": "4.3.0",
        "gemini": gemini_client is not None,
        "model": GEMINI_MODEL,
    }


@app.get("/api/weather")
def weather_endpoint(city: str = "Grenoble"):
    data = get_weather(city)
    if not data:
        return JSONResponse({"error": "Météo indisponible"}, status_code=503)
    return data


@app.get("/api/market")
def market_endpoint(symbol: str = "AAPL"):
    data = get_market(symbol.upper())
    if not data:
        return JSONResponse({"error": "Marché indisponible"}, status_code=503)
    return data


@app.post("/api/chat")
def chat(payload: ChatRequest):
    text = payload.text.strip()

    # 1. Ouverture d'applis (téléphone ou PC) : zéro appel Gemini.
    opened = try_open(text, payload.device)
    if opened:
        remember("user", text)
        remember("assistant", opened["reply"])
        return opened

    # 2. Météo : API directe.
    if wants_weather(text):
        city = extract_city(text)
        data = get_weather(city)
        if data:
            reply = (
                f"À {data['city']}, il fait {data['temperature']} °C. "
                f"{data['description']}, humidité {data['humidity']} %."
            )
            remember("user", text)
            remember("assistant", reply)
            return {"reply": reply, "action": {"type": "weather", "data": data}}

    # 3. Marchés : API directe.
    if wants_market(text):
        symbol = None
        lowered = text.lower()
        for name, ticker in MARKETS.items():
            if name in lowered or ticker.lower() in lowered:
                symbol = ticker
                break
        symbol = symbol or "AAPL"

        data = get_market(symbol)
        if data and data.get("price") is not None:
            pct = data.get("change_percent")
            change_text = f"{pct:+.2f} %" if pct is not None else "variation indisponible"
            reply = f"{symbol} est à {data['price']} {data['currency']} ({change_text})."
            remember("user", text)
            remember("assistant", reply)
            return {"reply": reply, "action": {"type": "market", "data": data}}

    # 4. Heure (heure de Paris) : zéro appel Gemini.
    if wants_time(text):
        now = datetime.now(PARIS) if PARIS else datetime.now()
        reply = f"Il est {now.strftime('%H:%M')}."
        remember("user", text)
        remember("assistant", reply)
        return {"reply": reply}

    # 5. Conversation locale ultra-rapide pour les phrases simples.
    simple_patterns = [
        r"^(salut|bonjour|bonsoir|hello|hey|coucou)[!. ]*$",
        r"^(merci|merci beaucoup)[!. ]*$",
        r"^(ça va|ca va|comment ça va|comment ca va)[?! .]*$",
        r"^(ok|okay|d'accord|daccord)[!. ]*$",
    ]
    if any(re.match(pattern, text.lower()) for pattern in simple_patterns):
        reply = local_conversation(text)
        remember("user", text)
        remember("assistant", reply)
        return {"reply": reply}

    # 6. Mode rapide : moteur local en priorité pour éviter d'attendre Gemini.
    if payload.fast:
        reply = local_conversation(text)
        remember("user", text)
        remember("assistant", reply)
        return {
            "reply": reply,
            "fast_local": True,
            "note": "Mode rapide : aucune requête Gemini pour cette réponse.",
        }

    # 7. Gemini.
    remember("user", text)
    reply = ask_gemini(text, payload.fast)

    # 8. Si Gemini échoue, on le dit clairement.
    if not reply:
        remember("assistant", "Gemini indisponible")
        return {
            "reply": "Mon cerveau Gemini est indisponible pour le moment (quota atteint ou erreur). Réessaie plus tard.",
            "fallback": True,
            "gemini_error": True,
        }

    remember("assistant", reply)
    return {"reply": reply, "gemini": True}


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
