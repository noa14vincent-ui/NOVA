# -*- coding: utf-8 -*-
"""
NOVA — backend iPhone / PWA
Interface 2D futuriste, pensée pour mobile.
- Conversation locale (sans clé API)
- Météo Open-Meteo
- Marchés Yahoo Finance
- Ouverture de sites sur l'iPhone
- API FastAPI compatible Render 
"""

from __future__ import annotations

import difflib
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Palette identique à l'interface PC NOVA existante.
BG = "#030712"
BLUE = "#38bdf8"
CYAN = "#67e8f9"
VIOLET = "#a78bfa"
WHITE = "#e8f5ff"
MUTED = "#66809a"
PANEL = "#07111f"
LINE = "#10253a"

app = FastAPI(title="NOVA", version="2.1.0")

session_history: list[dict[str, str]] = []

RESPONSES = {
    "salut": [
        "Salut. NOVA est opérationnelle. Qu'est-ce qu'on fait ?",
        "Salut. Je suis prête.",
        "Bonjour. Dis-moi ce que tu veux faire.",
        "Présente. On continue ?",
        "Salut. Je t'écoute.",
    ],
    "merci": [
        "Avec plaisir.",
        "Pas de souci.",
        "Toujours disponible.",
        "Bien reçu.",
    ],
    "humeur": [
        "Je t'écoute. Qu'est-ce qui te met dans cet état ?",
        "D'accord. Raconte-moi ce qui se passe.",
        "Je suis là. Qu'est-ce que tu as en tête ?",
        "On peut prendre ça étape par étape.",
    ],
    "idee": [
        "Intéressant. C'est quoi l'idée de départ ?",
        "Décris-moi ton idée comme tu la vois.",
        "On peut la transformer en plan concret. Par quoi veux-tu commencer ?",
        "Je note l'idée. Qu'est-ce qu'elle doit permettre de faire ?",
    ],
    "projet": [
        "D'accord. Quel est l'objectif principal du projet ?",
        "On peut le découper en petites étapes. Tu veux commencer par laquelle ?",
        "Parle-moi de l'état actuel du projet.",
        "Très bien. Quelle est la prochaine étape que tu veux réaliser ?",
    ],
    "probleme": [
        "On va isoler le problème. Qu'est-ce qui ne fonctionne pas exactement ?",
        "Décris-moi le blocage et ce que tu as déjà essayé.",
        "On peut chercher la cause ensemble. Qu'est-ce que tu observes ?",
        "Commence par me donner le message d'erreur ou le comportement gênant.",
    ],
    "question": [
        "Oui. Pose ta question.",
        "Vas-y, je t'écoute.",
        "D'accord. Quelle est ta question ?",
    ],
    "defaut": [
        "Je t'écoute.",
        "Continue.",
        "D'accord. Développe un peu.",
        "Je suis là. Donne-moi plus de contexte.",
        "Compris. Qu'est-ce que tu veux faire ensuite ?",
        "On peut avancer étape par étape.",
        "Je note. Continue.",
        "D'accord. Et ensuite ?",
    ],
}

last_intent = "defaut"
last_subject = ""

SITES = {
    "netflix": "https://www.netflix.com/",
    "youtube": "https://www.youtube.com/",
    "spotify": "https://open.spotify.com/",
    "google": "https://www.google.com/",
    "github": "https://github.com/",
    "discord": "https://discord.com/app",
    "instagram": "https://www.instagram.com/",
    "chatgpt": "https://chatgpt.com/",
    "gmail": "https://mail.google.com/",
    "tiktok": "https://www.tiktok.com/",
}

MARKETS = {
    "cac 40": {"symbol": "^FCHI", "label": "CAC 40"},
    "cac40": {"symbol": "^FCHI", "label": "CAC 40"},
    "dow": {"symbol": "^DJI", "label": "Dow Jones"},
    "dow jones": {"symbol": "^DJI", "label": "Dow Jones"},
    "nasdaq": {"symbol": "^IXIC", "label": "Nasdaq"},
    "sp500": {"symbol": "^GSPC", "label": "S&P 500"},
    "s&p 500": {"symbol": "^GSPC", "label": "S&P 500"},
    "bitcoin": {"symbol": "BTC-USD", "label": "Bitcoin"},
    "apple": {"symbol": "AAPL", "label": "Apple"},
    "tesla": {"symbol": "TSLA", "label": "Tesla"},
    "amazon": {"symbol": "AMZN", "label": "Amazon"},
    "microsoft": {"symbol": "MSFT", "label": "Microsoft"},
    "nvidia": {"symbol": "NVDA", "label": "NVIDIA"},
    "meta": {"symbol": "META", "label": "Meta"},
    "netflix action": {"symbol": "NFLX", "label": "Netflix"},
    "lvmh": {"symbol": "MC.PA", "label": "LVMH"},
    "totalenergies": {"symbol": "TTE.PA", "label": "TotalEnergies"},
}

