# -*- coding: utf-8 -*-
"""
NOVA — backend iPhone / PWA
Version 3.0
- Conversation locale enrichie (10 réponses ou plus par catégorie)
- Commandes d'applications/sites
- Priorité aux liens d'applications/universal links sur iPhone
- Fallback web si l'application n'est pas disponible
- Météo Open-Meteo
- Marchés Yahoo Finance
- API FastAPI compatible Render
"""

from __future__ import annotations

import difflib
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

BG = "#030712"
BLUE = "#38bdf8"
CYAN = "#67e8f9"
VIOLET = "#a78bfa"
WHITE = "#e8f5ff"
MUTED = "#66809a"
PANEL = "#07111f"
LINE = "#10253a"

app = FastAPI(title="NOVA", version="3.0.0")

session_history: list[dict[str, str]] = []
last_intent = "defaut"
last_subject = ""

# ---------------------------------------------------------------------------
# CONVERSATION : 10+ réponses par catégorie
# ---------------------------------------------------------------------------

RESPONSES = {
    "salut": [
        "Salut. NOVA est opérationnelle. Qu'est-ce qu'on fait ?",
        "Salut. Je suis prête.",
        "Bonjour. Dis-moi ce que tu veux faire.",
        "Présente. On continue ?",
        "Salut. Je t'écoute.",
        "Bonjour. Système NOVA en ligne.",
        "Salut. Quel est le programme ?",
        "Bonjour. Donne-moi ta prochaine commande.",
        "Salut. Je suis connectée.",
        "Hello. On peut commencer.",
        "Coucou. Qu'est-ce que tu veux faire ?",
    ],
    "merci": [
        "Avec plaisir.",
        "Pas de souci.",
        "Toujours disponible.",
        "Bien reçu.",
        "Avec plaisir, on continue.",
        "Je t'en prie.",
        "Service NOVA opérationnel.",
        "Pas de problème.",
        "C'est fait.",
        "Toujours là.",
        "Commande terminée.",
        "Avec plaisir.",
    ],
    "humeur": [
        "Je t'écoute. Qu'est-ce qui te met dans cet état ?",
        "D'accord. Raconte-moi ce qui se passe.",
        "Je suis là. Qu'est-ce que tu as en tête ?",
        "On peut prendre ça étape par étape.",
        "Tu peux m'expliquer ce qui se passe.",
        "D'accord. Qu'est-ce qui te préoccupe ?",
        "Je t'écoute. Commence par le point le plus important.",
        "On peut regarder ça calmement.",
        "Dis-moi ce qui s'est passé.",
        "Je suis attentive. Continue.",
        "D'accord. On va clarifier tout ça.",
        "Raconte-moi la suite.",
    ],
    "idee": [
        "Intéressant. C'est quoi l'idée de départ ?",
        "Décris-moi ton idée comme tu la vois.",
        "On peut la transformer en plan concret. Par quoi veux-tu commencer ?",
        "Je note l'idée. Qu'est-ce qu'elle doit permettre de faire ?",
        "Bonne base. Quel serait le fonctionnement idéal ?",
        "Décris-moi le résultat que tu imagines.",
        "On peut commencer par définir les fonctions principales.",
        "Quelle partie de ton idée veux-tu construire en premier ?",
        "D'accord. Qu'est-ce qui rendrait cette idée vraiment utile ?",
        "On peut lui donner une structure. Quel est le cœur du concept ?",
        "Très bien. Quelle serait la première version simple ?",
        "Je peux t'aider à passer de l'idée au prototype.",
    ],
    "projet": [
        "D'accord. Quel est l'objectif principal du projet ?",
        "On peut le découper en petites étapes. Tu veux commencer par laquelle ?",
        "Parle-moi de l'état actuel du projet.",
        "Très bien. Quelle est la prochaine étape que tu veux réaliser ?",
        "On peut commencer par l'architecture.",
        "Quel est le résultat final que tu veux obtenir ?",
        "Donne-moi les fonctions que tu veux absolument avoir.",
        "On peut construire une première version puis l'améliorer.",
        "Qu'est-ce qui est déjà terminé ?",
        "Quel est le point qui te bloque actuellement ?",
        "On peut organiser le projet en modules.",
        "Décris-moi la version idéale du projet.",
    ],
    "probleme": [
        "On va isoler le problème. Qu'est-ce qui ne fonctionne pas exactement ?",
        "Décris-moi le blocage et ce que tu as déjà essayé.",
        "On peut chercher la cause ensemble. Qu'est-ce que tu observes ?",
        "Commence par me donner le message d'erreur ou le comportement gênant.",
        "D'accord. À quel moment le problème apparaît-il ?",
        "On va procéder étape par étape.",
        "Qu'est-ce qui fonctionnait avant ?",
        "Donne-moi le résultat attendu puis le résultat obtenu.",
        "Je peux t'aider à isoler la cause.",
        "D'accord. On commence par vérifier le point le plus probable.",
        "Quel est le dernier changement que tu as fait ?",
        "On va réduire le problème à un cas simple.",
    ],
    "question": [
        "Oui. Pose ta question.",
        "Vas-y, je t'écoute.",
        "D'accord. Quelle est ta question ?",
        "Je suis prête. Explique-moi ce que tu cherches.",
        "Pose-la directement.",
        "D'accord. Donne-moi le point précis.",
        "Je t'écoute.",
        "Vas-y, on regarde ça.",
        "Quelle information veux-tu obtenir ?",
        "D'accord. Je vais regarder le sujet avec toi.",
        "Explique-moi ce que tu veux comprendre.",
        "On peut commencer par ta question principale.",
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
        "Très bien. Précise-moi ce point.",
        "Je suis prête pour la suite.",
        "D'accord. Donne-moi le détail important.",
        "On continue.",
    ],
}

