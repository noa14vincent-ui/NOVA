# -*- coding: utf-8 -*-
"""
NOVA backend
Gemini + commandes + météo + bourse + mémoire de conversation
"""

from __future__ import annotations

import difflib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()

app = FastAPI(
    title="NOVA",
    version="4.1.0"
)


# ============================================================
# GEMINI
# ============================================================

gemini_client = None

if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )

        print("========================================")
        print("NOVA - GEMINI")
        print("Clé détectée : OUI")
        print(f"Modèle : {GEMINI_MODEL}")
        print("Client Gemini : OK")
        print("========================================")

    except Exception as error:
        print("========================================")
        print("ERREUR INITIALISATION GEMINI")
        print(repr(error))
        print("========================================")
        gemini_client = None

else:
    print("========================================")
    print("ATTENTION : GEMINI_API_KEY ABSENTE")
    print("========================================")


# ============================================================
# MÉMOIRE
# ============================================================

session_history: list[dict[str, str]] = []


def remember(role: str, text: str) -> None:
    session_history.append({
        "role": role,
        "text": text
    })

    # Garde seulement les 30 derniers messages
    del session_history[:-30]


# ============================================================
# APPLICATIONS / SITES
# ============================================================

APP_COMMANDS = {

    "netflix": (
        ["netflix"],
        "https://www.netflix.com/",
        "Netflix"
    ),

    "youtube": (
        ["youtube", "youtube music"],
        "https://www.youtube.com/",
        "YouTube"
    ),

    "spotify": (
        ["spotify"],
        "https://open.spotify.com/",
        "Spotify"
    ),

    "discord": (
        ["discord"],
        "https://discord.com/app",
        "Discord"
    ),

    "instagram": (
        ["instagram", "insta"],
        "https://www.instagram.com/",
        "Instagram"
    ),

    "tiktok": (
        ["tiktok", "tik tok"],
        "https://www.tiktok.com/",
        "TikTok"
    ),

    "chatgpt": (
        ["chatgpt", "chat gpt"],
        "https://chatgpt.com/",
        "ChatGPT"
    ),

    "gmail": (
        ["gmail", "mail"],
        "https://mail.google.com/",
        "Gmail"
    ),

    "github": (
        ["github", "git hub"],
        "https://github.com/",
        "GitHub"
    ),

    "roblox": (
        ["roblox"],
        "https://www.roblox.com/",
        "Roblox"
    ),

    "minecraft": (
        ["minecraft"],
        "https://www.minecraft.net/",
        "Minecraft"
    ),

    "steam": (
        ["steam"],
        "https://store.steampowered.com/",
        "Steam"
    ),

    "twitch": (
        ["twitch"],
        "https://www.twitch.tv/",
        "Twitch"
    ),

    "google maps": (
        ["google maps", "maps"],
        "https://www.google.com/maps/",
        "Google Maps"
    ),

    "wikipedia": (
        ["wikipedia", "wiki"],
        "https://www.wikipedia.org/",
        "Wikipedia"
    ),

    "reddit": (
        ["reddit"],
        "https://www.reddit.com/",
        "Reddit"
    ),
}


OPEN_WORDS = (
    "ouvre",
    "ouvrir",
    "lance",
    "lancer",
    "démarre",
    "demarre",
    "démarrer",
    "demarrer",
    "va sur",
    "aller sur",
    "accède",
    "accede",
    "affiche",
)


# ============================================================
# MARCHÉS
# ============================================================

MARKETS = {
    "cac 40": ("^FCHI", "CAC 40"),
    "cac40": ("^FCHI", "CAC 40"),
    "nasdaq": ("^IXIC", "Nasdaq"),
    "dow": ("^DJI", "Dow Jones"),
    "dow jones": ("^DJI", "Dow Jones"),
    "sp500": ("^GSPC", "S&P 500"),
    "bitcoin": ("BTC-USD", "Bitcoin"),
    "apple": ("AAPL", "Apple"),
    "tesla": ("TSLA", "Tesla"),
    "amazon": ("AMZN", "Amazon"),
    "microsoft": ("MSFT", "Microsoft"),
    "nvidia": ("NVDA", "NVIDIA"),
    "meta": ("META", "Meta"),
    "lvmh": ("MC.PA", "LVMH"),
    "totalenergies": ("TTE.PA", "TotalEnergies"),
}