WEATHER_CODES = {
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

CITY_ALIASES = {
    "grenoble": "Grenoble",
    "paris": "Paris",
    "lyon": "Lyon",
    "marseille": "Marseille",
    "toulouse": "Toulouse",
    "nice": "Nice",
    "bordeaux": "Bordeaux",
    "lille": "Lille",
    "strasbourg": "Strasbourg",
    "belleville": "Belleville",
}


def add_history(role: str, text: str) -> None:
    session_history.append({"role": role, "text": text})
    if len(session_history) > 40:
        del session_history[:-40]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def detect_intent(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(salut|bonjour|hello|yo|coucou)\b", t):
        return "salut"
    if re.search(r"\b(merci|thanks)\b", t):
        return "merci"
    if re.search(r"\b(idée|idee|j'ai pensé|j ai pense|imagine|concept)\b", t):
        return "idee"
    if re.search(r"\b(projet|développer|developper|programme|application|app)\b", t):
        return "projet"
    if re.search(r"\b(problème|probleme|bug|erreur|bloqué|bloque|marche pas|ça ne marche)\b", t):
        return "probleme"
    if re.search(r"\b(comment|pourquoi|quelle|quel|qu'est-ce|question|est-ce que)\b", t):
        return "question"
    if re.search(r"\b(ça va|ca va|stress|triste|content|fatigué|fatigue|énervé|enerve|mal|bien)\b", t):
        return "humeur"
    return "defaut"


def subject_from_text(text: str) -> str:
    t = clean_text(text)
    t = re.sub(r"^(j'ai|j ai|je veux|je voudrais|mon|ma|mes|c'est|c est)\s+", "", t, flags=re.I)
    words = t.split()
    return " ".join(words[:9]).strip(" ,.!?")


def conversation_reply(text: str) -> str:
    global last_intent, last_subject
    intent = detect_intent(text)
    subject = subject_from_text(text)

    if re.fullmatch(r"(oui|ouais|yes|d'accord|ok|okay)\W*", text.lower()):
        reply = {
            "projet": "Parfait. Donne-moi la prochaine étape et on la découpe ensemble.",
            "idee": "Parfait. On peut maintenant préciser le fonctionnement.",
            "probleme": "Très bien. Donne-moi le détail qui bloque encore.",
            "question": "D'accord. On continue sur ce point.",
            "humeur": "D'accord. Raconte-moi la suite.",
        }.get(last_intent, "D'accord. Continue.")
    elif re.fullmatch(r"(non|nan|pas vraiment)\W*", text.lower()):
        reply = "D'accord. On change d'angle. Qu'est-ce que tu préfères faire ?"
    elif re.search(r"\b(et après|ensuite)\b", text.lower()):
        reply = "Ensuite, on valide le résultat puis on passe à l'étape suivante."
    elif re.search(r"\b(pourquoi)\b", text.lower()) and last_subject:
        reply = f"Pour le point « {last_subject} », le plus simple est de regarder d'abord la cause puis l'effet."
    elif re.search(r"\b(comment)\b", text.lower()) and last_subject:
        reply = f"Pour « {last_subject} », on peut procéder étape par étape. Donne-moi ce que tu as déjà."
    else:
        pool = RESPONSES.get(intent, RESPONSES["defaut"])
        index = sum(len(x["text"]) for x in session_history[-8:]) % len(pool)
        reply = pool[index]

    last_intent = intent
    last_subject = subject or last_subject
    return reply


def url_action(text: str) -> str | None:
    t = text.lower()
    for name, url in SITES.items():
        if re.search(rf"\b{re.escape(name)}\b", t):
            if any(k in t for k in ("ouvre", "ouvrir", "lance", "aller", "va sur")):
                return url
    m = re.search(r"https?://[^\s]+", text, flags=re.I)
    if m:
        url = m.group(0).rstrip(".,!?;)")
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            return url
    if re.search(r"\b(cherche|recherche)\b", t):
        q = re.sub(r".*?\b(cherche|recherche)\b", "", text, count=1, flags=re.I).strip()
        if q:
            return "https://www.google.com/search?q=" + quote_plus(q)
    return None


def wants_weather(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(météo|meteo|temps|prévisions|previsions)\b", t))


def wants_market(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(bourse|marché|marche|cac 40|cac40|nasdaq|dow jones|sp500|s&p 500|bitcoin|actions)\b", t))


def find_city(text: str) -> str:
    t = text.lower()
    for key, value in CITY_ALIASES.items():
        if key in t:
            return value
    m = re.search(r"(?:à|a|sur|de)\s+([A-Za-zÀ-ÿ' -]{2,40})", text, re.I)
    if m:
        candidate = m.group(1).strip(" .,!?;")
        candidate = re.sub(r"\b(la|le|les|au|aux|dans)\b", "", candidate, flags=re.I).strip()
        if candidate:
            return candidate.title()
    return "Grenoble"


def fetch_weather(city: str) -> dict[str, Any]:
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "fr", "format": "json"},
        timeout=8,
    )
    geo.raise_for_status()
    results = geo.json().get("results") or []
    if not results:
        raise ValueError("Ville introuvable")
    place = results[0]
    lat = place["latitude"]
    lon = place["longitude"]
    name = place.get("name", city)
    weather = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "timezone": "auto",
        },
        timeout=8,
    )
    weather.raise_for_status()
    current = weather.json().get("current", {})
    return {
        "city": name,
        "temperature": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "code": current.get("weather_code"),
        "condition": WEATHER_CODES.get(current.get("weather_code"), "Conditions inconnues"),
        "time": current.get("time"),
        "source": "Open-Meteo",
    }