# ---------------------------------------------------------------------------
# APPLICATIONS / SITES
#
# Sur iPhone, on privilégie les URLs HTTP(S) qui peuvent être des Universal
# Links : si l'app est installée et que le domaine les prend en charge,
# iOS peut ouvrir l'app ; sinon le même lien reste utilisable dans le web.
# Apple documente ce comportement des Universal Links.
# ---------------------------------------------------------------------------

APP_COMMANDS: dict[str, dict[str, Any]] = {
    "netflix": {
        "aliases": ["netflix"],
        "url": "https://www.netflix.com/",
        "label": "Netflix",
    },
    "youtube": {
        "aliases": ["youtube", "youtube music"],
        "url": "https://www.youtube.com/",
        "label": "YouTube",
    },
    "spotify": {
        "aliases": ["spotify"],
        "url": "https://open.spotify.com/",
        "label": "Spotify",
    },
    "discord": {
        "aliases": ["discord"],
        "url": "https://discord.com/app",
        "label": "Discord",
    },
    "instagram": {
        "aliases": ["instagram", "insta"],
        "url": "https://www.instagram.com/",
        "label": "Instagram",
    },
    "tiktok": {
        "aliases": ["tiktok", "tik tok"],
        "url": "https://www.tiktok.com/",
        "label": "TikTok",
    },
    "chatgpt": {
        "aliases": ["chatgpt", "chat gpt"],
        "url": "https://chatgpt.com/",
        "label": "ChatGPT",
    },
    "gmail": {
        "aliases": ["gmail", "mail"],
        "url": "https://mail.google.com/",
        "label": "Gmail",
    },
    "google": {
        "aliases": ["google"],
        "url": "https://www.google.com/",
        "label": "Google",
    },
    "github": {
        "aliases": ["github", "git hub"],
        "url": "https://github.com/",
        "label": "GitHub",
    },
    "brawl stars": {
        "aliases": ["brawl stars", "brawlstar", "brawl star", "brawl"],
        "url": "https://supercell.com/en/games/brawlstars/",
        "label": "Brawl Stars",
    },
    "clash royale": {
        "aliases": ["clash royale", "clash royal"],
        "url": "https://supercell.com/en/games/clashroyale/",
        "label": "Clash Royale",
    },
    "clash of clans": {
        "aliases": ["clash of clans", "clash"],
        "url": "https://supercell.com/en/games/clashofclans/",
        "label": "Clash of Clans",
    },
    "roblox": {
        "aliases": ["roblox"],
        "url": "https://www.roblox.com/",
        "label": "Roblox",
    },
    "minecraft": {
        "aliases": ["minecraft", "minecraft bedrock", "minecraft java"],
        "url": "https://www.minecraft.net/",
        "label": "Minecraft",
    },
    "steam": {
        "aliases": ["steam"],
        "url": "https://store.steampowered.com/",
        "label": "Steam",
    },
    "twitch": {
        "aliases": ["twitch"],
        "url": "https://www.twitch.tv/",
        "label": "Twitch",
    },
    "prime video": {
        "aliases": ["prime video", "amazon prime", "prime"],
        "url": "https://www.primevideo.com/",
        "label": "Prime Video",
    },
    "disney plus": {
        "aliases": ["disney plus", "disney+", "disney"],
        "url": "https://www.disneyplus.com/",
        "label": "Disney+",
    },
    "max": {
        "aliases": ["max", "hbo max"],
        "url": "https://www.max.com/",
        "label": "Max",
    },
    "google maps": {
        "aliases": ["google maps", "maps"],
        "url": "https://www.google.com/maps/",
        "label": "Google Maps",
    },
    "wikipedia": {
        "aliases": ["wikipedia", "wiki"],
        "url": "https://www.wikipedia.org/",
        "label": "Wikipedia",
    },
    "reddit": {
        "aliases": ["reddit"],
        "url": "https://www.reddit.com/",
        "label": "Reddit",
    },
    "linkedin": {
        "aliases": ["linkedin", "linked in"],
        "url": "https://www.linkedin.com/",
        "label": "LinkedIn",
    },
}

