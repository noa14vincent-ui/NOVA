# -*- coding: utf-8 -*-
"""NOVA backend: Gemini + commandes + météo + bourse."""
from __future__ import annotations
import difflib, os, re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from google import genai
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
app = FastAPI(title="NOVA", version="4.0.0")
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except Exception as error:
    print("Gemini non initialise:", error)
    gemini_client = None

session_history: list[dict[str, str]] = []
APP_COMMANDS = {
 "netflix":(["netflix"],"https://www.netflix.com/","Netflix"), "youtube":(["youtube","youtube music"],"https://www.youtube.com/","YouTube"), "spotify":(["spotify"],"https://open.spotify.com/","Spotify"), "discord":(["discord"],"https://discord.com/app","Discord"), "instagram":(["instagram","insta"],"https://www.instagram.com/","Instagram"), "tiktok":(["tiktok","tik tok"],"https://www.tiktok.com/","TikTok"), "chatgpt":(["chatgpt","chat gpt"],"https://chatgpt.com/","ChatGPT"), "gmail":(["gmail","mail"],"https://mail.google.com/","Gmail"), "github":(["github","git hub"],"https://github.com/","GitHub"), "roblox":(["roblox"],"https://www.roblox.com/","Roblox"), "minecraft":(["minecraft"],"https://www.minecraft.net/","Minecraft"), "steam":(["steam"],"https://store.steampowered.com/","Steam"), "twitch":(["twitch"],"https://www.twitch.tv/","Twitch"), "google maps":(["google maps","maps"],"https://www.google.com/maps/","Google Maps"), "wikipedia":(["wikipedia","wiki"],"https://www.wikipedia.org/","Wikipedia"), "reddit":(["reddit"],"https://www.reddit.com/","Reddit")}
OPEN_WORDS = ("ouvre","ouvrir","lance","lancer","démarre","demarre","démarrer","demarrer","va sur","aller sur","accède","accede","affiche")
MARKETS = {"cac 40":("^FCHI","CAC 40"),"cac40":("^FCHI","CAC 40"),"nasdaq":("^IXIC","Nasdaq"),"dow":("^DJI","Dow Jones"),"dow jones":("^DJI","Dow Jones"),"sp500":("^GSPC","S&P 500"),"bitcoin":("BTC-USD","Bitcoin"),"apple":("AAPL","Apple"),"tesla":("TSLA","Tesla"),"amazon":("AMZN","Amazon"),"microsoft":("MSFT","Microsoft"),"nvidia":("NVDA","NVIDIA"),"meta":("META","Meta"),"lvmh":("MC.PA","LVMH"),"totalenergies":("TTE.PA","TotalEnergies")}
CITIES={"grenoble":"Grenoble","paris":"Paris","lyon":"Lyon","marseille":"Marseille","toulouse":"Toulouse","nice":"Nice","bordeaux":"Bordeaux","lille":"Lille","strasbourg":"Strasbourg"}
WEATHER={0:"Ciel dégagé",1:"Peu nuageux",2:"Partiellement nuageux",3:"Couvert",45:"Brouillard",48:"Brouillard givrant",51:"Bruine légère",53:"Bruine",55:"Bruine forte",61:"Pluie légère",63:"Pluie",65:"Pluie forte",71:"Neige légère",73:"Neige",75:"Neige forte",80:"Averses légères",81:"Averses",82:"Fortes averses",95:"Orage",96:"Orage avec grêle",99:"Orage fort"}

class ChatRequest(BaseModel):
    text: str = Field(min_length=1,max_length=4000)
    device: str = Field(default="iphone",max_length=30)
    fast: bool = False
class LocationRequest(BaseModel): city: str = Field(default="Grenoble",min_length=1,max_length=60)
class MarketRequest(BaseModel): market: str = Field(default="CAC 40",min_length=1,max_length=60)

def clean(text:str)->str: return re.sub(r"\s+"," ",text).strip()
def remember(role:str,text:str)->None:
    session_history.append({"role":role,"text":text})
    del session_history[:-30]
def get(url:str,**kwargs:Any)->dict[str,Any]:
    r=requests.get(url,timeout=10,headers={"User-Agent":"NOVA/4.0"},**kwargs);r.raise_for_status();return r.json()
def city_from(text:str)->str:
    low=text.lower()
    for alias,city in CITIES.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)",low): return city
    m=re.search(r"(?:à|a|sur|de)\s+([A-Za-zÀ-ÿ' -]{2,40})",text,re.I)
    return m.group(1).strip(" .,!?;").title() if m else "Grenoble"
def weather(city:str)->dict[str,Any]:
    p=(get("https://geocoding-api.open-meteo.com/v1/search",params={"name":city,"count":1,"language":"fr","format":"json"}).get("results") or [None])[0]
    if not p: raise ValueError("Ville introuvable")
    c=get("https://api.open-meteo.com/v1/forecast",params={"latitude":p["latitude"],"longitude":p["longitude"],"current":"temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m","timezone":"auto"}).get("current",{})
    return {"city":p.get("name",city),"temperature":c.get("temperature_2m"),"feels_like":c.get("apparent_temperature"),"humidity":c.get("relative_humidity_2m"),"wind":c.get("wind_speed_10m"),"condition":WEATHER.get(c.get("weather_code"),"Conditions inconnues"),"source":"Open-Meteo"}