def fetch_market(label: str) -> dict[str, Any]:
    key = label.lower().strip()
    info = MARKETS.get(key)
    if info is None:
        # recherche tolérante
        best = difflib.get_close_matches(key, list(MARKETS.keys()), n=1, cutoff=0.65)
        if not best:
            raise ValueError("Valeur introuvable")
        info = MARKETS[best[0]]
    symbol = info["symbol"]
    data = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",
        params={"range": "1d", "interval": "5m"},
        headers={"User-Agent": "NOVA/2.1"},
        timeout=8,
    )
    data.raise_for_status()
    result = (data.json().get("chart", {}).get("result") or [None])[0]
    if not result:
        raise ValueError("Donnée de marché indisponible")
    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("previousClose")
    change = None
    change_pct = None
    if isinstance(price, (int, float)) and isinstance(previous, (int, float)) and previous:
        change = price - previous
        change_pct = change / previous * 100
    return {
        "symbol": symbol,
        "label": info["label"],
        "price": price,
        "currency": meta.get("currency", ""),
        "previous_close": previous,
        "change": change,
        "change_pct": change_pct,
        "time": meta.get("regularMarketTime"),
        "source": "Yahoo Finance",
        "note": "Les cotations peuvent avoir un léger décalage selon la place de marché.",
    }


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    device: str = Field(default="iphone", max_length=30)
    fast: bool = False


class LocationRequest(BaseModel):
    city: str = Field(default="Grenoble", min_length=1, max_length=60)


class MarketRequest(BaseModel):
    market: str = Field(default="CAC 40", min_length=1, max_length=60)


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "NOVA", "version": app.version}


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "online": True,
        "mode_rapide": False,
        "history": session_history[-30:],
        "time": datetime.now().isoformat(timespec="seconds"),
        "palette": {"bg": BG, "blue": BLUE, "cyan": CYAN, "violet": VIOLET, "white": WHITE, "muted": MUTED, "panel": PANEL, "line": LINE},
    }


@app.post("/api/chat")
def chat(payload: ChatRequest) -> JSONResponse:
    text = clean_text(payload.text)
    add_history("user", text)

    action: dict[str, Any] | None = None
    lower = text.lower()

    if wants_weather(text):
        city = find_city(text)
        try:
            meteo = fetch_weather(city)
            action = {"type": "weather", "data": meteo}
            reply = (
                f"Météo {meteo['city']} : {meteo['temperature']} °C, {meteo['condition']}. "
                f"Ressenti {meteo['feels_like']} °C, humidité {meteo['humidity']} %."
            )
        except Exception:
            reply = f"Je n'arrive pas à récupérer la météo de {city} pour le moment."
    elif wants_market(text):
        requested = "CAC 40"
        for name in MARKETS:
            if name in lower:
                requested = name
                break
        try:
            market = fetch_market(requested)
            action = {"type": "market", "data": market}
            if market["price"] is None:
                reply = f"Les données de {market['label']} sont momentanément indisponibles."
            else:
                currency = market["currency"] or ""
                change = market["change_pct"]
                suffix = f", variation {change:+.2f} %" if isinstance(change, (int, float)) else ""
                reply = f"{market['label']} : {market['price']} {currency}{suffix}."
        except Exception:
            reply = "Je n'arrive pas à récupérer la donnée de marché pour le moment."
    else:
        url = url_action(text)
        if url:
            action = {"type": "open_url", "url": url, "device": payload.device}
            reply = "J'ouvre le site sur cet appareil."
        elif "sur le pc" in lower or "depuis le pc" in lower:
            reply = "Pour une action sur le PC, il faut que l'agent NOVA du PC soit lancé."
        elif re.search(r"\b(heure|quelle heure|il est quelle heure)\b", lower):
            reply = "Il est " + datetime.now().strftime("%H:%M") + "."
        elif "mode rapide" in lower:
            reply = "Le mode rapide est géré directement par l'interface du téléphone."
        elif payload.fast:
            reply = conversation_reply(text)
            if len(reply) > 100:
                reply = reply.split(".")[0] + "."
        else:
            reply = conversation_reply(text)

    add_history("nova", reply)
    return JSONResponse({"text": reply, "action": action})


@app.post("/api/weather")
def weather(payload: LocationRequest) -> dict[str, Any]:
    try:
        return fetch_weather(payload.city)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Météo indisponible: {exc}") from exc


@app.post("/api/market")
def market(payload: MarketRequest) -> dict[str, Any]:
    try:
        return fetch_market(payload.market)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Marché indisponible: {exc}") from exc


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(BASE_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(BASE_DIR / "sw.js", media_type="application/javascript")