# ============================================================
# VILLES
# ============================================================

CITIES = {
    "grenoble": "Grenoble",
    "paris": "Paris",
    "lyon": "Lyon",
    "marseille": "Marseille",
    "toulouse": "Toulouse",
    "nice": "Nice",
    "bordeaux": "Bordeaux",
    "lille": "Lille",
    "strasbourg": "Strasbourg",
}


# ============================================================
# MÉTÉO
# ============================================================

WEATHER = {
    0: "Ciel dégagé",
    1: "Peu nuageux",
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
    99: "Orage fort",
}


# ============================================================
# MODÈLES API
# ============================================================

class ChatRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=4000
    )

    device: str = Field(
        default="iphone",
        max_length=30
    )

    fast: bool = False


class LocationRequest(BaseModel):
    city: str = Field(
        default="Grenoble",
        min_length=1,
        max_length=60
    )


class MarketRequest(BaseModel):
    market: str = Field(
        default="CAC 40",
        min_length=1,
        max_length=60
    )


# ============================================================
# OUTILS
# ============================================================

def clean(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def get(
    url: str,
    **kwargs: Any
) -> dict[str, Any]:

    response = requests.get(
        url,
        timeout=10,
        headers={
            "User-Agent": "NOVA/4.1"
        },
        **kwargs
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# VILLE
# ============================================================

def city_from(text: str) -> str:

    low = text.lower()

    for alias, city in CITIES.items():

        if re.search(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            low
        ):
            return city

    match = re.search(
        r"(?:à|a|sur|de)\s+([A-Za-zÀ-ÿ' -]{2,40})",
        text,
        re.I
    )

    if match:
        return match.group(1).strip(
            " .,!?;"
        ).title()

    return "Grenoble"


# ============================================================
# MÉTÉO
# ============================================================

def weather(city: str) -> dict[str, Any]:

    geocoding = get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": city,
            "count": 1,
            "language": "fr",
            "format": "json",
        }
    )

    results = geocoding.get("results") or []

    if not results:
        raise ValueError(
            f"Ville introuvable : {city}"
        )

    place = results[0]

    forecast = get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": (
                "temperature_2m,"
                "apparent_temperature,"
                "weather_code,"
                "wind_speed_10m,"
                "relative_humidity_2m"
            ),
            "timezone": "auto",
        }
    )

    current = forecast.get(
        "current",
        {}
    )

    return {
        "city": place.get(
            "name",
            city
        ),
        "temperature": current.get(
            "temperature_2m"
        ),
        "feels_like": current.get(
            "apparent_temperature"
        ),
        "humidity": current.get(
            "relative_humidity_2m"
        ),
        "wind": current.get(
            "wind_speed_10m"
        ),
        "condition": WEATHER.get(
            current.get("weather_code"),
            "Conditions inconnues"
        ),
        "source": "Open-Meteo",
    }


# ============================================================
# BOURSE
# ============================================================

def market(name: str) -> dict[str, Any]:

    key = name.lower().strip()

    if key not in MARKETS:

        found = difflib.get_close_matches(
            key,
            list(MARKETS),
            n=1,
            cutoff=0.6
        )

        if not found:
            raise ValueError(
                "Valeur introuvable"
            )

        key = found[0]

    symbol, label = MARKETS[key]

    data = get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",
        params={
            "range": "1d",
            "interval": "5m"
        }
    )

    result = (
        data
        .get("chart", {})
        .get("result")
        or [None]
    )[0]

    if not result:
        raise ValueError(
            "Donnée indisponible"
        )

    meta = result.get(
        "meta",
        {}
    )

    price = meta.get(
        "regularMarketPrice"
    )

    previous = meta.get(
        "previousClose"
    )

    percentage = None

    if (
        isinstance(price, (int, float))
        and isinstance(previous, (int, float))
        and previous
    ):
        percentage = (
            (price - previous)
            / previous
            * 100
        )

    return {
        "symbol": symbol,
        "label": label,
        "price": price,
        "currency": meta.get(
            "currency",
            ""
        ),
        "previous_close": previous,
        "change_pct": percentage,
        "source": "Yahoo Finance",
    }


# ============================================================
# OUVERTURE APPLICATION / SITE
# ============================================================