# Alias "site-only" conservé pour compatibilité avec d'anciens appels.
SITES = {k: v["url"] for k, v in APP_COMMANDS.items()}

OPEN_WORDS = (
    "ouvre", "ouvrir", "lance", "lancer", "démarre", "demarre",
    "démarrer", "demarrer", "va sur", "aller sur", "mets", "met",
    "accède", "accede", "affiche"
)

# ---------------------------------------------------------------------------
# MARCHÉS / MÉTÉO
# ---------------------------------------------------------------------------

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
    0: "Ciel dégagé", 1: "Peu nuageux", 2: "Partiellement nuageux",
    3: "Couvert", 45: "Brouillard", 48: "Brouillard givrant",
    51: "Bruine légère", 53: "Bruine", 55: "Bruine forte",
    61: "Pluie légère", 63: "Pluie", 65: "Pluie forte",
    71: "Neige légère", 73: "Neige", 75: "Neige forte",
    80: "Averses légères", 81: "Averses", 82: "Fortes averses",
    95: "Orage", 96: "Orage avec grêle", 99: "Orage fort",
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
    if re.search(r"\b(salut|bonjour|hello|yo|coucou|bonsoir)\b", t):
        return "salut"
    if re.search(r"\b(merci|thanks|super|parfait)\b", t):
        return "merci"
    if re.search(r"\b(idée|idee|j'ai pensé|j ai pense|imagine|concept)\b", t):
        return "idee"
    if re.search(r"\b(projet|développer|developper|programme|application|app|coder|code)\b", t):
        return "projet"
    if re.search(r"\b(problème|probleme|bug|erreur|bloqué|bloque|marche pas|ça ne marche|ca ne marche)\b", t):
        return "probleme"
    if re.search(r"\b(comment|pourquoi|quelle|quel|qu'est-ce|question|est-ce que)\b", t):
        return "question"
    if re.search(r"\b(ça va|ca va|stress|triste|content|fatigué|fatigue|énervé|enerve|mal|bien)\b", t):
        return "humeur"
    return "defaut"


def subject_from_text(text: str) -> str:
    t = clean_text(text)
    t = re.sub(
        r"^(j'ai|j ai|je veux|je voudrais|mon|ma|mes|c'est|c est)\s+",
        "",
        t,
        flags=re.I,
    )
    words = t.split()
    return " ".join(words[:9]).strip(" ,.!?")