def market(name:str)->dict[str,Any]:
    key=name.lower().strip()
    if key not in MARKETS:
        found=difflib.get_close_matches(key,list(MARKETS),n=1,cutoff=.6)
        if not found: raise ValueError("Valeur introuvable")
        key=found[0]
    symbol,label=MARKETS[key]; data=get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",params={"range":"1d","interval":"5m"})
    result=(data.get("chart",{}).get("result") or [None])[0]
    if not result: raise ValueError("Donnée indisponible")
    meta=result.get("meta",{}); price=meta.get("regularMarketPrice"); previous=meta.get("previousClose")
    pct=((price-previous)/previous*100) if isinstance(price,(int,float)) and isinstance(previous,(int,float)) and previous else None
    return {"symbol":symbol,"label":label,"price":price,"currency":meta.get("currency", ""),"previous_close":previous,"change_pct":pct,"source":"Yahoo Finance"}
def open_action(text:str,device:str)->dict[str,Any]|None:
    low=text.lower()
    if not any(w in low for w in OPEN_WORDS): return None
    for _,(aliases,url,label) in sorted(APP_COMMANDS.items(),key=lambda x:max(map(len,x[1][0])),reverse=True):
        if any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)",low) for a in aliases): return {"type":"open_url","url":url,"device":device,"label":label,"app_first":True,"fallback_url":url}
    m=re.search(r"https?://[^\s]+",text,re.I)
    if m and urlparse(m.group(0)).scheme in {"http","https"}: return {"type":"open_url","url":m.group(0).rstrip(".,!?;)"),"device":device,"label":"Lien"}
    return None
def ask_gemini(text: str, fast: bool) -> str | None:
    if not gemini_client:
        print("❌ Gemini : client non initialisé")
        return None

    hist = "\n".join(
        f"{x['role']}: {x['text']}"
        for x in session_history[-10:]
    )

    prompt = f"""
Tu es NOVA, une assistante IA française utile, naturelle et chaleureuse.

Tu aides particulièrement un élève qui apprend :
- Python
- C#
- Unity
- développement de jeux
- intelligence artificielle

Règles :
- Réponds uniquement en français.
- Sois naturelle et facile à comprendre.
- {'Réponds en deux phrases maximum.' if fast else 'Explique clairement avec des étapes lorsque c’est utile.'}
- Pour du code, donne du code directement utilisable.
- Ne prétends jamais avoir ouvert une application ou contrôlé un appareil si NOVA ne l'a pas réellement fait.

Historique récent :
{hist}

Message utilisateur :
{text}
"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        print("✅ Réponse Gemini reçue")

        answer = (response.text or "").strip()

        if not answer:
            print("⚠️ Gemini a répondu sans texte")
            print("Réponse complète :", response)
            return None

        return answer[:4000]

    except Exception as error:
        print("❌ ERREUR GEMINI COMPLÈTE :", repr(error))
        return None

@app.get("/")
def home(): return FileResponse(BASE_DIR/"index.html")
@app.get("/api/health")
def health(): return {"status":"ok","service":"NOVA","version":app.version,"gemini_configured":gemini_client is not None,"gemini_model":GEMINI_MODEL}
@app.get("/api/status")
def status(): return {"online":True,"history":session_history[-20:],"time":datetime.now().isoformat(timespec="seconds"),"gemini_configured":gemini_client is not None}
@app.post("/api/chat")
def chat(payload:ChatRequest)->JSONResponse:
    text=clean(payload.text); remember("user",text); low=text.lower(); action=None
    try:
        if re.search(r"\b(météo|meteo|temps|prévisions|previsions)\b",low):
            d=weather(city_from(text)); action={"type":"weather","data":d}; reply=f"Météo {d['city']} : {d['temperature']} °C, {d['condition']}. Ressenti {d['feels_like']} °C, humidité {d['humidity']} %."
        elif re.search(r"\b(bourse|marché|marche|cac 40|cac40|nasdaq|dow jones|sp500|bitcoin|actions)\b",low):
            name=next((n for n in MARKETS if n in low),"cac 40"); d=market(name); action={"type":"market","data":d}; suffix=f", variation {d['change_pct']:+.2f} %" if isinstance(d['change_pct'],(int,float)) else ""; reply=f"{d['label']} : {d['price']} {d['currency']}{suffix}."
        else:
            action=open_action(text,payload.device)
            if action: reply=f"J'ouvre {action['label']}."
            elif re.search(r"\b(heure|quelle heure|il est quelle heure)\b",low): reply="Il est "+datetime.now().strftime("%H:%M")+"."
            elif "mode rapide" in low: reply="Le mode rapide est géré directement par l'interface du téléphone."
            else: reply=ask_gemini(text,payload.fast) or "Gemini est indisponible ou la clé API n'est pas encore configurée."
    except Exception as error:
        print("Erreur NOVA:",error); reply="Je n'arrive pas à récupérer cette information pour le moment."
    remember("nova",reply); return JSONResponse({"text":reply,"action":action})
@app.post("/api/weather")
def weather_api(payload:LocationRequest):
    try:return weather(payload.city)
    except Exception as e: raise HTTPException(status_code=502,detail=f"Météo indisponible: {e}")
@app.post("/api/market")
def market_api(payload:MarketRequest):
    try:return market(payload.market)
    except Exception as e: raise HTTPException(status_code=502,detail=f"Marché indisponible: {e}")
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(BASE_DIR/"manifest.webmanifest",media_type="application/manifest+json")
@app.get("/sw.js")
def service_worker(): return FileResponse(BASE_DIR/"sw.js",media_type="application/javascript")