def open_action(
    text: str,
    device: str
) -> dict[str, Any] | None:

    low = text.lower()

    if not any(
        word in low
        for word in OPEN_WORDS
    ):
        return None

    sorted_apps = sorted(
        APP_COMMANDS.items(),
        key=lambda item: max(
            map(
                len,
                item[1][0]
            )
        ),
        reverse=True
    )

    for _, (
        aliases,
        url,
        label
    ) in sorted_apps:

        found = any(
            re.search(
                rf"(?<!\w){re.escape(alias)}(?!\w)",
                low
            )
            for alias in aliases
        )

        if found:

            return {
                "type": "open_url",
                "url": url,
                "device": device,
                "label": label,
                "app_first": True,
                "fallback_url": url,
            }

    match = re.search(
        r"https?://[^\s]+",
        text,
        re.I
    )

    if match:

        url = match.group(0).rstrip(
            ".,!?;)"
        )

        if urlparse(url).scheme in {
            "http",
            "https"
        }:

            return {
                "type": "open_url",
                "url": url,
                "device": device,
                "label": "Lien",
                "app_first": True,
                "fallback_url": url,
            }

    return None


# ============================================================
# GEMINI - CERVEAU DE NOVA
# ============================================================

def ask_gemini(
    text: str,
    fast: bool
) -> str | None:

    if gemini_client is None:

        print(
            "❌ GEMINI : client non initialisé"
        )

        return None

    history_text = "\n".join(
        f"{item['role']}: {item['text']}"
        for item in session_history[-10:]
    )

    mode = (
        "Réponds en deux phrases maximum."
        if fast
        else
        "Réponds clairement et naturellement. "
        "Utilise des étapes lorsque c'est utile. "
        "Pour le code, donne du code directement utilisable."
    )

    system_instruction = """
Tu es NOVA, une assistante IA française.

Tu es :
- naturelle
- chaleureuse
- claire
- intelligente
- concise quand la question est simple
- détaillée quand la question le nécessite

Tu aides particulièrement pour :
- Python
- C#
- Unity
- développement de jeux
- intelligence artificielle
- programmation
- création de projets

Règles importantes :
- Réponds toujours en français.
- Ne prétends jamais avoir effectué une action que NOVA n'a pas réellement effectuée.
- Ne prétends pas contrôler un appareil si le serveur ne l'a pas réellement fait.
- Si tu ne sais pas quelque chose, dis-le clairement.
- Ne fabrique pas de données présentées comme réelles.
"""

    prompt = f"""
{system_instruction}

Mode :
{mode}

Historique récent :
{history_text}

Message de l'utilisateur :
{text}
"""

    try:

        print(
            "🧠 Envoi de la demande à Gemini..."
        )

        print(
            f"🧠 Modèle utilisé : {GEMINI_MODEL}"
        )

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        print(
            "✅ Réponse Gemini reçue."
        )

        try:
            answer = response.text
        except Exception as error:
            print(
                "⚠️ Impossible de lire response.text :",
                repr(error)
            )
            answer = ""

        answer = (
            answer or ""
        ).strip()

        if not answer:

            print(
                "⚠️ Gemini a répondu sans texte."
            )

            print(
                "Réponse Gemini complète :",
                repr(response)
            )

            return None

        print(
            f"📝 Réponse Gemini : {answer[:200]}"
        )

        return answer[:4000]

    except Exception as error:

        print(
            "========================================"
        )

        print(
            "❌ ERREUR GEMINI"
        )

        print(
            "Type :",
            type(error).__name__
        )

        print(
            "Erreur :",
            str(error)
        )

        print(
            "Représentation :",
            repr(error)
        )

        print(
            "========================================"
        )

        return None


# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.get("/")
def home():

    return FileResponse(
        BASE_DIR / "index.html"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
def health():

    return {
        "status": "ok",
        "service": "NOVA",
        "version": app.version,
        "gemini_configured": (
            gemini_client is not None
        ),
        "gemini_model": GEMINI_MODEL,
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def status():

    return {
        "online": True,
        "history": session_history[-20:],
        "time": datetime.now().isoformat(
            timespec="seconds"
        ),
        "gemini_configured": (
            gemini_client is not None
        ),
        "gemini_model": GEMINI_MODEL,
    }


# ============================================================
# CHAT PRINCIPAL
# ============================================================

@app.post("/api/chat")
def chat(
    payload: ChatRequest
) -> JSONResponse:

    text = clean(
        payload.text
    )

    remember(
        "user",
        text
    )

    low = text.lower()

    action = None

    try:

        # ----------------------------------------------------
        # MÉTÉO
        # ----------------------------------------------------

        if re.search(
            r"\b(météo|meteo|temps|prévisions|previsions)\b",
            low
        ):

            city = city_from(text)

            data = weather(city)

            action = {
                "type": "weather",
                "data": data
            }

            reply = (
                f"Météo {data['city']} : "
                f"{data['temperature']} °C, "
                f"{data['condition']}. "
                f"Ressenti {data['feels_like']} °C, "
                f"humidité {data['humidity']} %."
            )

        # ----------------------------------------------------
        # BOURSE
        # ----------------------------------------------------

        elif re.search(
            r"\b("
            r"bourse|marché|marche|"
            r"cac 40|cac40|nasdaq|"
            r"dow jones|sp500|bitcoin|actions"
            r")\b",
            low
        ):

            name = next(
                (
                    market_name
                    for market_name in MARKETS
                    if market_name in low
                ),
                "cac 40"
            )

            data = market(name)

            action = {
                "type": "market",
                "data": data
            }

            if isinstance(
                data["change_pct"],
                (int, float)
            ):

                variation = (
                    f", variation "
                    f"{data['change_pct']:+.2f} %"
                )

            else:
                variation = ""

            reply = (
                f"{data['label']} : "
                f"{data['price']} "
                f"{data['currency']}"
                f"{variation}."
            )

        # ----------------------------------------------------
        # COMMANDES D'OUVERTURE
        # ----------------------------------------------------

        else:

            action = open_action(
                text,
                payload.device
            )

            if action:

                reply = (
                    f"J'ouvre "
                    f"{action['label']}."
                )

            # ------------------------------------------------
            # HEURE
            # ------------------------------------------------

            elif re.search(
                r"\b(heure|quelle heure|"
                r"il est quelle heure)\b",
                low
            ):

                reply = (
                    "Il est "
                    + datetime.now().strftime(
                        "%H:%M"
                    )
                    + "."
                )

            # ------------------------------------------------
            # MODE RAPIDE
            # ------------------------------------------------

            elif "mode rapide" in low:

                reply = (
                    "Le mode rapide est "
                    "géré directement par "
                    "l'interface de NOVA."
                )

            # ------------------------------------------------
            # GEMINI
            # ------------------------------------------------

            else:

                reply = ask_gemini(
                    text,
                    payload.fast
                )

                if not reply:

                    reply = (
                        "Je rencontre actuellement "
                        "un problème avec mon moteur "
                        "Gemini. Consulte les logs "
                        "du serveur NOVA pour voir "
                        "l'erreur exacte."
                    )

    except Exception as error:

        print(
            "========================================"
        )

        print(
            "❌ ERREUR NOVA"
        )

        print(
            "Type :",
            type(error).__name__
        )

        print(
            "Erreur :",
            str(error)
        )

        print(
            "Représentation :",
            repr(error)
        )

        print(
            "========================================"
        )

        reply = (
            "Je n'arrive pas à récupérer "
            "cette information pour le moment."
        )

    remember(
        "nova",
        reply
    )

    return JSONResponse({
        "text": reply,
        "action": action
    })


# ============================================================
# API MÉTÉO
# ============================================================

@app.post("/api/weather")
def weather_api(
    payload: LocationRequest
):

    try:

        return weather(
            payload.city
        )

    except Exception as error:

        raise HTTPException(
            status_code=502,
            detail=(
                f"Météo indisponible : "
                f"{error}"
            )
        )


# ============================================================
# API MARCHÉ
# ============================================================

@app.post("/api/market")
def market_api(
    payload: MarketRequest
):

    try:

        return market(
            payload.market
        )

    except Exception as error:

        raise HTTPException(
            status_code=502,
            detail=(
                f"Marché indisponible : "
                f"{error}"
            )
        )


# ============================================================
# PWA
# ============================================================

@app.get("/manifest.webmanifest")
def manifest():

    return FileResponse(
        BASE_DIR / "manifest.webmanifest",
        media_type="application/manifest+json"
    )


@app.get("/sw.js")
def service_worker():

    return FileResponse(
        BASE_DIR / "sw.js",
        media_type="application/javascript"
    )