def conversation_reply(text: str) -> str:
    global last_intent, last_subject

    intent = detect_intent(text)
    subject = subject_from_text(text)
    low = text.lower().strip()

    if re.fullmatch(r"(oui|ouais|yes|d'accord|ok|okay|exact)\W*", low):
        reply = {
            "projet": "Parfait. Donne-moi la prochaine étape et on la découpe ensemble.",
            "idee": "Parfait. On peut maintenant préciser le fonctionnement.",
            "probleme": "Très bien. Donne-moi le détail qui bloque encore.",
            "question": "D'accord. On continue sur ce point.",
            "humeur": "D'accord. Raconte-moi la suite.",
        }.get(last_intent, "D'accord. Continue.")
    elif re.fullmatch(r"(non|nan|pas vraiment)\W*", low):
        reply = "D'accord. On change d'angle. Qu'est-ce que tu préfères faire ?"
    elif re.search(r"\b(et après|ensuite|et maintenant)\b", low):
        reply = "Ensuite, on valide le résultat puis on passe à l'étape suivante."
    elif re.search(r"\bpourquoi\b", low) and last_subject:
        reply = f"Pour le point « {last_subject} », le plus simple est de regarder d'abord la cause puis l'effet."
    elif re.search(r"\bcomment\b", low) and last_subject:
        reply = f"Pour « {last_subject} », on peut procéder étape par étape. Donne-moi ce que tu as déjà."
    else:
        pool = RESPONSES.get(intent, RESPONSES["defaut"])
        # évite de toujours prendre la même réponse
        seed = sum(ord(c) for c in text) + len(session_history)
        reply = pool[seed % len(pool)]

    last_intent = intent
    last_subject = subject or last_subject
    return reply


def find_app_command(text: str) -> dict[str, Any] | None:
    """Détecte une application/site dans une commande d'ouverture."""
    t = text.lower()

    if not any(word in t for word in OPEN_WORDS):
        return None

    # Trie les alias du plus long au plus court pour éviter les collisions.
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for key, info in APP_COMMANDS.items():
        for alias in info["aliases"]:
            candidates.append((alias.lower(), key, info))

    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    for alias, key, info in candidates:
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", t):
            return {
                "key": key,
                "label": info["label"],
                "url": info["url"],
            }

    return None


def url_action(text: str) -> dict[str, Any] | None:
    """Retourne une action d'ouverture plutôt qu'une simple URL."""
    app = find_app_command(text)
    if app:
        return app

    m = re.search(r"https?://[^\s]+", text, flags=re.I)
    if m:
        url = m.group(0).rstrip(".,!?;)")
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            return {"label": url, "url": url, "key": "custom"}

    if re.search(r"\b(cherche|recherche|google)\b", text.lower()):
        q = re.sub(
            r".*?\b(cherche|recherche|google)\b",
            "",
            text,
            count=1,
            flags=re.I,
        ).strip()
        if q:
            return {
                "label": "Recherche Google",
                "url": "https://www.google.com/search?q=" + quote_plus(q),
                "key": "google-search",
            }

    return None


def wants_weather(text: str) -> bool:
    return bool(re.search(
        r"\b(météo|meteo|temps|prévisions|previsions)\b",
        text.lower()
    ))


def wants_market(text: str) -> bool:
    return bool(re.search(
        r"\b(bourse|marché|marche|cac 40|cac40|nasdaq|dow jones|sp500|s&p 500|bitcoin|actions)\b",
        text.lower()
    ))


def find_city(text: str) -> str:
    t = text.lower()

    for key, value in CITY_ALIASES.items():
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", t):
            return value

    m = re.search(r"(?:à|a|sur|de)\s+([A-Za-zÀ-ÿ' -]{2,40})", text, re.I)
    if m:
        candidate = m.group(1).strip(" .,!?;")
        candidate = re.sub(
            r"\b(la|le|les|au|aux|dans)\b",
            "",
            candidate,
            flags=re.I,
        ).strip()
        if candidate:
            return candidate.title()

    return "Grenoble"


