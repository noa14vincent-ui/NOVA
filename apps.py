"""NOVA : ouverture d'applis sur le téléphone (iPhone) ou sur le PC (via agent)."""
import hmac
import os
import re
import time

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()

PC_TOKEN = os.getenv("NOVA_PC_TOKEN", "").strip()
_queue: list[dict] = []
_last_seen = 0.0

OPEN_WORDS = (
    "ouvre", "ouvrir", "lance", "lancer", "démarre", "demarre",
    "démarrer", "demarrer", "va sur", "aller sur", "mets", "met",
    "accède", "accede", "affiche",
)

# clé: (nom affiché, adresse web, schéma d'appli iPhone ou None)
APPS = {
    "netflix": ("Netflix", "https://www.netflix.com/", "nflx://"),
    "youtube": ("YouTube", "https://www.youtube.com/", "youtube://"),
    "spotify": ("Spotify", "https://open.spotify.com/", "spotify://"),
    "discord": ("Discord", "https://discord.com/app", "discord://"),
    "instagram": ("Instagram", "https://www.instagram.com/", "instagram://"),
    "tiktok": ("TikTok", "https://www.tiktok.com/", "tiktok://"),
    "whatsapp": ("WhatsApp", "https://web.whatsapp.com/", "whatsapp://"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/", None),
    "gmail": ("Gmail", "https://mail.google.com/", "googlegmail://"),
    "google maps": ("Google Maps", "https://maps.google.com/", "comgooglemaps://"),
    "maps": ("Google Maps", "https://maps.google.com/", "comgooglemaps://"),
    "google": ("Google", "https://www.google.com/", None),
    "github": ("GitHub", "https://github.com/", None),
    "brawl stars": ("Brawl Stars", "https://link.brawlstars.com/", None),
    "brawl": ("Brawl Stars", "https://link.brawlstars.com/", None),
    "clash royale": ("Clash Royale", "https://link.clashroyale.com/", None),
    "clash of clans": ("Clash of Clans", "https://link.clashofclans.com/", None),
    "roblox": ("Roblox", "https://www.roblox.com/", "roblox://"),
    "minecraft": ("Minecraft", "https://www.minecraft.net/", None),
    "steam": ("Steam", "https://store.steampowered.com/", "steam://"),
    "twitch": ("Twitch", "https://www.twitch.tv/", "twitch://"),
    "prime video": ("Prime Video", "https://www.primevideo.com/", None),
    "disney+": ("Disney+", "https://www.disneyplus.com/", "disneyplus://"),
    "disney": ("Disney+", "https://www.disneyplus.com/", "disneyplus://"),
    "max": ("Max", "https://www.max.com/", None),
    "wikipedia": ("Wikipedia", "https://www.wikipedia.org/", None),
    "reddit": ("Reddit", "https://www.reddit.com/", "reddit://"),
    "linkedin": ("LinkedIn", "https://www.linkedin.com/", "linkedin://"),
    # Applis qui n'existent que sur PC (l'agent sait les lancer)
    "calculatrice": ("la calculatrice", None, None),
    "bloc notes": ("le Bloc-notes", None, None),
    "explorateur": ("l'Explorateur", None, None),
    "vscode": ("VS Code", None, None),
    "chrome": ("Chrome", None, None),
    "edge": ("Edge", None, None),
    "paramètres": ("les Paramètres", None, None),
}


def _find_app(text: str):
    t = text.lower().strip()
    if not any(t.startswith(w) or f" {w} " in f" {t} " for w in OPEN_WORDS):
        return None
    for key in sorted(APPS, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", t):
            label, url, scheme = APPS[key]
            return key, label, url, scheme
    return None


def _device(text: str, current: str) -> str:
    t = text.lower()
    if re.search(r"\b(pc|ordinateur|ordi)\b", t):
        return "pc"
    if re.search(r"\b(tel|téléphone|telephone|iphone|portable)\b", t):
        return "iphone"
    return "pc" if current == "pc" else "iphone"


def _open_on_pc(key: str, label: str, url):
    if not PC_TOKEN:
        return {"reply": "Pour piloter le PC, ajoute la variable NOVA_PC_TOKEN sur Render."}
    if time.time() - _last_seen > 20:
        return {"reply": "Je ne vois pas ton PC. Lance l'agent NOVA (nova_pc_agent.py) sur le PC."}
    _queue.append({"app": key, "label": label, "url": url, "ts": time.time()})
    return {"reply": f"J'ouvre {label} sur le PC.", "action": {"type": "pc_open", "label": label}}


def try_open(text: str, current: str = "iphone"):
    """Renvoie {'reply', 'action'?} si la phrase demande d'ouvrir une appli, sinon None."""
    found = _find_app(text)
    if not found:
        return None
    key, label, url, scheme = found
    device = _device(text, current)

    if device == "pc":
        return _open_on_pc(key, label, url)

    if current == "pc":
        return {"reply": "Je ne peux pas piloter ton téléphone depuis le PC. Demande-le depuis l'iPhone."}
    if not url:
        return {"reply": f"{label.capitalize()} n'existe que sur PC. Dis « ouvre {key} sur le PC »."}

    return {
        "reply": f"J'ouvre {label}.",
        "action": {"type": "open_url", "device": "iphone", "url": url, "ios_url": scheme, "label": label},
    }


@router.get("/api/pc/next")
def pc_next(x_nova_token: str = Header(default="")):
    """Appelé toutes les 2 s par l'agent installé sur le PC."""
    global _last_seen
    if not PC_TOKEN or not hmac.compare_digest(x_nova_token.encode(), PC_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="token invalide")
    _last_seen = time.time()
    _queue[:] = [c for c in _queue if _last_seen - c["ts"] < 60]
    if _queue:
        c = _queue.pop(0)
        return {"command": {"app": c["app"], "label": c["label"], "url": c["url"]}}
    return {"command": None}