def fetch_weather(city: str) -> dict[str, Any]:
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={
            "name": city,
            "count": 1,
            "language": "fr",
            "format": "json",
        },
        timeout=8,
    )
    geo.raise_for_status()

    results = geo.json().get("results") or []
    if not results:
        raise ValueError("Ville introuvable")

    place = results[0]
    lat = place["latitude"]
    lon = place["longitude"]

    weather = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,apparent_temperature,weather_code,"
                "wind_speed_10m,relative_humidity_2m"
            ),
            "timezone": "auto",
        },
        timeout=8,
    )
    weather.raise_for_status()

    current = weather.json().get("current", {})

    return {
        "city": place.get("name", city),
        "temperature": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "code": current.get("weather_code"),
        "condition": WEATHER_CODES.get(
            current.get("weather_code"),
            "Conditions inconnues"
        ),
        "time": current.get("time"),
        "source": "Open-Meteo",
    }


def fetch_market(label: str) -> dict[str, Any]:
    key = label.lower().strip()
    info = MARKETS.get(key)

    if info is None:
        best = difflib.get_close_matches(
            key,
            list(MARKETS.keys()),
            n=1,
            cutoff=0.65,
        )
        if not best:
            raise ValueError("Valeur introuvable")
        info = MARKETS[best[0]]

    symbol = info["symbol"]

    data = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",
        params={"range": "1d", "interval": "5m"},
        headers={"User-Agent": "NOVA/3.0"},
        timeout=8,
    )
    data.raise_for_status()

    result = (
        data.json()
        .get("chart", {})
        .get("result") or [None]
    )[0]

    if not result:
        raise ValueError("Donnée de marché indisponible")

    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("previousClose")

    change = None
    change_pct = None

    if (
        isinstance(price, (int, float))
        and isinstance(previous, (int, float))
        and previous
    ):
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
        "palette": {
            "bg": BG,
            "blue": BLUE,
            "cyan": CYAN,
            "violet": VIOLET,
            "white": WHITE,
            "muted": MUTED,
            "panel": PANEL,
            "line": LINE,
        },
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
                f"Météo {meteo['city']} : {meteo['temperature']} °C, "
                f"{meteo['condition']}. Ressenti {meteo['feels_like']} °C, "
                f"humidité {meteo['humidity']} %."
            )
        except Exception:
            reply = (
                f"Je n'arrive pas à récupérer la météo de {city} "
                "pour le moment."
            )

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
                reply = (
                    f"Les données de {market['label']} "
                    "sont momentanément indisponibles."
                )
            else:
                currency = market["currency"] or ""
                change = market["change_pct"]
                suffix = (
                    f", variation {change:+.2f} %"
                    if isinstance(change, (int, float))
                    else ""
                )
                reply = (
                    f"{market['label']} : {market['price']} "
                    f"{currency}{suffix}."
                )
        except Exception:
            reply = (
                "Je n'arrive pas à récupérer la donnée de marché "
                "pour le moment."
            )

    else:
        command = url_action(text)

        if command:
            # IMPORTANT :
            # Sur iPhone, un lien HTTPS peut être pris en charge par l'app
            # installée via Universal Links. Sinon, il reste une URL web.
            # On ne prétend donc pas détecter à distance si l'app est installée.
            action = {
                "type": "open_url",
                "url": command["url"],
                "device": payload.device,
                "label": command["label"],
                "app_first": True,
                "fallback_url": command["url"],
            }

            if command["key"] == "custom":
                reply = "J'ouvre le lien demandé."
            else:
                reply = f"J'ouvre {command['label']}."

        elif "sur le pc" in lower or "depuis le pc" in lower:
            reply = (
                "Pour lancer une application installée sur le PC, "
                "l'agent NOVA du PC doit être actif."
            )

        elif re.search(
            r"\b(heure|quelle heure|il est quelle heure)\b",
            lower
        ):
            reply = "Il est " + datetime.now().strftime("%H:%M") + "."

        elif "mode rapide" in lower:
            reply = (
                "Le mode rapide est géré directement "
                "par l'interface du téléphone."
            )

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
        raise HTTPException(
            status_code=502,
            detail=f"Météo indisponible: {exc}",
        ) from exc


@app.post("/api/market")
def market(payload: MarketRequest) -> dict[str, Any]:
    try:
        return fetch_market(payload.market)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Marché indisponible: {exc}",
        ) from exc


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(
        BASE_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(
        BASE_DIR / "sw.js",
        media_type="application/javascript",
    )
