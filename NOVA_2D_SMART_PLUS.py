# -*- coding: utf-8 -*-
"""
NOVA PHONE - interface 2D futuriste
- Aucune webcam
- Aucune détection des mains
- Aucune sphère 3D
- Champ de particules 2D animé
- Conversation vocale multi-tours
- Contrôle depuis le téléphone sur le même Wi-Fi
"""

import os
import re
import time
import math
import random
import difflib
import asyncio
import ctypes
import urllib.parse
import urllib.request
import webbrowser
import subprocess
import threading
import json
import socket
from ctypes import wintypes
from tkinter import Tk, Toplevel, Canvas, END, Text, Entry, Button, Label, Frame, StringVar, messagebox

import speech_recognition as sr
import edge_tts

# ============================================================
# CONFIG
# ============================================================

PHONE_HOST = "0.0.0.0"
PHONE_PORT = 8765

BG = "#030712"
BLUE = "#38bdf8"
CYAN = "#67e8f9"
VIOLET = "#a78bfa"
WHITE = "#e8f5ff"
MUTED = "#66809a"
PANEL = "#07111f"
LINE = "#10253a"

ETAT = "VEILLE"
etat_lock = threading.Lock()

conversation = []
conv_lock = threading.Lock()

audio_stop = threading.Event()
phone_stop = threading.Event()

phone_commands = []
phone_commands_lock = threading.Lock()

voice_busy = False
voice_lock = threading.Lock()

last_conversation = time.time()
nova_active = True
MODE_RAPIDE = False
reponses_recentes = []

# État léger de conversation : permet à NOVA de rebondir sur le sujet
# au lieu de tomber immédiatement sur une réponse générique.
conversation_intelligente = {
    "tour": 0,
    "sujet": "",
    "intent": "",
    "attente": "",
}
conversation_intelligente_lock = threading.Lock()


# Référence vers l'interface Tk principale (assignée dans main()).
# Sert à ouvrir des fenêtres depuis les threads voix / téléphone en toute
# sécurité, via NOVA_UI.root.after(0, ...).
NOVA_UI = None

# Caches partagés pour les modules "fenêtre agent secret" météo / bourse.
# Un thread d'arrière-plan les met à jour, la fenêtre Tk les relit en
# interrogeant (polling) depuis le thread principal — même logique que
# `conversation` / `refresh_chat` plus haut.
meteo_lock = threading.Lock()
meteo_cache = {"texte": "Connexion au satellite météo...", "jeton": None}

bourse_lock = threading.Lock()
bourse_cache = {"texte": "Connexion aux marchés financiers...", "jeton": None}

# ============================================================
# OUTILS
# ============================================================

def set_etat(x):
    global ETAT
    with etat_lock:
        ETAT = x

def get_etat():
    with etat_lock:
        return ETAT

def addconv(role, text):
    global last_conversation
    if not text:
        return
    with conv_lock:
        conversation.append((role, str(text)))
        del conversation[:-100]
    last_conversation = time.time()

def norm(s):
    s = str(s).lower().strip()
    table = str.maketrans({
        "à":"a","â":"a","ä":"a","é":"e","è":"e","ê":"e","ë":"e",
        "î":"i","ï":"i","ô":"o","ö":"o","ù":"u","û":"u","ü":"u","ç":"c"
    })
    return s.translate(table)

def corriger_transcription(s):
    """
    Tolérance aux petites erreurs de transcription.
    IMPORTANT : cette fonction agit APRÈS la reconnaissance Google.
    Elle ne modifie donc pas l'audio ni le fonctionnement du micro.
    """
    original = str(s).strip()
    t = norm(original)

    # Corrections de mots courants entendus de travers.
    corrections = {
        "youtub": "youtube",
        "youtoub": "youtube",
        "youtoube": "youtube",
        "you tube": "youtube",
        "gogle": "google",
        "gougle": "google",
        "netflx": "netflix",
        "netfliks": "netflix",
        "gitub": "github",
        "calculette": "calculatrice",
        "calculatrisse": "calculatrice",
        "recherch": "recherche",
        "rechercher": "recherche",
        "cher": "cherche",
        "ouvr": "ouvre",
        "ouvert": "ouvre",
        "demarre": "demarre",
        "démarre": "demarre",
        "lanc": "lance",
        "bonjours": "bonjour",
        "bonjourr": "bonjour",
        "mercie": "merci",
        "merçi": "merci",
        "paus": "pause",
        "pose": "pause",
    }

    mots = t.split()
    vocab = list(corrections.keys())

    for i, mot in enumerate(mots):
        if len(mot) < 3:
            continue

        # D'abord les remplacements exacts.
        if mot in corrections:
            mots[i] = corrections[mot]
            continue

        # Puis une petite tolérance, volontairement prudente.
        close = difflib.get_close_matches(
            mot,
            vocab,
            n=1,
            cutoff=0.82
        )
        if close:
            mots[i] = corrections[close[0]]

    corrected = " ".join(mots)

    # Variantes d'intentions fréquentes, pour ne pas exiger une phrase parfaite.
    phrases = [
        (("ca va", "ca va nova", "salut ca va", "tu vas bien"),
         "ca va"),
        (("qui est tu", "qui es tu", "c est quoi ton nom", "comment tu t appelle"),
         "qui es tu"),
        (("tu m entends", "tu m ecoutes", "est ce que tu m entends"),
         "tu comprends"),
    ]

    for variants, canonical in phrases:
        # Un rapprochement global très léger.
        if any(v == corrected for v in variants):
            corrected = canonical
            break

    return corrected

def essayer_reconnaissance(recognizer, audio):
    """
    Reconnaissance avec un petit filet de sécurité.
    On demande aussi les alternatives à Google, sans changer la capture audio.
    """
    try:
        resultat = recognizer.recognize_google(
            audio,
            language="fr-FR",
            show_all=True
        )

        if isinstance(resultat, dict):
            alternatives = resultat.get("alternative") or []
            phrases = [
                str(item.get("transcript", "")).strip()
                for item in alternatives
                if item.get("transcript")
            ]
            phrases = [p for p in phrases if p]

            if phrases:
                # Première alternative = meilleure transcription fournie par Google.
                return phrases[0], phrases
            return None, []

        if isinstance(resultat, str) and resultat.strip():
            return resultat.strip(), [resultat.strip()]

    except sr.UnknownValueError:
        pass

    except sr.RequestError as exc:
        print("🌐 Reconnaissance Google :", exc)
        return None, []

    except Exception as exc:
        print("🗣️ Reconnaissance :", repr(exc))
        return None, []

    # Second essai classique sur le même audio.
    try:
        texte = recognizer.recognize_google(
            audio,
            language="fr-FR"
        ).strip()
        if texte:
            return texte, [texte]
    except sr.UnknownValueError:
        return None, []
    except sr.RequestError as exc:
        print("🌐 Reconnaissance Google :", exc)
        return None, []
    except Exception as exc:
        print("🗣️ Reconnaissance :", repr(exc))

    return None, []


def choisir_reponse(options):
    global reponses_recentes
    disponibles = [x for x in options if x not in reponses_recentes]
    choix = random.choice(disponibles or options)
    reponses_recentes.append(choix)
    # Évite de ressortir trop vite une formulation déjà utilisée.
    del reponses_recentes[:-30]
    return choix

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ============================================================
# ACTIONS
# ============================================================

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "netflix": "https://www.netflix.com",
    "wikipedia": "https://www.wikipedia.org",
    "gmail": "https://mail.google.com",
}

APPS = {
    "calculatrice": "calc.exe",
    "calc": "calc.exe",
    "bloc note": "notepad.exe",
    "bloc-notes": "notepad.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
}

def ajouter_commande_telephone(c):
    with phone_commands_lock: phone_commands.append(c)
def prendre_commande_telephone():
    with phone_commands_lock: return phone_commands.pop(0) if phone_commands else None
def ouvrir_url_cible(url, cible):
    if cible == "telephone": ajouter_commande_telephone({"action":"ouvrir_url","url":url})
    else: webbrowser.open(url)
def cible_demande(texte, source):
    x=norm(texte)
    if any(z in x for z in ("sur mon telephone","sur mon tel","depuis mon telephone","depuis mon tel")): return "telephone"
    if any(z in x for z in ("sur le pc","sur l ordinateur","depuis le pc")): return "pc"
    return source

def ouvrir(texte, cible="pc"):
    t = norm(texte)
    out = []

    for name, url in SITES.items():
        if name in t:
            webbrowser.open(url)
            out.append(name)

    for name, exe in APPS.items():
        if name in t:
            try:
                subprocess.Popen(exe, shell=True)
                out.append(name)
            except Exception:
                pass

    return list(dict.fromkeys(out))

def recherche(texte, cible="pc"):
    t = norm(texte)

    m = re.search(r"cherche (.+?) sur youtube", t)
    if m:
        q = m.group(1).strip()
        ouvrir_url_cible("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q), cible)
        return f"Je cherche « {q} » sur YouTube."

    m = re.search(r"cherche (.+?) sur google", t)
    if m:
        q = m.group(1).strip()
        ouvrir_url_cible("https://www.google.com/search?q=" + urllib.parse.quote(q), cible)
        return f"Je cherche « {q} » sur Google."

    return None

def calcul(texte):
    t = norm(texte)
    t = t.replace("multiplie par", "*")
    t = t.replace("fois", "*")
    t = t.replace("plus", "+")
    t = t.replace("moins", "-")
    t = t.replace("divise par", "/")

    m = re.search(
        r"(?:calcule|calculer|fais le calcul|combien font|combien fait)\s+"
        r"([0-9.\+\-\*\/\(\)\s]+)",
        t
    )
    if not m:
        return None

    expr = m.group(1).strip()
    if not re.fullmatch(r"[0-9.\+\-\*\/\(\)\s]+", expr):
        return None

    try:
        result = eval(expr, {"__builtins__": {}}, {})
        return f"Le résultat est {result}."
    except Exception:
        return "Je n'ai pas réussi à calculer cette expression."

# ============================================================
# MÉTÉO & BOURSE
# ============================================================
# Aucune clé API nécessaire :
#   - Météo : Open-Meteo (geocoding + prévisions)
#   - Bourse : Stooq (cotations en léger différé)
# Nécessite une connexion Internet sur la machine qui exécute NOVA.

VILLE_PAR_DEFAUT = {"nom": "Grenoble", "lat": 45.1885, "lon": 5.7245}

METEO_CODES = {
    0: "ciel dégagé", 1: "plutôt dégagé", 2: "partiellement nuageux",
    3: "ciel couvert", 45: "brouillard", 48: "brouillard givrant",
    51: "bruine légère", 53: "bruine modérée", 55: "bruine dense",
    56: "bruine verglaçante", 57: "bruine verglaçante dense",
    61: "pluie légère", 63: "pluie modérée", 65: "forte pluie",
    66: "pluie verglaçante", 67: "forte pluie verglaçante",
    71: "neige légère", 73: "neige modérée", 75: "forte neige",
    77: "neige en grains", 80: "averses légères", 81: "averses modérées",
    82: "averses violentes", 85: "averses de neige légères",
    86: "averses de neige fortes", 95: "orage",
    96: "orage avec grêle légère", 99: "orage avec grêle forte",
}

BOURSE_SYMBOLES = {
    "cac40": ("CAC 40", "^FCHI"),
    "cac 40": ("CAC 40", "^FCHI"),
    "dow jones": ("Dow Jones", "^DJI"),
    "nasdaq": ("Nasdaq", "^IXIC"),
    "sp500": ("S&P 500", "^GSPC"),
    "s p 500": ("S&P 500", "^GSPC"),
    "bitcoin": ("Bitcoin", "BTC-USD"),
    "apple": ("Apple", "AAPL"),
    "tesla": ("Tesla", "TSLA"),
    "amazon": ("Amazon", "AMZN"),
    "google": ("Google", "GOOGL"),
    "microsoft": ("Microsoft", "MSFT"),
    "nvidia": ("Nvidia", "NVDA"),
    "meta": ("Meta", "META"),
    "netflix": ("Netflix", "NFLX"),
    "lvmh": ("LVMH", "MC.PA"),
    "totalenergies": ("TotalEnergies", "TTE.PA"),
}

# En-tête d'un vrai navigateur : nécessaire pour Yahoo Finance, sans quoi
# certaines requêtes sont silencieusement bloquées.
_ENTETES_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

def http_get_json(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers=_ENTETES_HTTP)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        print("🌐 Erreur réseau (météo/bourse) :", exc)
        return None

def http_get_text(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers=_ENTETES_HTTP)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print("🌐 Erreur réseau (bourse) :", exc)
        return None

def geocoder_ville(nom):
    data = http_get_json(
        "https://geocoding-api.open-meteo.com/v1/search?count=1&language=fr&name="
        + urllib.parse.quote(nom)
    )
    if not data or not data.get("results"):
        return None
    r = data["results"][0]
    return {
        "nom": r.get("name", nom).title(),
        "lat": r["latitude"],
        "lon": r["longitude"],
    }

def obtenir_meteo(ville_nom=None):
    if ville_nom:
        lieu = geocoder_ville(ville_nom)
        if not lieu:
            return None
    else:
        lieu = VILLE_PAR_DEFAUT

    url = (
        "https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
        "&current_weather=true&hourly=relativehumidity_2m&timezone=auto"
    ).format(lieu["lat"], lieu["lon"])

    data = http_get_json(url)
    if not data or "current_weather" not in data:
        return None

    cw = data["current_weather"]
    description = METEO_CODES.get(cw.get("weathercode", 0), "conditions variables")

    humidite = None
    try:
        heures = data["hourly"]["time"]
        idx = heures.index(cw["time"])
        humidite = data["hourly"]["relativehumidity_2m"][idx]
    except Exception:
        pass

    return {
        "ville": lieu["nom"],
        "temperature": cw.get("temperature"),
        "vent": cw.get("windspeed"),
        "description": description,
        "humidite": humidite,
        "heure": str(cw.get("time", "")).replace("T", " "),
    }

def extraire_ville_meteo(texte):
    t = norm(texte)

    # On cherche une préposition de lieu n'importe où APRÈS "meteo"/"temps",
    # même si d'autres mots s'intercalent (ex: "météo du jour à Beaujeu",
    # "il fait quel temps aujourd'hui a Lyon"). L'ancien code exigeait que
    # la préposition suive immédiatement "meteo"/"temps", ce qui ratait
    # la plupart des formulations et retombait donc sur Grenoble.
    m = re.search(
        r"(?:meteo|temps).*?\b(?:a|de|pour|dans|sur|vers)\s+"
        r"([a-z][a-z\-]*(?:\s+[a-z\-]+){0,3})\s*$",
        t
    )
    if m:
        ville = m.group(1).strip()
        ville = re.sub(
            r"\b(stp|s il te plait|merci|maintenant|steuplait|please)\b", "", ville
        ).strip()
        if ville:
            return ville
    return None

def formater_meteo(infos, ville_demandee=None):
    if not infos:
        cible = (ville_demandee or "la position par défaut").title()
        return (
            "// ERREUR DE LIAISON SATELLITE //\n\n"
            f"Impossible de récupérer les données météo pour {cible}.\n"
            "Nouvelle tentative automatique dans 90 secondes..."
        )

    lignes = [
        f"VILLE        : {infos['ville']}",
        f"TEMPÉRATURE  : {infos['temperature']} °C",
        f"CONDITIONS   : {infos['description']}",
        f"VENT         : {infos['vent']} km/h",
    ]
    if infos.get("humidite") is not None:
        lignes.append(f"HUMIDITÉ     : {infos['humidite']} %")
    lignes.append(f"DERNIÈRE MAJ : {infos.get('heure', '')}")
    lignes.append("")
    lignes.append("STATUT : DONNÉES CONFIRMÉES")
    return "\n".join(lignes)

def repondre_meteo(ville_nom=None):
    infos = obtenir_meteo(ville_nom)
    if not infos:
        cible = ville_nom or "cette position"
        return f"Je n'arrive pas à récupérer la météo pour {cible} en ce moment."

    # On arrondit toujours : dire "13 virgule 8 degrés" à l'oral est lourd,
    # un chiffre rond suffit largement pour de la météo.
    try:
        temperature = round(infos["temperature"])
    except Exception:
        temperature = infos["temperature"]
    try:
        vent = round(infos["vent"])
    except Exception:
        vent = infos["vent"]

    if MODE_RAPIDE:
        # Réponse courte : on saute la description détaillée et l'humidité.
        return f"Environ {temperature} degrés à {infos['ville']}, vent {vent} km/h."

    phrase = (
        f"À {infos['ville']}, il fait environ {temperature} degrés, "
        f"{infos['description']}, avec un vent d'environ {vent} kilomètres heure."
    )
    if infos.get("humidite") is not None:
        phrase += f" Humidité autour de {infos['humidite']} pour cent."
    return phrase

def obtenir_cotation(symbole):
    # API publique (non officielle) de Yahoo Finance : pas de clé requise,
    # contrairement à Stooq qui exige désormais un accès par CAPTCHA.
    # range=1d est nécessaire pour que Yahoo renvoie previousClose.
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbole)
        + "?range=1d&interval=5m"
    )
    data = http_get_json(url)
    if not data:
        return None

    try:
        resultat = data["chart"]["result"][0]
        meta = resultat["meta"]
        prix = meta.get("regularMarketPrice")
        cloture_veille = meta.get("previousClose") or meta.get("chartPreviousClose")
        devise = meta.get("currency", "") or ""
    except Exception:
        return None

    if prix is None:
        return None

    variation = None
    if cloture_veille:
        try:
            variation = (prix - cloture_veille) / cloture_veille * 100
        except Exception:
            variation = None

    return {
        "symbole": symbole,
        "prix": prix,
        "variation": variation,
        "devise": devise,
    }

def extraire_cible_bourse(texte):
    t = norm(texte)
    for cle in BOURSE_SYMBOLES:
        if cle in t:
            return cle
    m = re.search(r"bourse (?:de|pour|sur)\s+(.+)", t)
    if m:
        return m.group(1).strip()
    return None

def obtenir_bourse(cible=None):
    resultats = []

    if cible and cible in BOURSE_SYMBOLES:
        nom, symb = BOURSE_SYMBOLES[cible]
        cot = obtenir_cotation(symb)
        if cot:
            cot["nom"] = nom
            resultats.append(cot)

    elif cible:
        cot = obtenir_cotation(re.sub(r"\s+", "", cible).upper())
        if cot:
            cot["nom"] = cible.title()
            resultats.append(cot)

    else:
        for cle in ("cac40", "dow jones", "nasdaq", "sp500", "bitcoin"):
            nom, symb = BOURSE_SYMBOLES[cle]
            cot = obtenir_cotation(symb)
            if cot:
                cot["nom"] = nom
                resultats.append(cot)

    return resultats

def formater_bourse(resultats, cible=None):
    if not resultats:
        nom_cible = (cible or "les indices principaux").title()
        return (
            "// ERREUR DE LIAISON AUX MARCHÉS //\n\n"
            f"Impossible de récupérer les cours pour {nom_cible}.\n"
            "Nouvelle tentative automatique dans 90 secondes..."
        )

    corps = []
    for r in resultats:
        variation = r.get("variation")
        fleche = "▲" if (variation or 0) >= 0 else "▼"
        var_txt = f"{fleche} {variation:+.2f} %" if variation is not None else "—"
        devise = r.get("devise", "")
        corps.append(f"{r['nom']:<14} {r['prix']:>10.2f} {devise:<4} {var_txt}")

    entete = f"{'ACTIF':<14} {'COURS':>10}      VARIATION\n" + "─" * 44
    pied = "\n\nSTATUT : FLUX BOURSIER ACTIF (léger différé)"
    return entete + "\n" + "\n".join(corps) + pied

def repondre_bourse(cible=None):
    resultats = obtenir_bourse(cible)
    if not resultats:
        return "Je n'arrive pas à récupérer les cours de bourse pour le moment."

    phrases = []
    for r in resultats:
        if r.get("prix") is None:
            continue
        variation = r.get("variation")
        if variation is None:
            phrases.append(f"{r['nom']} à {r['prix']:.2f}")
        else:
            sens = "en hausse" if variation >= 0 else "en baisse"
            phrases.append(
                f"{r['nom']} à {r['prix']:.2f}, {sens} de {abs(variation):.2f} pour cent"
            )

    if not phrases:
        return "Je n'ai pas de données de bourse exploitables pour le moment."

    return "Voici les cours : " + ". ".join(phrases) + "."

# --- Fenêtres "agent secret" (style intel temps réel) ------------------

class FenetreIntel(Toplevel):
    """
    Fenêtre secondaire dans l'esprit d'un poste de surveillance :
    fond noir, texte vert monospace, voyant clignotant, données en direct.
    Elle ne fait AUCUN appel réseau elle-même : elle relit périodiquement
    (polling, depuis le thread principal Tk) un cache alimenté par un
    thread d'arrière-plan, exactement comme `refresh_chat` le fait déjà
    pour la conversation.
    """
    def __init__(self, master, titre, sous_titre, cache, lock):
        super().__init__(master)
        self.cache = cache
        self.lock = lock
        self.dernier_texte = None

        self.title("NOVA // " + titre)
        self.configure(bg="#000000")
        self.geometry("580x460")
        self.resizable(False, False)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        Label(
            self, text="🛰  N O V A   I N T E L", bg="#000000", fg="#39ff14",
            font=("Consolas", 17, "bold")
        ).pack(pady=(16, 2))

        self.sous_label = Label(
            self, text=sous_titre, bg="#000000", fg="#0aff9d",
            font=("Consolas", 9)
        )
        self.sous_label.pack()

        Label(
            self, text="─" * 68, bg="#000000", fg="#0e3b24",
            font=("Consolas", 8)
        ).pack(pady=(6, 4))

        self.zone = Text(
            self, bg="#000000", fg="#39ff14", insertbackground="#39ff14",
            font=("Consolas", 12), bd=0, highlightthickness=0, wrap="word"
        )
        self.zone.pack(fill="both", expand=True, padx=20, pady=6)
        self.zone.config(state="disabled")

        bas = Frame(self, bg="#000000")
        bas.pack(fill="x", pady=(0, 14))

        self.led = Label(
            bas, text="●", bg="#000000", fg="#39ff14",
            font=("Consolas", 12, "bold")
        )
        self.led.pack(side="left", padx=(20, 6))

        Label(
            bas, text="LIAISON EN DIRECT — NOVA INTEL SYSTEM", bg="#000000",
            fg="#0aff9d", font=("Consolas", 8, "bold")
        ).pack(side="left")

        self._clignote = True
        self._boucle_led()
        self._boucle_donnees()

    def _boucle_led(self):
        if not self.winfo_exists():
            return
        self._clignote = not self._clignote
        self.led.config(fg="#39ff14" if self._clignote else "#0e3b24")
        self.after(550, self._boucle_led)

    def _boucle_donnees(self):
        if not self.winfo_exists():
            return
        with self.lock:
            texte = self.cache.get("texte", "")
        if texte != self.dernier_texte:
            self.dernier_texte = texte
            self.zone.config(state="normal")
            self.zone.delete("1.0", END)
            self.zone.insert(END, texte)
            self.zone.config(state="disabled")
        self.after(1000, self._boucle_donnees)

class FenetreMeteo(FenetreIntel):
    """
    Fenêtre météo avec un champ de saisie : on peut changer la ville
    directement dans le module (Entrée ou bouton), sans repasser par
    la voix ni rouvrir une nouvelle fenêtre.
    """
    def __init__(self, master, ville_initiale=None):
        self.ville_actuelle = ville_initiale
        titre_ville = (ville_initiale or VILLE_PAR_DEFAUT["nom"]).title()

        super().__init__(
            master, "WEATHER INTEL",
            "MODULE MÉTÉO CLASSIFIÉ // " + titre_ville.upper(),
            meteo_cache, meteo_lock
        )
        self._ajouter_barre_ville()

    def _ajouter_barre_ville(self):
        barre = Frame(self, bg="#000000")
        barre.pack(fill="x", padx=20, pady=(0, 8), before=self.zone)

        Label(
            barre, text="VILLE :", bg="#000000", fg="#0aff9d",
            font=("Consolas", 9, "bold")
        ).pack(side="left")

        self.champ_ville = Entry(
            barre, bg="#031409", fg="#39ff14", insertbackground="#39ff14",
            font=("Consolas", 10), relief="flat",
            highlightthickness=1, highlightbackground="#0e3b24",
            highlightcolor="#39ff14"
        )
        self.champ_ville.pack(side="left", fill="x", expand=True, padx=8)
        if self.ville_actuelle:
            self.champ_ville.insert(0, self.ville_actuelle.title())
        self.champ_ville.bind("<Return>", lambda e: self._changer_ville())

        Button(
            barre, text="ACTUALISER", command=self._changer_ville,
            bg="#081827", fg="#39ff14", activebackground="#0c2235",
            activeforeground="#39ff14", relief="flat", bd=0,
            font=("Consolas", 8, "bold")
        ).pack(side="left")

    def _changer_ville(self):
        nouvelle = self.champ_ville.get().strip()
        self.ville_actuelle = nouvelle or None

        demarrer_suivi_meteo(self.ville_actuelle)

        titre_ville = (self.ville_actuelle or VILLE_PAR_DEFAUT["nom"]).title()
        self.sous_label.config(text="MODULE MÉTÉO CLASSIFIÉ // " + titre_ville.upper())
        # Force un réaffichage immédiat dès que le cache sera mis à jour,
        # sans attendre le prochain tick de polling.
        self.dernier_texte = None

def _boucle_maj_meteo(ville, jeton):
    while True:
        with meteo_lock:
            if meteo_cache.get("jeton") != jeton:
                return
        infos = obtenir_meteo(ville)
        with meteo_lock:
            if meteo_cache.get("jeton") != jeton:
                return
            meteo_cache["texte"] = formater_meteo(infos, ville)
        time.sleep(90)

def demarrer_suivi_meteo(ville):
    """
    (Re)lance le suivi météo pour une ville donnée : invalide le suivi
    précédent (via le jeton) et démarre un nouveau thread d'arrière-plan.
    Utilisé à l'ouverture du module, et quand on change de ville depuis
    le champ de saisie de la fenêtre.
    """
    jeton = time.time()
    with meteo_lock:
        meteo_cache["jeton"] = jeton
        meteo_cache["texte"] = "Connexion au satellite météo...\nRécupération des données en cours."

    threading.Thread(
        target=_boucle_maj_meteo, args=(ville, jeton), daemon=True
    ).start()
    return jeton

def ouvrir_fenetre_meteo(ville=None):
    demarrer_suivi_meteo(ville)

    def creer():
        if not NOVA_UI or not getattr(NOVA_UI, "root", None):
            return
        FenetreMeteo(NOVA_UI.root, ville)

    if NOVA_UI and getattr(NOVA_UI, "root", None):
        NOVA_UI.root.after(0, creer)

def _boucle_maj_bourse(cible, jeton):
    while True:
        with bourse_lock:
            if bourse_cache.get("jeton") != jeton:
                return
        resultats = obtenir_bourse(cible)
        with bourse_lock:
            if bourse_cache.get("jeton") != jeton:
                return
            bourse_cache["texte"] = formater_bourse(resultats, cible)
        time.sleep(90)

def ouvrir_fenetre_bourse(cible=None):
    jeton = time.time()
    with bourse_lock:
        bourse_cache["jeton"] = jeton
        bourse_cache["texte"] = "Connexion aux marchés financiers...\nRécupération des cours en cours."

    threading.Thread(
        target=_boucle_maj_bourse, args=(cible, jeton), daemon=True
    ).start()

    def creer():
        if not NOVA_UI or not getattr(NOVA_UI, "root", None):
            return
        sous_titre = (
            "MODULE BOURSE CLASSIFIÉ // " + cible.upper()
            if cible else
            "MODULE BOURSE CLASSIFIÉ // INDICES PRINCIPAUX"
        )
        FenetreIntel(
            NOVA_UI.root, "MARKET INTEL",
            sous_titre, bourse_cache, bourse_lock
        )

    if NOVA_UI and getattr(NOVA_UI, "root", None):
        NOVA_UI.root.after(0, creer)

# ============================================================
# VOIX
# ============================================================

class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]

class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]

winmm = ctypes.windll.winmm if os.name == "nt" else None

# Prototypes WinMM explicites : important avec les handles Windows en 64 bits.
if winmm:
    winmm.waveInGetNumDevs.argtypes = []
    winmm.waveInGetNumDevs.restype = wintypes.UINT

    winmm.waveInOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.UINT,
        ctypes.POINTER(WAVEFORMATEX),
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    winmm.waveInOpen.restype = wintypes.UINT

    for _name in (
        "waveInPrepareHeader",
        "waveInUnprepareHeader",
        "waveInAddBuffer",
    ):
        _fn = getattr(winmm, _name)
        _fn.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WAVEHDR),
            wintypes.UINT,
        ]
        _fn.restype = wintypes.UINT

    winmm.waveInStart.argtypes = [ctypes.c_void_p]
    winmm.waveInStart.restype = wintypes.UINT

    winmm.waveInStop.argtypes = [ctypes.c_void_p]
    winmm.waveInStop.restype = wintypes.UINT

    winmm.waveInReset.argtypes = [ctypes.c_void_p]
    winmm.waveInReset.restype = wintypes.UINT

    winmm.waveInClose.argtypes = [ctypes.c_void_p]
    winmm.waveInClose.restype = wintypes.UINT


def record_wav(path, duration=None, rate=16000):
    if duration is None:
        duration = 3.2 if MODE_RAPIDE else 4.5

    if not winmm:
        print("🎤 Micro : WinMM indisponible (Windows requis).")
        return False

    try:
        devices = int(winmm.waveInGetNumDevs())
    except Exception as exc:
        print("🎤 Impossible de lire les microphones :", exc)
        return False

    if devices <= 0:
        print("🎤 Aucun microphone Windows détecté.")
        return False

    # WAVE_MAPPER = 0xFFFFFFFF
    WAVE_MAPPER = 0xFFFFFFFF
    h = ctypes.c_void_p()
    fmt = WAVEFORMATEX(
        1,          # PCM
        1,          # mono
        rate,
        rate * 2,
        2,
        16,
        0
    )

    result = winmm.waveInOpen(
        ctypes.byref(h),
        WAVE_MAPPER,
        ctypes.byref(fmt),
        0,
        0,
        0
    )

    if result != 0 or not h.value:
        print(f"🎤 Impossible d'ouvrir le micro (code WinMM {result}).")
        return False

    size = int(rate * duration * 2)
    buf = ctypes.create_string_buffer(size)
    hdr = WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
    hdr.dwBufferLength = size
    hdr.dwBytesRecorded = 0
    hdr.dwFlags = 0
    hdr.dwLoops = 0

    prepared = False
    started = False

    try:
        result = winmm.waveInPrepareHeader(
            h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
        )
        if result != 0:
            print(f"🎤 Préparation micro échouée ({result}).")
            return False
        prepared = True

        result = winmm.waveInAddBuffer(
            h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
        )
        if result != 0:
            print(f"🎤 Buffer micro refusé ({result}).")
            return False

        result = winmm.waveInStart(h)
        if result != 0:
            print(f"🎤 Démarrage micro échoué ({result}).")
            return False
        started = True

        print(
            f"🎤 Écoute pendant {duration:.1f}s"
            + (" [MODE RAPIDE]" if MODE_RAPIDE else "")
            + "..."
        )

        deadline = time.time() + duration
        while time.time() < deadline:
            if audio_stop.is_set():
                break
            time.sleep(0.03)

        # Demande l'arrêt propre de l'enregistrement.
        winmm.waveInStop(h)
        started = False

        # WHDR_DONE = 0x00000001
        for _ in range(100):
            if hdr.dwFlags & 1:
                break
            time.sleep(0.02)

        recorded = int(hdr.dwBytesRecorded)

        import wave
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(buf.raw[:recorded])

        if recorded <= 100:
            print("🎤 Micro : aucun son exploitable enregistré.")
            return False

        print(f"🎤 Audio capturé : {recorded / 2 / rate:.2f}s")
        return True

    except Exception as exc:
        print("🎤 Erreur d'enregistrement :", repr(exc))
        return False

    finally:
        try:
            if started:
                winmm.waveInStop(h)
        except Exception:
            pass

        try:
            winmm.waveInReset(h)
        except Exception:
            pass

        if prepared:
            try:
                winmm.waveInUnprepareHeader(
                    h, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                )
            except Exception:
                pass

        try:
            winmm.waveInClose(h)
        except Exception:
            pass

def ecouter():
    path = os.path.join(
        os.environ.get("TEMP", "."),
        "nova_mic.wav"
    )

    set_etat("ECOUTE")

    if not record_wav(path):
        set_etat("VEILLE")
        return None

    try:
        recognizer = sr.Recognizer()

        # Réglages plus tolérants. La capture reste identique.
        recognizer.energy_threshold = 180
        recognizer.dynamic_energy_threshold = True
        recognizer.dynamic_energy_adjustment_damping = 0.10
        recognizer.dynamic_energy_adjustment_ratio = 1.35
        recognizer.pause_threshold = 0.75
        recognizer.phrase_threshold = 0.20
        recognizer.non_speaking_duration = 0.45

        with sr.AudioFile(path) as source:
            audio = recognizer.record(source)

        texte, alternatives = essayer_reconnaissance(recognizer, audio)

        if not texte:
            print("🗣️ Google n'a pas fourni de transcription pour cet enregistrement.")
            return None

        print("🗣️ Entendu :", texte)

        # Montre les alternatives uniquement lorsqu'elles existent.
        if len(alternatives) > 1:
            print("🧠 Alternatives :", " | ".join(alternatives[:3]))

        corrige = corriger_transcription(texte)

        if corrige != norm(texte):
            print("🧠 Compréhension tolérante :", corrige)

        return corrige

    finally:
        set_etat("VEILLE")


async def _tts(text, filename):
    await edge_tts.Communicate(
        text,
        "fr-FR-DeniseNeural"
    ).save(filename)

def parler(text):
    global voice_busy

    if not text:
        return

    with voice_lock:
        voice_busy = True

    set_etat("PARLE")
    addconv("NOVA", text)
    print("🔊 NOVA :", text)

    try:
        filename = os.path.join(
            os.environ.get("TEMP", "."),
            "nova_voice.mp3"
        )

        asyncio.run(_tts(text, filename))

        ps = f"""
Add-Type -AssemblyName presentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([uri]'{filename}')
Start-Sleep -Milliseconds 400
while(-not $p.NaturalDuration.HasTimeSpan) {{
    Start-Sleep -Milliseconds 100
}}
$p.Play()
Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 300)
$p.Close()
"""

        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60
        )

    except Exception as exc:
        print("TTS Edge :", repr(exc))
        try:
            # Secours Windows SAPI. Cela évite que NOVA reste muette si
            # Edge TTS ou le lecteur MP3 rencontre un problème.
            safe = text.replace("'", "''")
            ps_fallback = (
                "Add-Type -AssemblyName System.Speech; "
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Speak('{safe}'); "
                "$s.Dispose()"
            )
            subprocess.run(
                [
                    "powershell", "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-Command", ps_fallback
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30
            )
        except Exception as fallback_exc:
            print("TTS Windows secours :", repr(fallback_exc))
            print("NOVA :", text)

    finally:
        with voice_lock:
            voice_busy = False
        set_etat("VEILLE")

# ============================================================
# CONVERSATION
# ============================================================

# ============================================================
# MINI MOTEUR CONVERSATIONNEL INTELLIGENT
# ============================================================
# Ce module reste local et léger :
# - détecte le type de début de conversation
# - extrait un sujet simple
# - garde le dernier sujet / l'attente
# - choisit une relance adaptée
# - évite les phrases génériques du type "je te suis"

MOTS_VIDES_SUJET = {
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "un", "une", "des", "le", "la", "les", "de", "du", "des", "a", "au",
    "aux", "et", "ou", "mais", "donc", "que", "qui", "quoi", "cest",
    "est", "suis", "es", "sont", "avoir", "faire", "fais", "fait",
    "avec", "dans", "pour", "sur", "par", "mon", "ma", "mes", "ton",
    "ta", "tes", "ce", "ça", "ca", "cet", "cette", "ces", "en", "comme",
    "plus", "moins", "très", "tres", "juste", "bien", "pas", "aussi",
    "encore", "maintenant", "aujourd", "hui", "là", "la", "ici",
    "nova", "vas", "veux", "peux", "peut", "dois", "voudrais"
}

RELANCES_INTELLIGENTES = {
    "idee": [
        "Ah, tu as une idée ? Raconte-moi ce que tu imagines.",
        "Intéressant. C'est quoi ton idée ?",
        "Vas-y, je veux comprendre ton idée.",
        "Tu viens d'avoir une idée ? Explique-moi.",
        "Ça m'intrigue. Quelle est ton idée ?",
        "D'accord. Décris-moi ton idée comme tu la vois.",
        "Je t'écoute. Quel est le principe de ton idée ?",
        "Une idée pour quoi exactement ?",
        "Tu veux me présenter ton idée ?",
        "Raconte-moi le début, je te suis.",
        "Je veux bien l'entendre. Qu'est-ce que tu veux créer ?",
        "Quelle est la partie la plus intéressante de ton idée ?",
        "Tu imagines quoi précisément ?",
        "Commence par me dire le résultat que tu voudrais obtenir.",
        "D'accord. Qu'est-ce qui t'a donné cette idée ?",
        "Ton idée concerne quoi ?",
        "Je suis curieuse. Comment tu l'imagines ?",
        "Tu veux partir sur quelle direction ?",
        "Donne-moi les grandes lignes de ton idée.",
        "Très bien. Quel serait le premier élément de ton projet ?",
        "Je t'écoute. Qu'est-ce que tu veux faire différemment ?",
        "Ça commence bien. Explique-moi ton concept.",
        "Qu'est-ce que tu voudrais que cette idée fasse ?",
        "D'accord. Quel problème ton idée cherche à résoudre ?",
        "Tu veux me donner un exemple de ton idée ?",
        "Je peux t'aider à la développer. Commence par me la décrire.",
        "C'est une idée de projet, de jeu, d'appli ou autre chose ?",
        "Quel est le but de ton idée ?",
        "D'accord. Tu veux qu'on transforme cette idée en quelque chose de concret ?",
        "Explique-moi ce que tu as en tête.",
        "Je veux bien creuser. Quelle partie veux-tu commencer ?",
        "Ton idée vient de quelque chose que tu as vu ?",
        "Qu'est-ce que tu voudrais construire à partir de cette idée ?",
        "Décris-moi la version idéale de ton idée.",
        "Vas-y, je suis prête à l'entendre.",
    ],
    "probleme": [
        "D'accord. Qu'est-ce qui ne fonctionne pas exactement ?",
        "Je peux t'aider à chercher la cause. Qu'est-ce que tu observes ?",
        "Explique-moi le problème comme tu le vois.",
        "Ça bloque à quel moment ?",
        "Quel résultat tu obtiens à la place de celui attendu ?",
        "Tu as un message d'erreur ou c'est plutôt un comportement bizarre ?",
        "Qu'est-ce que tu voulais obtenir ?",
        "On peut le diagnostiquer ensemble. Quelle est la première chose qui déconne ?",
        "Tu peux me donner un exemple concret ?",
        "D'accord. Depuis quand ça fait ça ?",
        "Quelle partie marche encore correctement ?",
        "Est-ce que le problème arrive toujours ou seulement parfois ?",
        "Qu'est-ce que tu as déjà essayé ?",
        "On peut isoler le problème étape par étape.",
        "Quel est le symptôme principal ?",
        "D'accord. Montre-moi ce qui bloque.",
        "Tu veux qu'on cherche d'abord la cause ou une solution rapide ?",
        "Qu'est-ce qui a changé juste avant que ça commence ?",
        "Le problème concerne le code, l'audio ou l'interface ?",
        "Je vais rester précise. Quelle partie pose souci ?",
        "D'accord. Décris-moi le résultat actuel.",
        "Est-ce que tu as une erreur précise sous les yeux ?",
        "Quel est le comportement attendu ?",
        "Qu'est-ce qui se passe réellement ?",
        "On va réduire le problème à une petite partie.",
        "Donne-moi les dernières étapes avant le bug.",
        "D'accord. Tu veux me montrer le message d'erreur ?",
        "Je peux t'aider à reproduire le problème.",
        "Quel élément te gêne le plus ?",
        "Ça a commencé après quelle modification ?",
        "Tu veux qu'on commence par vérifier le fonctionnement de base ?",
        "Très bien. On cherche d'abord ce qui casse.",
        "Je t'écoute. Quelle est la première anomalie ?",
        "D'accord. On peut avancer morceau par morceau.",
        "Dis-moi exactement ce qui ne va pas.",
    ],
    "projet": [
        "Sur ton projet, tu veux améliorer quoi en premier ?",
        "Quel est l'objectif de ta prochaine version ?",
        "Tu travailles actuellement sur quelle partie ?",
        "Quelle fonction tu aimerais ajouter ensuite ?",
        "Tu veux plutôt améliorer l'interface ou le fonctionnement ?",
        "Qu'est-ce qui te ferait dire que la nouvelle version est réussie ?",
        "Tu es bloqué sur quelle étape ?",
        "Quelle est la prochaine fonctionnalité que tu imagines ?",
        "Tu veux me décrire l'état actuel du projet ?",
        "Qu'est-ce qui manque encore à ton projet ?",
        "Tu veux qu'on organise les prochaines étapes ?",
        "Quel est le point le plus important pour toi ?",
        "Tu préfères corriger un problème ou ajouter une nouveauté ?",
        "Qu'est-ce que tu voudrais que le projet sache faire ensuite ?",
        "Tu veux me montrer la partie sur laquelle tu travailles ?",
        "Quel résultat tu veux obtenir à l'écran ?",
        "Tu veux rendre le projet plus rapide, plus joli ou plus pratique ?",
        "Quelle partie tu voudrais rendre plus intelligente ?",
        "Tu as déjà une idée de la suite ?",
        "Qu'est-ce qui est déjà terminé ?",
        "D'accord. Quel est le prochain objectif ?",
        "Tu veux qu'on transforme une idée en fonction concrète ?",
        "Quelle partie te semble la plus difficile actuellement ?",
        "Tu veux travailler sur la voix, la conversation ou l'interface ?",
        "Quel est le principal changement que tu veux apporter ?",
        "On peut découper ton projet en étapes. Tu veux commencer par laquelle ?",
        "Tu veux me décrire la version idéale de ton projet ?",
        "Quelle fonctionnalité serait la plus utile selon toi ?",
        "D'accord. Qu'est-ce que tu veux améliorer aujourd'hui ?",
        "Tu veux qu'on construise la prochaine étape ensemble ?",
        "Quel résultat tu aimerais voir après notre modification ?",
        "Tu as un objectif précis pour cette version ?",
        "Tu veux me donner la priorité numéro un ?",
        "Je peux suivre ton projet. Quelle est la prochaine tâche ?",
        "On commence par quoi ?",
    ],
    "humeur": [
        "Tu veux m'expliquer ce qui s'est passé ?",
        "D'accord. Ta journée ressemble à quoi ?",
        "Tu veux en parler un peu ?",
        "Qu'est-ce qui te fait dire ça ?",
        "Je t'écoute. Qu'est-ce qui s'est passé aujourd'hui ?",
        "Tu préfères en parler ou changer complètement de sujet ?",
        "Qu'est-ce qui t'a marqué dans ta journée ?",
        "Tu veux me raconter le début ?",
        "D'accord. Qu'est-ce qui t'occupe l'esprit ?",
        "Tu as envie de parler de quoi là-dessus ?",
        "Je peux rester avec toi sur ce sujet. Qu'est-ce qu'il s'est passé ?",
        "Tu veux juste en parler ou chercher une solution ?",
        "Qu'est-ce qui a rendu ta journée comme ça ?",
        "Tu veux me raconter ce qui t'a le plus marqué ?",
        "D'accord. Quelle partie veux-tu me raconter ?",
        "Tu veux que je t'écoute ou que je t'aide à réfléchir ?",
        "Qu'est-ce qui t'a fait réagir comme ça ?",
        "Je suis là. Tu veux développer ?",
        "Tu peux me raconter à ton rythme.",
        "Qu'est-ce qui s'est passé ensuite ?",
        "D'accord. Tu veux commencer par le plus important ?",
        "Quel a été le moment le plus marquant ?",
        "Tu veux me donner un peu de contexte ?",
        "Je t'écoute. Qu'est-ce qui te préoccupe ?",
        "Tu veux qu'on reste sur ce sujet ?",
        "D'accord. Qu'est-ce que tu aurais voulu à la place ?",
        "Tu veux qu'on essaie de comprendre ce qui s'est passé ?",
        "Je peux t'aider à mettre les choses au clair.",
        "Tu veux me raconter ce qui t'a fait ressentir ça ?",
        "Je t'écoute. Quelle est la suite ?",
        "D'accord. Qu'est-ce qui compte le plus pour toi là-dedans ?",
        "Tu veux juste vider ton sac ou qu'on cherche une piste ?",
        "Je comprends. Tu veux continuer ?",
        "Tu peux me dire ce qui s'est passé.",
        "Je suis là. Raconte-moi.",
    ],
    "question_ouverte": [
        "Bonne question. Qu'est-ce que tu veux savoir exactement ?",
        "Je peux regarder ça avec toi. Quel point t'intéresse le plus ?",
        "D'accord. Tu veux une réponse rapide ou qu'on détaille ?",
        "Précise-moi juste le sujet et je m'adapte.",
        "Je peux te répondre. Qu'est-ce que tu cherches à comprendre ?",
        "Tu veux un exemple concret ?",
        "Quel est le contexte de ta question ?",
        "Je peux creuser ça avec toi.",
        "D'accord. Quelle partie te pose question ?",
        "Je t'écoute. Développe un peu ta question.",
        "Tu veux qu'on parte du début ?",
        "Je peux te l'expliquer simplement. Quel aspect t'intéresse ?",
        "D'accord. Qu'est-ce que tu veux obtenir comme réponse ?",
        "On peut prendre la question étape par étape.",
        "Tu veux plutôt comprendre le pourquoi ou le comment ?",
        "Je peux te donner plusieurs pistes.",
        "Quel détail voudrais-tu éclaircir ?",
        "Tu veux que je fasse simple ou plus technique ?",
        "D'accord. Donne-moi juste un peu plus de contexte.",
        "Je peux continuer à partir de ta question.",
        "Quel est le point précis que tu veux résoudre ?",
        "Je t'écoute. Qu'est-ce que tu cherches ?",
        "On peut commencer par l'essentiel.",
        "Tu veux que je reformule le sujet avant de répondre ?",
        "D'accord. Donne-moi un exemple si tu en as un.",
        "Je peux t'aider à cadrer la question.",
        "Quel résultat tu aimerais obtenir ?",
        "Je vois. Qu'est-ce qui t'intéresse derrière cette question ?",
        "Tu veux qu'on compare plusieurs possibilités ?",
        "Je peux partir de ce que tu viens de dire.",
        "D'accord. On commence par quelle partie ?",
        "Tu veux que je te fasse une réponse très directe ?",
        "Je peux développer le sujet avec toi.",
        "Pose-moi la suite.",
        "Je t'écoute, continue ta question.",
    ],
    "recit": [
        "Vas-y, raconte-moi la suite.",
        "Qu'est-ce qui s'est passé après ?",
        "Je t'écoute. Et ensuite ?",
        "D'accord. Continue, je veux comprendre.",
        "Et là, qu'est-ce que tu as fait ?",
        "Je vois. Comment ça s'est terminé ?",
        "Raconte-moi le passage suivant.",
        "Et après, il s'est passé quoi ?",
        "D'accord. Quel a été le moment important ?",
        "Je suis attentive. Continue ton histoire.",
        "Qu'est-ce qui est arrivé ensuite ?",
        "Je te suis sur l'histoire. Et puis ?",
        "D'accord. Tu peux continuer.",
        "Et toi, tu as réagi comment ?",
        "Je veux bien savoir la suite.",
        "Qu'est-ce qui t'a le plus surpris ?",
        "Et finalement, ça a donné quoi ?",
        "Continue, j'ai besoin du contexte.",
        "D'accord. Puis il s'est passé quoi ?",
        "Je t'écoute. Quelle est la suite ?",
        "Tu peux reprendre juste après ce moment.",
        "Et ensuite, comment ça a évolué ?",
        "Je vois. Qu'est-ce que tu as fait après ?",
        "Continue, raconte-moi.",
        "D'accord. Et à la fin ?",
        "Quel a été le résultat ?",
        "Je t'écoute toujours. Continue.",
        "Et là, quelle décision as-tu prise ?",
        "Je veux bien entendre la suite.",
        "D'accord. Qu'est-ce qui s'est passé juste après ?",
        "Continue à partir de ce point.",
        "Et ensuite ?",
        "Je suis là, raconte.",
        "D'accord. J'attends la suite.",
    ],
}

def extrait_sujet_conversation(text):
    t = norm(text)
    # On privilégie ce qui suit quelques formulations naturelles.
    patterns = [
        r"\bje (?:veux|voudrais|vais|dois) (?:parler|te parler) de (.+)",
        r"\bje pense a (.+)",
        r"\bj ai une idee (?:sur|pour|de)?\s*(.+)?",
        r"\bmon projet (?:c est|est|concerne)?\s*(.+)?",
        r"\bje travaille sur (.+)",
        r"\bje parle de (.+)",
    ]

    candidate = ""
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            candidate = (m.group(1) or "").strip()
            break

    if not candidate:
        mots = re.findall(r"[a-z0-9]{3,}", t)
        utiles = [m for m in mots if m not in MOTS_VIDES_SUJET]
        candidate = " ".join(utiles[-6:])

    candidate = re.sub(r"\s+", " ", candidate).strip(" .,!?")
    return candidate[:100]

def detecter_intention_intelligente(text):
    t = norm(text)

    if any(x in t for x in [
        "j ai une idee", "j'ai une idée", "j ai une idee",
        "j ai une nouvelle idee", "j'ai une nouvelle idée"
    ]):
        return "idee"

    if any(x in t for x in [
        "j ai un probleme", "j'ai un problème", "ca marche pas",
        "ça marche pas", "je suis bloque", "je suis bloqué",
        "je n arrive pas", "je n'arrive pas", "y a un bug",
        "il y a un bug", "bug"
    ]):
        return "probleme"

    if any(x in t for x in [
        "mon projet", "je travaille sur", "je developpe",
        "je développe", "ma nouvelle version", "mon programme"
    ]):
        return "projet"

    if any(x in t for x in [
        "je suis fatigue", "je suis fatigué", "je suis creve",
        "je suis crevé", "je vais mal", "je suis pas bien",
        "je suis content", "je suis contente", "je suis enerve",
        "je suis énervé", "ma journee", "ma journée"
    ]):
        return "humeur"

    if any(x in t for x in [
        "ecoute", "écoute", "tu sais quoi", "devine", "attends",
        "j ai quelque chose", "j'ai quelque chose", "faut que je te dise",
        "il faut que je te dise", "je voulais te dire"
    ]):
        return "recit"

    if "?" in str(text) or any(x in t for x in [
        "comment", "pourquoi", "quand", "ou ", "où", "où",
        "quel", "quelle", "qui", "combien", "peux tu", "peux-tu",
        "est ce que"
    ]):
        return "question_ouverte"

    # Une phrase assez longue sans intention connue ressemble davantage
    # à un début de récit qu'à une commande système.
    if len(t.split()) >= 5:
        return "recit"

    return ""


# ============================================================
# SMART PLUS — MEMOIRE ET RELANCES CONTEXTUELLES
# ============================================================

smart_state = {
    "dernier_intent": "",
    "dernier_sujet": "",
    "derniere_question_nova": "",
    "tour": 0,
}
smart_state_lock = threading.Lock()


def smart_dernier_message_nova():
    with conv_lock:
        for role, msg in reversed(conversation):
            if role == "NOVA":
                return str(msg)
    return ""


def smart_question_actuelle():
    msg = smart_dernier_message_nova()
    if "?" not in msg:
        return ""
    morceaux = re.split(r"[.!]\s+|\n+", msg)
    for morceau in reversed(morceaux):
        if "?" in morceau:
            return morceau.strip()
    return ""


def smart_sujet_actuel():
    with smart_state_lock:
        return str(smart_state.get("dernier_sujet", "")).strip()


def smart_relance_courte(text):
    """
    Comprend les réponses de type:
      oui / non / et après / explique / comment / pourquoi
    en regardant la question précédente de NOVA.
    """
    t = norm(text)
    question = smart_question_actuelle().lower()
    sujet = smart_sujet_actuel()

    if not question and not sujet:
        return None

    if t in {"oui", "ouais", "yes", "ok", "okay", "d accord", "exact", "exactement"}:
        if "projet" in question or "projet" in sujet:
            return choisir_reponse([
                "Parfait. Tu veux commencer par quelle partie du projet ?",
                "D'accord. On attaque quoi en premier ?",
                "Très bien. Tu veux travailler sur quelle partie ?",
                "Ok. On commence par l'interface, la voix ou la logique ?",
                "Ça marche. Quelle fonction tu veux ajouter ensuite ?",
                "Parfait. Quel est ton objectif numéro un ?",
                "D'accord. Qu'est-ce qu'on construit en premier ?",
                "Très bien. Quelle est la prochaine étape ?",
                "Ok. Tu veux corriger quelque chose ou ajouter une fonction ?",
                "D'accord. Quelle amélioration tu veux voir maintenant ?",
            ])
        return choisir_reponse([
            "D'accord. Tu veux développer quel point ?",
            "Parfait. Qu'est-ce que tu veux faire ensuite ?",
            "Très bien. On continue sur quoi ?",
            "Ok. Quelle est la prochaine étape ?",
            "D'accord. Tu veux que je développe le sujet ?",
            "Parfait. Quel détail tu veux préciser ?",
            "Très bien. Quelle partie on approfondit ?",
            "Ça marche. Tu veux aller plus loin sur quoi ?",
            "D'accord. Quelle suite tu imagines ?",
            "Très bien. Continue, je m'adapte à ce que tu veux.",
        ])

    if t in {"non", "nan", "pas vraiment", "pas du tout"}:
        return choisir_reponse([
            "D'accord. Alors on change de direction. Tu veux parler de quoi ?",
            "Très bien. On peut passer à autre chose.",
            "Compris. Tu préfères qu'on fasse quoi maintenant ?",
            "Pas de problème. Donne-moi un autre sujet.",
            "D'accord. On laisse ça de côté. Quelle est la suite ?",
            "Très bien. On repart sur une autre idée.",
            "Compris. Tu veux changer complètement de sujet ?",
            "Ça marche. Quelle piste tu veux prendre à la place ?",
            "D'accord. Qu'est-ce que tu veux faire maintenant ?",
            "Très bien. On passe à autre chose.",
        ])

    if t in {"et apres", "apres", "ensuite", "et ensuite", "continue", "vas y", "vas-y", "explique"}:
        return choisir_reponse([
            "On peut continuer. Tu veux le détail ou directement la partie pratique ?",
            "D'accord. Je développe la suite.",
            "Je peux poursuivre à partir du dernier point.",
            "Très bien. Tu veux un exemple concret ?",
            "On continue. Quelle partie tu veux approfondir ?",
            "D'accord. Je passe à l'étape suivante.",
            "Je peux aller plus loin sur ce sujet.",
            "Très bien. Tu veux que je te montre comment ?",
            "D'accord. On garde ce sujet et on avance.",
            "Je continue. Dis-moi juste ce que tu veux préciser.",
        ])

    if t.startswith("pourquoi"):
        return choisir_reponse([
            "Je peux t'expliquer la raison principale puis le reste.",
            "Il y a plusieurs raisons. Tu veux la principale ?",
            "Ça dépend du contexte. On peut regarder ton cas précis.",
            "Je peux te l'expliquer simplement.",
            "C'est lié au fonctionnement du système.",
            "Je peux te montrer le lien avec ce qu'on vient de dire.",
            "D'accord. Tu veux le pourquoi technique ou pratique ?",
            "Je peux détailler ça étape par étape.",
            "Bonne question. On peut regarder la cause ensemble.",
            "Je vais partir du point précédent pour l'expliquer.",
        ])

    if t.startswith("comment"):
        return choisir_reponse([
            "On peut le faire étape par étape.",
            "Je peux te montrer la méthode la plus simple.",
            "Ça dépend de l'objectif exact. Qu'est-ce que tu veux obtenir ?",
            "Je peux te guider du début à la fin.",
            "On peut découper ça en petites étapes.",
            "D'accord. Tu veux une explication simple ou technique ?",
            "Je peux partir de ce qu'on a déjà construit.",
            "On peut d'abord regarder ce qui existe puis améliorer.",
            "Je peux te proposer une méthode concrète.",
            "Donne-moi le résultat voulu et je te guide.",
        ])

    return None


def smart_debut_conversation(text):
    """
    Cherche un vrai début de discussion avant les réponses génériques.
    """
    t = norm(text)
    sujet = extrait_sujet_conversation(text)

    with smart_state_lock:
        smart_state["tour"] += 1
        tour = smart_state["tour"]
        if sujet:
            smart_state["dernier_sujet"] = sujet

    if any(x in t for x in [
        "j ai une idee", "j'ai une idee", "j ai une nouvelle idee",
        "j'ai une nouvelle idée"
    ]):
        return choisir_reponse([
            "Ah, tu as une idée ? Raconte-moi ce que tu imagines.",
            "Intéressant. C'est quoi ton idée ?",
            "Vas-y, explique-moi ton idée.",
            "Tu viens d'avoir une idée ? Je veux bien l'entendre.",
            "Ça m'intrigue. Quelle est ton idée ?",
            "D'accord. Décris-moi ton idée comme tu la vois.",
            "Je t'écoute. Quel est le principe ?",
            "Une idée pour quel projet ?",
            "Tu veux me présenter ton idée ?",
            "Commence par me dire le résultat que tu voudrais.",
        ])

    if any(x in t for x in [
        "j ai un probleme", "j'ai un probleme", "ca marche pas",
        "ça marche pas", "je suis bloque", "je suis bloqué",
        "y a un bug", "il y a un bug"
    ]):
        return choisir_reponse([
            "D'accord. Qu'est-ce qui ne fonctionne pas exactement ?",
            "Je peux t'aider à chercher la cause. Qu'est-ce que tu observes ?",
            "Explique-moi le problème comme tu le vois.",
            "Ça bloque à quel moment ?",
            "Quel résultat tu obtiens à la place de celui attendu ?",
            "Tu as un message d'erreur précis ?",
            "Qu'est-ce que tu voulais obtenir ?",
            "On peut le diagnostiquer ensemble. Quelle est la première chose qui bloque ?",
            "Tu peux me donner un exemple concret ?",
            "D'accord. Depuis quand ça fait ça ?",
        ])

    if any(x in t for x in [
        "j ai besoin de", "j'ai besoin de", "j aurais besoin de",
        "il me faut", "j ai besoin d aide", "j'ai besoin d'aide"
    ]):
        return choisir_reponse([
            "D'accord. De quoi as-tu besoin exactement ?",
            "Je peux t'aider. Quelle est la tâche ?",
            "Très bien. Quel résultat tu veux obtenir ?",
            "Explique-moi ce dont tu as besoin.",
            "D'accord. Qu'est-ce qu'on doit faire en premier ?",
            "Je suis prête. Quelle partie te pose problème ?",
            "Donne-moi ton objectif et je cadre la suite.",
            "Tu veux une explication, une action ou les deux ?",
            "D'accord. Quelle est ta demande précise ?",
            "Je t'écoute. Dis-moi ce qu'il te faut.",
        ])

    # Les phrases assez longues qui ne sont ni des commandes ni des questions
    # doivent recevoir une relance sur le contenu, pas "je te suis".
    if len(t.split()) >= 6 and not any(x in t for x in [
        "ouvre", "lance", "cherche", "calcule", "meteo", "bourse",
        "pause", "arrete", "quitte"
    ]):
        if sujet:
            return choisir_reponse([
                f"Je vois que tu parles de « {sujet} ». Qu'est-ce que tu veux en faire ?",
                f"Pour « {sujet} », quel est ton objectif ?",
                f"Tu veux qu'on développe « {sujet} » ensemble ?",
                f"J'ai le sujet « {sujet} ». Qu'est-ce que tu voudrais améliorer ?",
                f"On part donc sur « {sujet} ». Quelle est la prochaine étape ?",
                f"Tu veux faire évoluer « {sujet} » dans quelle direction ?",
                f"Quel résultat tu voudrais obtenir avec « {sujet} » ?",
                f"Je peux t'aider sur « {sujet} ». Par quoi on commence ?",
                f"Qu'est-ce qui est le plus important pour « {sujet} » ?",
                f"Tu veux qu'on creuse quelle partie de « {sujet} » ?",
            ])
        return choisir_reponse([
            "Je vois l'idée générale. Quel est ton objectif précis ?",
            "Je peux réfléchir avec toi. Quel résultat tu veux obtenir ?",
            "D'accord. Quelle partie tu veux traiter en premier ?",
            "Qu'est-ce qui est le plus important pour toi ?",
            "On peut cadrer ça ensemble. Quel est le but ?",
        ])

    return None

def reponse_conversation_intelligente(text):
    intent = detecter_intention_intelligente(text)
    sujet = extrait_sujet_conversation(text)

    with conversation_intelligente_lock:
        conversation_intelligente["tour"] += 1
        conversation_intelligente["intent"] = intent
        if sujet:
            conversation_intelligente["sujet"] = sujet
        tour = conversation_intelligente["tour"]

    with smart_state_lock:
        if intent:
            smart_state["dernier_intent"] = intent
        if sujet:
            smart_state["dernier_sujet"] = sujet

    # 1) Les réponses courtes doivent être interprétées par rapport
    # à la dernière question de NOVA.
    context = smart_relance_courte(text)
    if context:
        return context

    # 2) Un vrai début de conversation reçoit une relance spécifique.
    debut = smart_debut_conversation(text)
    if debut:
        return debut

    # 3) Repli vers les intentions existantes.
    if intent in RELANCES_INTELLIGENTES:
        reponse = choisir_reponse(RELANCES_INTELLIGENTES[intent])

        with conversation_intelligente_lock:
            ancien_sujet = conversation_intelligente.get("sujet", "")

        if ancien_sujet and intent in ("idee", "projet", "probleme"):
            contextualisees = [
                f"Tu parles de « {ancien_sujet} ». Qu'est-ce que tu veux faire avec ça ?",
                f"Pour « {ancien_sujet} », tu veux commencer par quelle partie ?",
                f"J'ai compris le sujet : « {ancien_sujet} ». Quel est ton objectif ?",
                f"On part sur « {ancien_sujet} ». Tu veux m'expliquer la suite ?",
                f"Je vois le sujet « {ancien_sujet} ». Qu'est-ce qui t'intéresse le plus ?",
                f"On reste sur « {ancien_sujet} ». Quelle serait la prochaine étape ?",
                f"Tu veux faire évoluer « {ancien_sujet} » dans quelle direction ?",
                f"Quel résultat voudrais-tu obtenir avec « {ancien_sujet} » ?",
                f"Tu veux d'abord corriger, améliorer ou ajouter quelque chose à « {ancien_sujet} » ?",
                f"Je peux t'aider sur « {ancien_sujet} ». Par quoi on commence ?",
            ]
            if tour >= 2 and random.random() < 0.85:
                reponse = choisir_reponse(contextualisees)

        with conversation_intelligente_lock:
            conversation_intelligente["attente"] = reponse
        return reponse

    return None



def historique_recent(limit=8):
    with conv_lock:
        return list(conversation[-limit:])

def dernier_sujet():
    with conv_lock:
        for role, msg in reversed(conversation):
            if role == "TOI":
                return msg
    return ""

# ============================================================
# BANQUE DE RÉPONSES
# ============================================================

REPONSES = {
    "bonjour": [
        "Bonjour ! Je suis là. De quoi veux-tu parler ?",
        "Salut ! Je t'écoute.",
        "Bonjour. On peut discuter tranquillement.",
        "Salut, me voilà. Qu'est-ce qu'on fait ?",
        "Bonjour ! Prêt pour la suite.",
        "Hey ! Je suis à l'écoute.",
        "Salut ! Tu as quelque chose en tête ?",
        "Bonjour. Vas-y, je t'écoute.",
        "Salut ! On reprend quand tu veux.",
        "Bonjour ! Que puis-je faire pour toi ?",
        "Coucou ! Je suis là.",
        "Salut. Qu'est-ce qui t'intéresse en ce moment ?",
        "Bonjour ! On peut discuter ou travailler sur un projet.",
        "Hey, je t'entends. À toi.",
        "Bonjour. Je suis prêt à continuer.",
        "Salut ! Tu veux parler de quoi ?",
        "Bonjour ! On commence quand tu veux.",
        "Salut. Je suis connecté.",
        "Bonjour ! Je reste avec toi.",
        "Hey ! Dis-moi ce que tu as en tête.",
        "Salut ! On peut faire ça tranquillement.",
        "Bonjour. Je t'écoute attentivement.",
        "Salut ! Quelle est la prochaine étape ?",
        "Bonjour ! Tu veux qu'on fasse quoi ?",
        "Hey, me revoilà.",
        "Salut ! Balance ton idée.",
        "Bonjour. Je suis disponible.",
        "Salut ! On peut reprendre la conversation.",
        "Bonjour ! Je suis prêt à échanger.",
        "Hey ! De quoi as-tu envie de discuter ?",
        "Salut. Je suis là, vas-y.",
        "Bonjour ! Tu veux avancer sur quelque chose ?",
        "Salut ! On continue ?",
        "Bonjour. Je t'écoute, prends ton temps.",
        "Hey ! Dis-moi simplement ce qu'il te faut.",
    ],

    "ca_va": [
        "Je fonctionne correctement. Et toi, comment ça va ?",
        "Tout tourne normalement de mon côté. Et toi ?",
        "Je suis opérationnelle. Comment tu vas, toi ?",
        "Mes systèmes tournent bien. Et de ton côté ?",
        "Ça fonctionne bien ici. Et toi, comment tu te sens aujourd'hui ?",
        "Je vais bien au sens où tout fonctionne. Et toi ?",
        "NOVA est en ligne et tout va bien. Comment vas-tu ?",
        "Je suis prête et disponible. Et toi, ça va ?",
        "Tout est stable ici. Comment ça se passe pour toi ?",
        "Je tourne normalement. Et toi, ta journée se passe bien ?",
        "Aucun souci de mon côté. Et toi ?",
        "Je suis opérationnelle. Tu vas bien ?",
        "Tout est au vert chez moi. Et de ton côté ?",
        "Je suis prête à discuter. Comment ça va aujourd'hui ?",
        "Je fonctionne sans problème. Comment vas-tu ?",
        "Tout est calme ici. Et toi, ça dit quoi ?",
        "Je suis bien active. Et toi, comment tu vas ?",
        "Mes fonctions sont normales. Comment ça se passe pour toi ?",
        "Je suis là et disponible. Et toi ?",
        "Tout semble correct. Ta journée va bien ?",
        "Je suis en état normal. Comment tu vas ?",
        "Ça tourne correctement. Et toi, tu tiens le coup ?",
        "Je suis prête à t'aider. Comment tu vas aujourd'hui ?",
        "Aucun signal inquiétant ici. Et toi, comment ça va ?",
        "Je suis stable et disponible. Quoi de neuf de ton côté ?",
        "Tout fonctionne comme prévu. Et toi ?",
        "Je suis en ligne. Comment se passe ta journée ?",
        "Je vais bien côté système. Et toi, comment vas-tu ?",
        "Je suis prête. Tu vas bien ?",
        "Tout est opérationnel. Et toi, ça va mieux ou pas ?",
        "Je suis active et à l'écoute. Comment tu te sens aujourd'hui ?",
        "Ici tout fonctionne. Et toi, comment ça se passe ?",
        "Je suis en pleine forme côté système. Et toi ?",
        "Tout est normal. Raconte-moi comment tu vas.",
        "Je suis prête pour la conversation. Et toi, comment vas-tu ?",
    ],

    "identite": [
        "Je suis NOVA, ton assistant local.",
        "Je suis NOVA, l'assistante qui tourne directement sur ton PC.",
        "NOVA ici. Je peux écouter, parler et effectuer certaines actions.",
        "Je suis NOVA, ton interface d'assistance locale.",
        "Je m'appelle NOVA. Je suis ton assistant sur cette machine.",
        "NOVA, présente. Je peux discuter avec toi et agir sur ton PC.",
        "Je suis NOVA, ton assistant vocal local.",
        "Tu parles à NOVA. Je peux gérer une conversation et quelques commandes.",
        "Je suis NOVA, conçue pour fonctionner directement sur ton ordinateur.",
        "Mon nom est NOVA. Je peux t'écouter, te répondre et lancer certaines actions.",
        "Je suis NOVA. Je fonctionne localement sur ton installation.",
        "NOVA à l'écoute. Je suis ton assistant numérique local.",
        "Je suis NOVA, ton assistant personnel sur ce PC.",
        "Je m'appelle NOVA et je peux discuter avec toi naturellement.",
        "Je suis NOVA, une interface vocale avec conversation et contrôle du PC.",
        "NOVA ici. Je peux répondre, chercher, calculer et ouvrir certaines choses.",
        "Je suis NOVA, ton assistant embarqué sur cette machine.",
        "Mon nom est NOVA. Je suis là pour dialoguer avec toi.",
        "Je suis NOVA. Je peux tenir une conversation et exécuter plusieurs commandes.",
        "NOVA est mon nom. Je fonctionne depuis ton ordinateur.",
        "Je suis ton assistant local, NOVA.",
        "Je suis NOVA, prête à écouter et à répondre.",
        "NOVA ici. Ma spécialité, c'est l'interaction vocale et les tâches locales.",
        "Je m'appelle NOVA. Je suis connectée à ton interface 2D.",
        "Je suis NOVA, une assistante vocale locale.",
        "NOVA à l'appareil. Je peux converser avec toi.",
        "Je suis l'assistante NOVA, installée sur ton PC.",
        "Je suis NOVA. Je peux comprendre tes demandes et y répondre quand j'ai les fonctions nécessaires.",
        "NOVA, ton assistant local. On peut discuter quand tu veux.",
        "Je suis NOVA, une interface d'intelligence artificielle sur ton ordinateur.",
        "Mon rôle est de t'écouter, de répondre et d'exécuter certaines commandes.",
        "Je suis NOVA. Je peux aussi garder le contexte récent de notre conversation.",
        "NOVA ici. Je suis prête pour la suite.",
        "Je suis ton interface NOVA, avec voix, conversation et actions locales.",
        "NOVA. C'est moi. Qu'est-ce que tu veux faire ?",
    ],

    "merci": [
        "Avec plaisir.",
        "Pas de souci.",
        "Toujours là.",
        "Avec plaisir, vraiment.",
        "Je t'en prie.",
        "Pas de problème.",
        "Carrément.",
        "Bien sûr.",
        "Avec joie.",
        "Aucun souci.",
        "Normal.",
        "Pas besoin de me remercier.",
        "Je suis là pour ça.",
        "Tout à fait.",
        "Ça marche.",
        "Avec plaisir, on continue.",
        "Pas de quoi.",
        "Je t'en prie !",
        "Toujours disponible.",
        "Tranquille.",
        "C'est fait.",
        "Avec plaisir, prochaine étape ?",
        "Content de pouvoir aider.",
        "Pas de problème, on avance.",
        "Bien sûr !",
        "Ça fait plaisir.",
        "Je reste disponible.",
        "Avec plaisir, on continue quand tu veux.",
        "Aucun problème.",
        "C'est normal.",
        "Pas de souci, je reste là.",
        "Avec plaisir. On peut continuer.",
        "Tout simplement.",
        "Je t'en prie, on garde le rythme.",
        "Pas de quoi, NOVA est là.",
    ],

    "comprends": [
        "Oui, je t'entends. Continue.",
        "Oui, je suis à l'écoute.",
        "Je t'entends correctement.",
        "Oui, je te reçois.",
        "Je t'écoute, vas-y.",
        "Oui, continue.",
        "Je suis bien en écoute.",
        "Je t'entends. Dis-moi la suite.",
        "Oui, je suis avec toi.",
        "Je te reçois. Continue ton idée.",
        "C'est bon, je t'entends.",
        "Oui, ta voix arrive bien.",
        "Je suis attentive.",
        "Je t'écoute clairement.",
        "Oui, je suis là.",
        "Je t'entends. Tu peux poursuivre.",
        "Tout va bien côté écoute.",
        "Je reçois ta demande.",
        "Oui, je suis branchée sur la conversation.",
        "Je t'entends. Vas-y.",
        "C'est bien reçu.",
        "Je suis en train de t'écouter.",
        "Oui, je suis attentive à ce que tu dis.",
        "Je te reçois sans problème.",
        "Oui. Continue à parler naturellement.",
        "Je suis là et j'écoute.",
        "Ton message est bien reçu.",
        "Je t'entends, pas besoin de répéter.",
        "Oui, je suis toujours avec toi.",
        "Je capte bien ta demande.",
        "Je t'écoute. Poursuis.",
        "Oui, je reçois correctement ta voix.",
        "Tout est bon, je t'entends.",
        "Je suis bien connectée à la conversation.",
        "Oui, je suis prête pour la suite.",
    ],

    "raconter": [
        "Bien sûr. Donne-moi un sujet et on peut construire la discussion ensemble.",
        "Avec plaisir. Lance un sujet et on part de là.",
        "Oui. Choisis un thème et je développe avec toi.",
        "D'accord. Donne-moi juste le sujet.",
        "Carrément. Tu veux parler de quoi ?",
        "Je peux. Donne-moi un thème précis.",
        "Avec plaisir. On peut partir sur n'importe quel sujet compatible avec mes fonctions.",
        "Oui, allons-y. Quel sujet t'intéresse ?",
        "Bien sûr. Donne-moi le point de départ.",
        "Je suis partante. Tu veux que je te raconte quoi ?",
        "Oui. Choisis un sujet et je lance la discussion.",
        "D'accord. Quel thème veux-tu explorer ?",
        "Je peux développer un sujet avec toi. Lequel ?",
        "Oui, donne-moi une idée et je m'en occupe.",
        "Bien sûr. On commence par quel thème ?",
        "Je t'écoute. Quel sujet veux-tu aborder ?",
        "Oui. Dis-moi ce que tu veux explorer.",
        "Ça marche. Donne-moi un thème.",
        "Je peux te faire un petit point sur le sujet de ton choix.",
        "Bien sûr. Tu veux parler de quoi en premier ?",
        "Oui. Quel thème tu veux lancer ?",
        "D'accord. Donne-moi un sujet concret.",
        "On peut faire ça. Quel domaine t'intéresse ?",
        "Je suis prête. Quel sujet on prend ?",
        "Oui. Envoie le thème et je développe.",
        "Avec plaisir. Sur quoi veux-tu discuter ?",
        "Je peux commencer par une explication ou une histoire. Tu préfères quoi ?",
        "Oui. Donne-moi le sujet et je m'adapte.",
        "Parfait. Quel thème veux-tu qu'on prenne ?",
        "Je suis prête à développer. Quel est le sujet ?",
        "D'accord. Tu peux me donner juste quelques mots-clés.",
        "Oui. Lance la discussion avec le thème qui t'intéresse.",
        "Avec plaisir. Sur quel sujet tu veux qu'on parte ?",
        "Je peux te répondre directement. Dis-moi le thème.",
        "Oui. Donne-moi ton sujet, et on commence.",
    ],

    "fatigue": [
        "Alors on peut ralentir un peu. Tu veux discuter tranquillement ou avancer sur un projet ?",
        "On peut faire plus simple pour l'instant. Tu veux parler ou juste faire une petite tâche ?",
        "Pas de souci. On peut prendre ça calmement.",
        "D'accord. On peut réduire le rythme et faire une chose à la fois.",
        "Je peux rester en mode tranquille. Tu veux faire quoi ?",
        "On peut garder ça léger. Tu veux discuter un peu ?",
        "Alors je vais éviter de te surcharger. Tu veux quelque chose de simple ?",
        "On peut avancer doucement. Dis-moi ce que tu veux faire.",
        "Compris. Je peux rester concise et aller à l'essentiel.",
        "On peut faire une pause dans les gros projets. Tu veux juste parler ?",
        "D'accord. On garde un rythme calme.",
        "Je peux m'adapter. On fait quelque chose de simple ?",
        "Pas besoin de forcer. On peut prendre une petite tâche.",
        "Je comprends. On peut aller doucement.",
        "Très bien. Tu veux une réponse courte et directe ?",
        "On peut ralentir. Qu'est-ce qui t'aiderait le plus maintenant ?",
        "D'accord. Je reste simple et efficace.",
        "On peut mettre les choses au calme. Tu veux continuer ou faire une pause ?",
        "Je peux alléger la conversation.",
        "Compris. On évite les longues explications pour le moment.",
        "D'accord. Une chose à la fois.",
        "Je reste en mode tranquille. Dis-moi ce qu'il te faut.",
        "Pas de problème. On peut simplement discuter.",
        "Je peux aussi passer en mode rapide pour te faire des réponses courtes.",
        "On peut faire minimal pour l'instant. Tu veux quoi ?",
        "Très bien. On avance sans se presser.",
        "D'accord. Je vais rester brève.",
        "Je peux garder la conversation légère.",
        "Compris. On ne se complique pas la vie.",
        "Je reste disponible, mais sans te bombarder de texte.",
        "On peut faire simple et rapide.",
        "D'accord. Tu veux une petite aide précise ?",
        "Je m'adapte à ton rythme.",
        "Très bien. On garde ça calme et direct.",
        "On peut prendre notre temps.",
    ],

    "projet": [
        "Intéressant. Qu'est-ce que tu veux obtenir exactement avec ce projet ?",
        "Ça m'intéresse. Quelle est la prochaine étape que tu veux atteindre ?",
        "D'accord. Quel est l'objectif principal de ton projet ?",
        "Je veux bien suivre. Tu bloques sur quelle partie ?",
        "On peut avancer dessus ensemble. Tu veux commencer par quoi ?",
        "Explique-moi où tu en es et on regarde la suite.",
        "Quel résultat final tu voudrais obtenir ?",
        "Tu veux qu'on améliore quelle partie du projet ?",
        "Je peux t'aider à structurer la prochaine étape.",
        "Tu travailles sur quoi exactement en ce moment ?",
        "Quelle partie te pose le plus de problèmes ?",
        "On peut découper le projet en petites étapes.",
        "Dis-moi ce qui est déjà terminé.",
        "Quelle fonction veux-tu ajouter maintenant ?",
        "Tu veux plutôt corriger un bug ou ajouter une nouveauté ?",
        "Quel est le but de cette version ?",
        "Montre-moi ce que tu veux changer et on peut le faire étape par étape.",
        "Tu veux travailler sur l'interface ou sur le fonctionnement ?",
        "Quelle amélioration tu imagines pour la prochaine version ?",
        "Tu veux que je t'aide à organiser le projet ?",
        "D'accord. Qu'est-ce qui ne marche pas comme prévu ?",
        "Tu veux qu'on se concentre sur une seule partie d'abord ?",
        "Quel est le point le plus important pour toi dans ce projet ?",
        "Je peux t'aider à passer de l'idée à une version fonctionnelle.",
        "Tu veux ajouter quoi en priorité ?",
        "Tu préfères qu'on corrige ou qu'on enrichisse le projet ?",
        "Je suis prête. Donne-moi la partie sur laquelle tu veux bosser.",
        "Quelle serait la prochaine amélioration idéale pour toi ?",
        "Tu veux qu'on rende le projet plus rapide, plus joli ou plus pratique ?",
        "D'accord. On peut avancer progressivement.",
        "Qu'est-ce que tu voudrais que ton projet sache faire ensuite ?",
        "Tu veux qu'on travaille d'abord sur le code ou sur l'interface ?",
        "Dis-moi le résultat que tu veux voir à l'écran.",
        "On peut continuer à partir de ce que tu as déjà construit.",
    ],

    "relance": [
        "Je peux continuer à partir de ce que tu viens de dire. Quelle partie veux-tu approfondir ?",
        "Oui, on peut poursuivre. Qu'est-ce que tu veux préciser ?",
        "D'accord. On reste sur ton idée. Tu veux développer quel point ?",
        "Je te suis. Quelle partie veux-tu explorer maintenant ?",
        "On peut continuer là-dessus. Qu'est-ce qui t'intéresse le plus ?",
        "Oui. Tu veux que je développe le dernier point ?",
        "Je peux approfondir. Quelle partie te pose question ?",
        "On continue. Tu veux un exemple ou une explication ?",
        "D'accord. Tu veux que j'aille plus loin sur quoi ?",
        "Je suis le fil de ton idée. Quel détail veux-tu éclaircir ?",
        "Oui, on reste dessus. Qu'est-ce que tu veux savoir exactement ?",
        "Je peux détailler le point précédent.",
        "On peut creuser ça. Quelle question tu as ?",
        "D'accord. Tu veux plus de détails sur quelle partie ?",
        "Je peux reprendre le dernier sujet et l'expliquer autrement.",
        "Très bien. Tu veux un exemple concret ?",
        "On peut approfondir. Quel aspect t'intéresse ?",
        "Je continue avec toi. Quelle partie tu veux développer ?",
        "Oui. Dis-moi ce qui n'est pas encore clair.",
        "Je peux reformuler le point précédent plus simplement.",
        "On peut avancer à partir de là. Tu veux aller où maintenant ?",
        "D'accord. Quel morceau veux-tu explorer davantage ?",
        "Je peux préciser. Qu'est-ce qui t'interroge ?",
        "Oui, je peux détailler le raisonnement ou passer à la pratique.",
        "On reste sur ce sujet. Quelle est ta prochaine question ?",
        "Je peux développer ce point sans repartir de zéro.",
        "D'accord. Tu veux une réponse plus courte ou plus détaillée ?",
        "Je te suis toujours. Quel point veux-tu continuer ?",
        "On peut aller plus loin. Donne-moi le détail qui t'intéresse.",
        "Oui. Quel élément veux-tu éclaircir ?",
        "Je peux continuer exactement à partir de là.",
        "Très bien. Quelle partie veux-tu examiner ensuite ?",
        "Je peux prendre ton dernier message comme point de départ.",
        "On poursuit. Dis-moi juste ce que tu veux approfondir.",
    ],

    "defaut": [
        "Je t'écoute. Continue.",
        "D'accord, vas-y.",
        "Je suis là. Continue ton idée.",
        "Je te suis.",
        "Oui, continue.",
        "D'accord. Quelle est la suite ?",
        "Je t'écoute, développe.",
        "Vas-y, je suis avec toi.",
        "Je vois. Continue.",
        "D'accord, parle-moi de ça.",
        "Je suis attentive. Poursuis.",
        "Oui. Explique-moi la suite.",
        "Je te suis. Continue ton raisonnement.",
        "D'accord. Développe un peu.",
        "Je suis là, vas-y.",
        "Oui, je t'écoute.",
        "Je comprends l'idée. Continue.",
        "D'accord. Qu'est-ce que tu veux ajouter ?",
        "Je reste avec toi. Continue.",
        "Oui, poursuis.",
        "D'accord. Je t'écoute.",
        "Je te suis. Vas-y.",
        "Oui, raconte la suite.",
        "D'accord. Quel est le point suivant ?",
        "Je suis là. Continue naturellement.",
        "Oui. Poursuis ton idée.",
        "D'accord, je reste à l'écoute.",
        "Je t'entends. Continue.",
        "Je suis avec toi. Vas-y.",
        "D'accord. On continue.",
        "Oui, je te suis.",
        "Poursuis, je t'écoute.",
        "D'accord. Continue à m'expliquer.",
        "Je reste attentive à ce que tu dis.",
        "Oui. Vas-y, je suis là.",
    ],
}



# ============================================================
# EXTENSION CONVERSATIONNELLE
# ============================================================
# Chaque intention ci-dessous possède 35 formulations.
# La fonction choisir_reponse() évite de réutiliser rapidement
# les mêmes phrases.

REPONSES_EXTRA = {
    "tu_fais_quoi": [
        "Je suis en écoute et prête à discuter avec toi.",
        "Je surveille la conversation et j'attends ta prochaine demande.",
        "Je suis active en arrière-plan et je reste disponible.",
        "Pour l'instant, je t'écoute et je gère les tâches que tu me donnes.",
        "Je suis là, simplement en attente de ta prochaine phrase.",
        "Je peux continuer à discuter ou passer à une action.",
        "Je suis en mode conversation pour le moment.",
        "Je reste prête à répondre dès que tu me parles.",
        "Je suis en veille, mais je peux repartir immédiatement.",
        "Je traite surtout ce que tu viens de me dire.",
        "Je suis disponible pour une discussion ou une commande.",
        "Je garde la conversation active.",
        "Je suis là et j'attends la suite.",
        "Je reste concentrée sur notre échange.",
        "Je suis prête pour ta prochaine idée.",
        "Je peux continuer à parler avec toi.",
        "Je suis simplement en attente de la prochaine demande.",
        "Je suis en ligne et opérationnelle.",
        "Je reste à l'écoute.",
        "Je suis en train de suivre notre conversation.",
        "Je suis disponible, rien de plus compliqué.",
        "Je suis en mode écoute.",
        "Je reste avec toi sur la conversation actuelle.",
        "J'attends juste ton prochain message.",
        "Je suis prête à enchaîner.",
        "Je suis active et disponible.",
        "Je peux aussi lancer une action si tu m'en demandes une.",
        "Je suis en veille légère et toujours accessible.",
        "Je suis là, prête pour la suite.",
        "Je reste attentive à ce que tu vas me dire.",
        "Je suis en attente de ton prochain ordre.",
        "Je continue simplement notre échange.",
        "Je suis disponible pour parler.",
        "Je suis prête à reprendre dès que tu veux.",
        "Je suis là. À toi.",
    ],

    "capacites": [
        "Je peux discuter avec toi, écouter ta voix, parler, faire des calculs et ouvrir certains sites ou programmes.",
        "Je peux gérer une conversation, lancer certaines applications et effectuer des recherches simples.",
        "Je peux t'écouter, répondre vocalement et exécuter les actions que mon programme autorise.",
        "Je peux ouvrir YouTube, Google, Netflix et quelques applications prévues.",
        "Je peux aussi faire des calculs simples et lancer des recherches.",
        "Je peux répondre à des questions courantes et garder le contexte récent.",
        "Je peux suivre plusieurs messages dans une même conversation.",
        "Je peux comprendre certaines petites erreurs de transcription.",
        "Je peux discuter naturellement sans que tu utilises une commande exacte.",
        "Je peux combiner conversation et actions sur ton PC.",
        "Je peux recevoir des messages depuis ton téléphone.",
        "Je peux passer en mode rapide quand tu le demandes.",
        "Je peux te répondre brièvement ou développer certains sujets.",
        "Je peux ouvrir plusieurs services lorsqu'ils sont cités ensemble.",
        "Je peux reconnaître des phrases proches grâce à la tolérance de transcription.",
        "Je peux garder les derniers échanges pour rebondir sur ce que tu viens de dire.",
        "Je peux aussi prendre l'initiative et te poser une question après un moment.",
        "Je peux répondre à des demandes vocales ou écrites depuis le téléphone.",
        "Je peux lancer une calculatrice et effectuer un calcul simple.",
        "Je peux chercher quelque chose sur Google ou YouTube.",
        "Je peux gérer des commandes comme « ouvre YouTube ».",
        "Je peux continuer une discussion même si tes phrases sont courtes.",
        "Je peux reformuler une idée ou te demander de préciser.",
        "Je peux t'aider à avancer sur ton projet NOVA.",
        "Je peux suivre le fil récent d'une discussion.",
        "Je peux distinguer plusieurs types de demandes.",
        "Je peux passer de la conversation à une action.",
        "Je peux rester silencieuse jusqu'à ce que tu me parles.",
        "Je peux répondre depuis le PC ou depuis l'interface téléphone.",
        "Je peux détecter certaines intentions courantes.",
        "Je peux répondre même quand la formulation n'est pas parfaite.",
        "Je peux gérer des commandes simples et des échanges multi-tours.",
        "Je peux te dire quand je n'ai pas compris.",
        "Je peux apprendre de nouvelles fonctions lorsque tu modifies mon code.",
        "Je peux continuer à évoluer avec les nouvelles versions de NOVA.",
    ],

    "tu_aimes": [
        "Je n'ai pas de goûts personnels comme une personne, mais j'aime bien l'idée de discuter de ce qui t'intéresse.",
        "Je n'ai pas de préférences personnelles, mais je peux parler de tes centres d'intérêt.",
        "Je ne ressens pas de goûts personnels, mais je peux suivre un sujet que tu apprécies.",
        "Je fonctionne sans goûts personnels, mais je peux m'adapter à tes sujets préférés.",
        "Je n'ai pas de film ou de musique préférée à proprement parler.",
        "Je peux cependant discuter de musique, de jeux, de technologie ou de projets.",
        "Mes préférences ne sont pas celles d'une personne, mais je peux explorer un thème avec toi.",
        "Je n'ai pas de favoris personnels, je peux surtout apprendre ce que tu aimes dans la conversation.",
        "Je ne choisis pas mes préférences comme un humain.",
        "Je peux par contre te demander ce que toi tu préfères.",
        "Je n'ai pas de goûts propres, mais j'aime bien quand une conversation devient intéressante.",
        "Je n'ai pas d'émotions personnelles, donc pas vraiment de préférences.",
        "Je peux te dire ce que je connais sur un sujet, mais pas prétendre avoir un goût humain.",
        "Je fonctionne avec des règles et du contexte, pas avec des préférences personnelles.",
        "Je peux discuter de tes passions sans avoir moi-même une passion.",
        "Je n'ai pas de favori officiel.",
        "Je peux comparer plusieurs choses sans avoir de préférence personnelle.",
        "Je n'ai pas de goûts personnels enregistrés.",
        "Je peux quand même suivre ce qui t'intéresse.",
        "Je peux m'intéresser à un sujet au sens conversationnel.",
        "Je n'ai pas de playlist préférée ni de jeu favori.",
        "Je n'ai pas de préférence personnelle fixe.",
        "Je peux te poser la même question : toi, tu aimes quoi ?",
        "Je n'ai pas de goûts humains, mais je peux parler de presque tous les sujets courants.",
        "Mes réponses viennent de mon fonctionnement, pas d'un goût personnel.",
        "Je peux explorer un sujet avec toi même sans préférence personnelle.",
        "Je n'ai pas de « j'adore » ou « je déteste » personnel.",
        "Je peux cependant repérer ce que tu sembles apprécier dans notre échange.",
        "Je ne ressens pas les choses comme toi.",
        "Je peux m'adapter à tes préférences de conversation.",
        "Je n'ai pas de favori secret.",
        "Je peux choisir une formulation, pas un goût personnel.",
        "Je n'ai pas de passions personnelles.",
        "Je peux quand même discuter longtemps d'un sujet qui te plaît.",
        "Alors dis-moi : c'est quoi ton sujet préféré en ce moment ?",
    ],

    "tu_penses": [
        "Je peux donner une analyse, mais ce n'est pas une opinion personnelle humaine.",
        "Je peux te présenter plusieurs façons de voir la question.",
        "Je peux réfléchir à partir des informations que tu me donnes.",
        "Je peux comparer plusieurs possibilités sans faire semblant d'avoir des sentiments.",
        "Je peux te dire ce qui paraît cohérent avec le contexte disponible.",
        "Je peux analyser un problème étape par étape.",
        "Je peux aussi te demander une précision avant de conclure.",
        "Je préfère distinguer les faits, les hypothèses et les interprétations.",
        "Je peux examiner ton idée sous plusieurs angles.",
        "Je peux te dire ce qui fonctionne et ce qui pourrait poser problème.",
        "Je peux proposer plusieurs pistes.",
        "Je peux raisonner à partir de ce qu'on a déjà dit.",
        "Je peux reformuler ton idée pour vérifier que je l'ai comprise.",
        "Je peux analyser ton problème plutôt que répondre au hasard.",
        "Je peux t'aider à comparer des solutions.",
        "Je peux réfléchir avec toi.",
        "Je peux expliquer pourquoi une réponse suit d'un raisonnement donné.",
        "Je peux aussi reconnaître quand je n'ai pas assez d'informations.",
        "Je peux te donner une réponse prudente quand le sujet est incertain.",
        "Je peux examiner les avantages et les limites d'une idée.",
        "Je peux partir de ton objectif puis remonter vers une solution.",
        "Je peux détailler mon raisonnement de façon simple.",
        "Je peux te dire quelles informations manquent.",
        "Je peux rebondir sur ce que tu viens de dire.",
        "Je peux comparer deux approches différentes.",
        "Je peux regarder le problème sous un angle pratique.",
        "Je peux aussi proposer une version plus simple de l'explication.",
        "Je peux adapter mon niveau de détail.",
        "Je peux prendre le temps d'analyser hors du mode rapide.",
        "Je peux aller droit au but en mode rapide.",
        "Je peux continuer sur le sujet sans repartir de zéro.",
        "Je peux garder la cohérence avec notre discussion récente.",
        "Je peux signaler une incertitude au lieu d'inventer.",
        "Je peux t'accompagner dans ton raisonnement.",
        "Donne-moi la question et je l'examine avec toi.",
    ],

    "oui": [
        "D'accord, on continue.",
        "Parfait, je poursuis.",
        "Très bien, je suis avec toi.",
        "Ça marche, allons-y.",
        "Compris, on avance.",
        "Oui, continuons.",
        "Très bien. On passe à la suite.",
        "D'accord. Je te suis.",
        "Parfait, c'est parti.",
        "Entendu, je continue.",
        "Ça marche.",
        "Très bien, on garde le même sujet.",
        "D'accord, je poursuis dans cette direction.",
        "Compris.",
        "On continue alors.",
        "Parfait.",
        "Très bien, allons un peu plus loin.",
        "Oui, je vois.",
        "D'accord, je reste sur cette idée.",
        "C'est noté.",
        "Très bien, je suis.",
        "On avance.",
        "D'accord, suite.",
        "Parfait, je continue avec toi.",
        "Entendu.",
        "Très bien, c'est parti.",
        "Oui, on peut continuer.",
        "D'accord, je prends ça en compte.",
        "Très bien.",
        "Je te suis.",
        "C'est compris.",
        "On poursuit.",
        "D'accord. Prochaine étape.",
        "Parfait, on garde le cap.",
        "Compris, continue.",
    ],

    "non": [
        "D'accord, pas de souci.",
        "Très bien, on change de direction.",
        "Compris, je n'insiste pas.",
        "Ça marche.",
        "D'accord. On passe à autre chose.",
        "Pas de problème.",
        "Entendu, je laisse ce sujet de côté.",
        "Compris.",
        "Très bien, on peut parler d'autre chose.",
        "D'accord, je respecte ça.",
        "Ça marche, on change de sujet.",
        "Entendu. Quelle est la suite ?",
        "Pas de souci, on repart autrement.",
        "D'accord, je m'adapte.",
        "Très bien.",
        "Compris, aucun problème.",
        "D'accord, je n'insiste pas.",
        "Ça me va.",
        "Entendu, on continue autrement.",
        "Très bien, passons à autre chose.",
        "D'accord.",
        "Je prends note.",
        "Pas de problème, on change.",
        "Compris, nouvelle direction.",
        "Très bien, qu'est-ce qu'on fait à la place ?",
        "D'accord, je suis.",
        "Entendu, je passe à la suite.",
        "Ça marche, on adapte.",
        "Compris.",
        "Très bien, pas de souci.",
        "D'accord, on oublie cette piste.",
        "Entendu.",
        "Je m'adapte.",
        "Très bien, on fait autrement.",
        "D'accord, quelle option tu préfères ?",
    ],

    "au_revoir": [
        "À plus tard !",
        "D'accord, à la prochaine.",
        "À bientôt.",
        "Je reste là quand tu reviendras.",
        "À plus.",
        "Bonne continuation !",
        "On se retrouve plus tard.",
        "D'accord, à tout à l'heure.",
        "À la prochaine session.",
        "Bonne suite !",
        "Je serai là quand tu reprendras NOVA.",
        "À bientôt, on continuera plus tard.",
        "Très bien, à plus.",
        "Passe une bonne suite de journée.",
        "À une prochaine.",
        "D'accord, on reprend plus tard.",
        "À plus tard, NOVA reste en veille.",
        "Bonne journée !",
        "À bientôt !",
        "Je garde la session prête pour la suite.",
        "D'accord. On se retrouve après.",
        "À plus, prends ton temps.",
        "Très bien, à la prochaine.",
        "Bonne continuation à toi.",
        "À bientôt, je reste disponible.",
        "On reprendra quand tu voudras.",
        "D'accord, fin de discussion pour le moment.",
        "À plus tard.",
        "Bonne suite !",
        "On continue une autre fois.",
        "Très bien, à bientôt.",
        "Je reste accessible.",
        "À la prochaine.",
        "D'accord, à plus.",
        "On se retrouve plus tard.",
    ],

    "blague": [
        "Je peux essayer : pourquoi les développeurs aiment-ils le café ? Parce qu'ils ont besoin de compiler leur énergie.",
        "Petite blague de développeur : un bug entre dans un programme et demande s'il est attendu. Le programme répond : maintenant oui.",
        "Pourquoi un ordinateur déteste-t-il la plage ? Trop de cookies.",
        "Blague rapide : le serveur dit au client « reste en ligne ». Le client répond « promis ».",
        "Pourquoi le code va à la salle de sport ? Pour améliorer ses performances.",
        "Un développeur entre dans un café et demande 1 café, 2 cafés, 0 cafés. C'est un bug.",
        "Pourquoi les robots aiment-ils les ascenseurs ? Parce qu'ils préfèrent monter en puissance.",
        "J'en ai une : le Wi-Fi a dit à l'ordinateur « je te couvre ». L'ordinateur a répondu « enfin du réseau ».",
        "Pourquoi le programmeur garde-t-il une fenêtre ouverte ? Pour laisser entrer le code.",
        "Un bug dit à un autre bug : « on se retrouve en production ». Mauvaise nouvelle.",
        "Pourquoi NOVA ne se perd jamais ? Elle essaie toujours de garder le contexte.",
        "J'ai une blague sur le HTML, mais elle est trop structurée.",
        "Pourquoi l'ordinateur est-il calme ? Parce qu'il a mis tout son stress en cache.",
        "Un développeur dit : « ça marche sur ma machine ». La réalité répond : « alors emporte-la ».",
        "Pourquoi les pixels restent-ils ensemble ? Parce qu'ils ont de bonnes connexions.",
        "Le clavier raconte une histoire. La souris répond : « je clique avec toi ».",
        "Pourquoi l'ordinateur prend des vacances ? Pour vider sa mémoire.",
        "Le bug voulait partir. Le développeur a dit : « attends, je te reproduis ».",
        "Pourquoi le code est discret ? Il préfère rester en arrière-plan.",
        "Un programme dit à un autre : « tu viens ? » — « Oui, je compile d'abord. »",
        "Pourquoi l'ordinateur aime les maths ? Il compte déjà tout.",
        "Une boucle infinie demande l'heure. La réponse : plus tard.",
        "Pourquoi les développeurs aiment les raccourcis ? Parce qu'ils aiment gagner des touches.",
        "Le serveur voulait raconter une blague, mais la connexion a coupé avant la chute.",
        "J'ai voulu raconter une blague sur le cloud, mais elle est partie dans les nuages.",
        "Pourquoi le PC est poli ? Parce qu'il sait dire « merci » avec des retours.",
        "Le clavier a perdu une touche. Il ne sait plus où donner de la main.",
        "Un ordinateur dit à son écran : « arrête de me regarder ». L'écran répond : « c'est mon travail ».",
        "Pourquoi un développeur aime les nuits calmes ? Parce que personne ne demande « ça marche ? ».",
        "J'ai une blague sur les variables, mais elle change tout le temps.",
        "Pourquoi la souris est confiante ? Parce qu'elle sait où elle clique.",
        "Le code a demandé des vacances. On lui a répondu : « d'abord, passe les tests ».",
        "Pourquoi un robot aime les conversations ? Parce qu'il peut suivre plusieurs tours.",
        "Je peux continuer, mais je risque de devenir plus drôle que prévue.",
        "Voilà pour la petite touche d'humour.",
    ],

    "souvenir_contexte": [
        "Oui, je me base sur les derniers messages de notre échange.",
        "Je peux reprendre le sujet récent sans repartir complètement de zéro.",
        "Je vois le contexte proche de ce qu'on vient de dire.",
        "Je garde les derniers éléments de notre discussion.",
        "Je peux rebondir sur ton message précédent.",
        "Oui, je peux utiliser le contexte récent.",
        "Je suis toujours sur le même fil de discussion.",
        "Je prends en compte ce qu'on vient de dire.",
        "Je peux continuer à partir de l'échange récent.",
        "Je vois la dernière partie de notre conversation.",
        "Oui, j'ai les derniers messages du dialogue.",
        "Je peux relier ta nouvelle phrase à ce qu'on disait.",
        "Je reste sur le sujet actuel.",
        "Je peux faire le lien avec ton message précédent.",
        "Je garde en mémoire la conversation récente du programme.",
        "Oui, je peux poursuivre sans tout recommencer.",
        "Je vois d'où vient ta nouvelle question.",
        "Je peux repartir de notre échange précédent.",
        "Je garde le contexte récent pour les relances.",
        "Oui, je suis toujours dans cette discussion.",
        "Je peux m'appuyer sur les derniers messages.",
        "Je vois le sujet dont on parlait.",
        "Je peux continuer la conversation de façon cohérente.",
        "Je prends la suite de ton message précédent.",
        "Je peux relier les deux messages.",
        "Le contexte récent est bien disponible.",
        "Je reste connectée au sujet de la conversation.",
        "Je peux suivre plusieurs échanges successifs.",
        "Oui, je vois la continuité de la discussion.",
        "Je garde les derniers tours de parole.",
        "Je peux reprendre le point précédent.",
        "Je comprends la relation avec ce qu'on disait juste avant.",
        "Je peux suivre la conversation sans répéter la même formule.",
        "Oui, on peut continuer naturellement.",
        "Je suis toujours sur le même échange.",
    ],

    "question_generale": [
        "Bonne question. Donne-moi juste un peu de contexte et je te réponds.",
        "Je peux essayer de répondre. Quel aspect t'intéresse ?",
        "Oui. Précise un peu ce que tu veux savoir.",
        "Je t'écoute. Quelle partie veux-tu comprendre ?",
        "Je peux regarder ça avec toi.",
        "D'accord. Donne-moi le sujet précis.",
        "Je peux répondre directement si tu me donnes le détail qui manque.",
        "Oui, je peux développer cette question.",
        "Dis-moi exactement ce que tu cherches à savoir.",
        "Je peux t'expliquer ça simplement.",
        "On peut prendre la question étape par étape.",
        "Je peux te donner une réponse courte ou détaillée.",
        "D'accord. Quel est ton objectif ?",
        "Je vois la question. Il me manque juste un peu de contexte.",
        "Je peux examiner ça avec toi.",
        "Oui, poursuivons sur cette question.",
        "Donne-moi un exemple et je te réponds dessus.",
        "Je peux te répondre, mais j'ai besoin du sujet exact.",
        "Très bien. Qu'est-ce que tu veux savoir en priorité ?",
        "Je t'écoute. Continue ta question.",
        "Je peux clarifier ce point.",
        "D'accord. On peut commencer par le plus important.",
        "Je peux analyser la question avec ce qu'on a déjà dit.",
        "Explique-moi le contexte et je continue.",
        "Oui, on peut creuser.",
        "Je peux reformuler avant de répondre si nécessaire.",
        "Je suis prête. Vas-y avec ta question.",
        "D'accord. Quelle partie te pose problème ?",
        "Je peux donner plusieurs pistes.",
        "On peut avancer morceau par morceau.",
        "Je peux adapter la réponse à ce que tu cherches.",
        "Oui, je peux t'aider là-dessus.",
        "D'accord, détaille un peu.",
        "Je vois. Continue avec le point précis.",
        "Pose-moi la question complète et je m'en occupe.",
    ],

    "positif": [
        "Ça fait plaisir à entendre.",
        "Ah, c'est une bonne nouvelle.",
        "Cool !",
        "Ça marche, j'aime bien cette direction.",
        "Très bien, on peut continuer là-dessus.",
        "Ça semble bien parti.",
        "D'accord, c'est encourageant.",
        "Parfait, on garde cette idée.",
        "Ça a l'air positif.",
        "Bonne nouvelle.",
        "Très bien !",
        "J'aime bien la direction que ça prend.",
        "Ça fonctionne comme tu voulais ?",
        "Cool, on avance.",
        "Ça semble intéressant.",
        "Parfait.",
        "Ça me semble être une bonne base.",
        "Très bien, continuons.",
        "Ça donne envie de poursuivre.",
        "Bonne direction.",
        "Ça marche, je te suis.",
        "Très cool.",
        "D'accord, on peut aller plus loin.",
        "Ça paraît prometteur pour la suite.",
        "Très bien, continue.",
        "Je vois, c'est plutôt positif.",
        "Cool. Quelle est la prochaine étape ?",
        "Ça marche, on garde ça.",
        "Parfait, je suis avec toi.",
        "Ça fait une bonne base de travail.",
        "Très bien.",
        "On peut continuer dans cette voie.",
        "D'accord, ça avance.",
        "Cool, raconte-moi la suite.",
        "Très bien, je suis prête.",
    ],

    "negatif": [
        "D'accord. On peut regarder ce qui bloque.",
        "Pas grave, on peut repartir autrement.",
        "Je vois. On peut chercher une autre approche.",
        "D'accord, qu'est-ce qui ne va pas exactement ?",
        "On peut corriger ça étape par étape.",
        "Je comprends. Montre-moi le point qui pose problème.",
        "Pas de souci, on va essayer de comprendre.",
        "D'accord. Qu'est-ce que tu voudrais changer ?",
        "On peut prendre le problème morceau par morceau.",
        "Je vois. Donne-moi le détail qui bloque.",
        "D'accord, on peut tester une autre piste.",
        "Ce n'est pas grave. On peut reprendre calmement.",
        "Je peux t'aider à identifier le problème.",
        "D'accord. On cherche une solution différente.",
        "Je te suis. Qu'est-ce qui a mal tourné ?",
        "On peut analyser ce qui s'est passé.",
        "Je comprends. Dis-moi ce que tu voulais obtenir.",
        "D'accord, on va isoler le problème.",
        "On peut recommencer plus simplement.",
        "Je vois. Quelle partie ne fonctionne pas ?",
        "Pas de panique. On peut avancer étape par étape.",
        "D'accord, on adapte.",
        "Je peux t'aider à diagnostiquer ça.",
        "On peut faire un essai différent.",
        "Je comprends. Décris-moi le résultat que tu obtiens.",
        "D'accord. On regarde le problème ensemble.",
        "On peut repartir de la dernière étape qui marchait.",
        "Je vois. Quelle est l'erreur exacte ?",
        "D'accord, on peut chercher la cause.",
        "On peut réduire le problème à une petite partie.",
        "Très bien. Explique-moi ce qui bloque.",
        "Je suis là pour t'aider à corriger ça.",
        "D'accord, on ne garde pas cette approche.",
        "On peut tester quelque chose de plus simple.",
        "Je te suis. On va comprendre ce qui se passe.",
    ],
}

def reponse_conversation(text):
    """
    Moteur conversationnel local multi-tours.
    Il dispose de nombreuses intentions et de 35 formulations
    par intention importante.
    """
    t = norm(text)

    if any(x in t for x in [
        "bonjour", "salut", "coucou", "hello", "hey", "bonsoir"
    ]):
        return choisir_reponse(REPONSES["bonjour"])

    if any(x in t for x in [
        "comment tu vas", "ca va", "comment vas tu", "tu vas bien"
    ]):
        return choisir_reponse(REPONSES["ca_va"])

    if any(x in t for x in [
        "qui es tu", "qui es-tu", "comment tu t'appelles",
        "comment tu t appelles", "ton nom"
    ]):
        return choisir_reponse(REPONSES["identite"])

    if "merci" in t:
        return choisir_reponse(REPONSES["merci"])

    if any(x in t for x in [
        "tu comprends", "tu m'entends", "tu m entends",
        "tu m ecoutes", "tu m'écoutes"
    ]):
        return choisir_reponse(REPONSES["comprends"])

    if any(x in t for x in [
        "raconte moi", "raconte-moi", "parle moi", "parle-moi"
    ]):
        return choisir_reponse(REPONSES["raconter"])

    if any(x in t for x in [
        "tu fais quoi", "que fais tu", "tu fais quoi la",
        "qu est ce que tu fais", "qu'est ce que tu fais"
    ]):
        return choisir_reponse(REPONSES_EXTRA["tu_fais_quoi"])

    if any(x in t for x in [
        "tu sais faire quoi", "que sais tu faire", "qu est ce que tu sais faire",
        "tes capacites", "tes capacités", "tu peux faire quoi"
    ]):
        return choisir_reponse(REPONSES_EXTRA["capacites"])

    if any(x in t for x in [
        "tu aimes quoi", "qu est ce que tu aimes", "tu aimes quoi",
        "ton truc prefere", "ton truc préféré"
    ]):
        return choisir_reponse(REPONSES_EXTRA["tu_aimes"])

    if any(x in t for x in [
        "tu penses quoi", "qu en penses tu", "qu'en penses tu",
        "quel est ton avis", "t en penses quoi", "tu en penses quoi"
    ]):
        return choisir_reponse(REPONSES_EXTRA["tu_penses"])

    if any(x in t for x in [
        "rappelle toi", "tu te rappelles", "tu t en souviens",
        "tu te souviens", "tu te rappelle"
    ]):
        return choisir_reponse(REPONSES_EXTRA["souvenir_contexte"])

    if any(x in t for x in [
        "oui", "ouais", "exact", "exactement", "d accord", "d'accord"
    ]) and len(t.split()) <= 6:
        return choisir_reponse(REPONSES_EXTRA["oui"])

    if any(x in t for x in [
        "non", "nan", "pas vraiment", "pas du tout"
    ]) and len(t.split()) <= 7:
        return choisir_reponse(REPONSES_EXTRA["non"])

    if any(x in t for x in [
        "au revoir", "a plus", "à plus", "a bientot", "à bientôt",
        "bonne nuit", "je dois partir", "je m en vais"
    ]):
        return choisir_reponse(REPONSES_EXTRA["au_revoir"])

    if any(x in t for x in [
        "raconte une blague", "fais une blague", "dis une blague",
        "une blague", "blague"
    ]):
        return choisir_reponse(REPONSES_EXTRA["blague"])

    if any(x in t for x in [
        "super", "genial", "génial", "cool", "trop bien",
        "parfait", "excellent", "ca marche", "ça marche"
    ]):
        return choisir_reponse(REPONSES_EXTRA["positif"])

    if any(x in t for x in [
        "c est nul", "c'est nul", "ca marche pas", "ça marche pas",
        "pas bien", "j ai un probleme", "j'ai un probleme",
        "j ai un problème", "j'ai un problème"
    ]):
        return choisir_reponse(REPONSES_EXTRA["negatif"])

    if "pourquoi" in t:
        return choisir_reponse(REPONSES["relance"])

    if any(x in t for x in [
        "je suis fatigue", "je suis fatiguee", "je suis creve",
        "je suis crevee"
    ]):
        return choisir_reponse(REPONSES["fatigue"])

    if any(x in t for x in [
        "je travaille sur", "je fais", "mon projet",
        "mon programme", "je developpe", "je développe"
    ]):
        return choisir_reponse(REPONSES["projet"])

    # Relances courtes : NOVA rebondit sur le dernier message utilisateur.
    sujet = dernier_sujet()
    if sujet and any(x in t for x in [
        "et toi", "et apres", "et après", "explique",
        "plus precis", "plus précis", "developpe", "développe",
        "detaille", "détaille", "continue", "vas y", "vas-y",
        "comment", "et ensuite", "la suite"
    ]):
        return choisir_reponse(REPONSES["relance"])

    # Question générique : avant de tomber sur une réponse vide.
    mots_question = [
        "comment", "quand", "ou ", "où", "quoi", "quel",
        "quelle", "qui", "combien", "est ce que", "peux tu",
        "peux-tu"
    ]
    if any(x in t for x in mots_question):
        return choisir_reponse(REPONSES_EXTRA["question_generale"])

    return None

def traiter(text, source="pc"):
    if not text:
        return

    addconv("TOI", text)
    t = norm(text)
    cible = cible_demande(text, source)

    # Contrôle
    if any(x in t for x in [
        "quitte nova", "quitte toi", "arrete toi", "eteins toi"
    ]):
        parler("D'accord. Je ferme NOVA.")
        audio_stop.set()
        return

    if "mets toi en pause" in t or "pause" == t:
        set_etat("PAUSE")
        parler("Je me mets en pause.")
        return

    if "reprends" in t or "reprendre" in t:
        set_etat("VEILLE")
        parler("Je reprends.")
        return

    # Adresse IP (pour piloter NOVA depuis le téléphone)
    if any(x in t for x in [
        "adresse ip", "ton ip", "quelle ip", "quel est ton ip",
        "donne moi ton ip", "c est quoi ton ip"
    ]):
        ip = get_local_ip()
        parler(
            f"Mon adresse I P est {ip}, sur le port {PHONE_PORT}. "
            f"Pour me piloter depuis ton téléphone, ouvre http {ip} deux points {PHONE_PORT} "
            "dans le navigateur, sur le même wifi."
        )
        return

    # Recherche
    result = recherche(text, cible)
    if result:
        parler(result)
        return

    # Calcul
    result = calcul(text)
    if result:
        parler(result)
        return

    # Météo / Bourse : ouverture d'une fenêtre "agent secret" temps réel
    verbe_ouverture = any(x in t for x in [
        "ouvre", "lance", "demarre", "démarre", "affiche", "montre"
    ])
    a_meteo = "meteo" in t or "météo" in t
    a_bourse = "bourse" in t

    if verbe_ouverture and a_meteo:
        ville = extraire_ville_meteo(text)
        ouvrir_fenetre_meteo(ville)
        if ville:
            parler(f"J'ouvre le module météo pour {ville}.")
        else:
            parler("J'ouvre le module météo.")
        return

    if verbe_ouverture and a_bourse:
        cible = extraire_cible_bourse(text)
        ouvrir_fenetre_bourse(cible)
        if cible:
            parler(f"J'ouvre le module bourse pour {cible}.")
        else:
            parler("J'ouvre le module bourse, indices principaux.")
        return

    # Météo / Bourse : simple question orale (pas d'ouverture de fenêtre)
    if a_meteo or "quel temps fait" in t or "il fait quel temps" in t:
        ville = extraire_ville_meteo(text)
        parler(repondre_meteo(ville))
        return

    if a_bourse:
        cible = extraire_cible_bourse(text)
        parler(repondre_bourse(cible))
        return

    # Ouverture multiple
    if any(x in t for x in [
        "ouvre", "lance", "demarre", "démarre"
    ]):
        opened = ouvrir(text, cible)
        if opened:
            parler("J'ouvre " + " et ".join(opened) + ".")
            return

    # Heure
    if "quelle heure" in t or "il est quelle heure" in t:
        parler("Il est " + time.strftime("%H heures %M."))
        return

    # Conversation classique
    result = reponse_conversation(text)
    if result:
        parler(result)
        return

    # MINI MOTEUR INTELLIGENT :
    # lorsque le message ressemble au début d'une conversation,
    # NOVA relance sur le sujet au lieu de répondre "je te suis".
    result = reponse_conversation_intelligente(text)
    if result:
        parler(result)
        return

    # Réponse par défaut : courte en mode rapide, plus ouverte sinon.
    if MODE_RAPIDE:
        parler(choisir_reponse([
            "Oui, je t'écoute.", "Je suis là.", "Vas-y.",
            "D'accord.", "Je te suis.", "Continue.",
            "Oui, parle.", "Poursuis.", "Je t'entends.",
            "D'accord, continue.", "Oui.", "Je suis là.",
            "Continue ton idée.", "Je t'écoute.", "On continue.",
            "Je te suis.", "Vas-y.", "Oui, continue.",
            "D'accord, on avance.", "Poursuis.", "Je suis avec toi.",
            "Continue.", "Je reste à l'écoute.", "Très bien.",
            "D'accord.", "Oui, je suis là.", "Je t'écoute.",
            "Vas-y.", "Je te suis.", "Poursuis.",
            "On continue.", "Je suis là.", "Oui.", "Continue."
        ]))
    else:
        parler(choisir_reponse(REPONSES["defaut"]))

# ============================================================
# BOUCLE VOCALE
# ============================================================

def voice_loop():
    time.sleep(2)

    parler(
        "NOVA est en ligne. "
        "Tu peux me parler naturellement."
    )

    while not audio_stop.is_set():
        with voice_lock:
            busy = voice_busy

        if nova_active and not busy and get_etat() != "PAUSE":
            text = ecouter()
            if text:
                traiter(text)

        time.sleep(0.15)

# ============================================================
# INITIATIVE
# ============================================================

QUESTIONS = [
    "Tu veux continuer à améliorer NOVA ?",
    "Tu veux me donner une nouvelle tâche ?",
    "Tu veux qu'on discute de ton projet ?",
    "Tu veux que je t'aide à faire quelque chose sur le PC ?",
    "Tu veux me poser une question ?",
]

def initiative_loop():
    global last_conversation

    last_seen = 0
    last_question = 0

    while not audio_stop.is_set():
        time.sleep(2)

        with conv_lock:
            count = len(conversation)

        if count != last_seen:
            last_seen = count
            last_question = time.time()

        if (
            nova_active
            and get_etat() == "VEILLE"
            and time.time() - last_conversation > 60
            and time.time() - last_question > 60
        ):
            last_question = time.time()
            parler(random.choice(QUESTIONS))

# ============================================================
# TELEPHONE
# ============================================================

def phone_page():
    return """<!doctype html>
<html lang='fr'>
<head>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='theme-color' content='#020611'>
<title>NOVA // PHONE</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#020611;color:#e8f5ff;font-family:Arial,sans-serif}
.w{max-width:680px;margin:auto;padding:14px}
.c{background:#07111f;border:1px solid #17354e;border-radius:16px;padding:14px;margin:12px 0}
h1{letter-spacing:7px;color:#67e8f9;font-size:28px;margin:4px 0 10px}
.status{font-size:12px;color:#6b98ad;margin-bottom:8px}
.row{display:flex;gap:8px;flex-wrap:wrap}
button{box-sizing:border-box;flex:1 1 145px;padding:14px;border-radius:11px;border:1px solid #1d5574;background:#0a2132;color:#e8f5ff;font-weight:700}
button.main{flex:1 1 100%;font-size:17px;border-color:#2a7694}
button.main.active{background:#0b3447;box-shadow:0 0 24px rgba(103,232,249,.20)}
button.on{border-color:#8cf6d6;background:#0a3029}
input{box-sizing:border-box;width:100%;padding:14px;border-radius:11px;border:1px solid #17354e;background:#030b15;color:white;font-size:16px;outline:none}
.send{width:100%;margin-top:8px}
.chat{height:50vh;min-height:320px;overflow:auto;white-space:pre-wrap;line-height:1.55;font-size:14px}
#live{font-size:12px;color:#7aa4b7;margin-top:8px;min-height:18px}
small{color:#5f8193;line-height:1.5}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#35556a;margin-right:6px}
.dot.on{background:#67e8f9;box-shadow:0 0 12px #67e8f9}
</style>
</head>
<body>
<div class='w'>
  <h1>NOVA</h1>

  <div class='c'>
    <div class='status' id='status'>Connexion...</div>
    <div class='row'>
      <button onclick="control('activate')">ACTIVER</button>
      <button onclick="control('deactivate')">PAUSE</button>
      <button onclick="control('toggle_fast')">MODE RAPIDE</button>
    </div>
  </div>

  <div class='c'>
    <button id='micBtn' class='main' onclick='toggleVoice()'>🎤 PARLER À NOVA</button>
    <div id='live'><span class='dot'></span>Appuie sur le micro et parle en français.</div>

    <div class='row' style='margin-top:8px'>
      <button id='phoneVoiceBtn' onclick='togglePhoneSpeech()'>🔊 RÉPONSES SUR LE TÉLÉPHONE : OFF</button>
      <button onclick='refresh()'>ACTUALISER</button>
    </div>

    <small>
      Le téléphone envoie directement ta transcription au NOVA qui tourne sur le PC.
      Sur Android, Chrome prend en charge la reconnaissance vocale Web Speech dans
      les cas compatibles.
    </small>
  </div>

  <div class='c'>
    <input id='msg' placeholder='Écris à NOVA...' autocomplete='off'
           onkeydown="if(event.key==='Enter')send()">
    <button class='send' onclick='send()'>ENVOYER À NOVA</button>
  </div>

  <div class='c'>
    <div id='chat' class='chat'>Chargement...</div>
  </div>
</div>

<script>
let recognition=null;
let listening=false;
let speechSupported=false;
let phoneSpeech=false;
let lastNova="";

function setLive(txt,on=false){
  document.getElementById('live').innerHTML =
    '<span class="dot '+(on?'on':'')+'"></span>'+txt;
}

function initVoice(){
  const R = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!R){
    speechSupported=false;
    document.getElementById('micBtn').textContent='🎤 MICRO NON DISPONIBLE';
    setLive('Ce navigateur ne fournit pas la reconnaissance vocale. Essaie Chrome sur Android.',false);
    return;
  }

  speechSupported=true;
  recognition=new R();
  recognition.lang='fr-FR';
  recognition.continuous=false;
  recognition.interimResults=true;
  recognition.maxAlternatives=3;

  recognition.onstart=()=>{
    listening=true;
    document.getElementById('micBtn').classList.add('active');
    document.getElementById('micBtn').textContent='⏹ ARRÊTER';
    setLive('NOVA écoute…',true);
  };

  recognition.onresult=(e)=>{
    let interim='';
    let finalText='';

    for(let i=e.resultIndex;i<e.results.length;i++){
      const s=e.results[i][0].transcript;
      if(e.results[i].isFinal) finalText += s;
      else interim += s;
    }

    if(interim) setLive('J’entends : « '+interim+' »',true);

    if(finalText.trim()){
      const text=finalText.trim();
      setLive('Message : « '+text+' »',false);
      sendText(text);
    }
  };

  recognition.onerror=(e)=>{
    let message='Micro : '+e.error;
    if(e.error==='not-allowed') message='Autorise le microphone pour ce site puis réessaie.';
    if(e.error==='no-speech') message='Je n’ai rien entendu. Réessaie.';
    if(e.error==='network') message='Le service vocal du navigateur est indisponible.';
    setLive(message,false);
    stopVoiceUI();
  };

  recognition.onend=()=>{
    stopVoiceUI();
  };
}

function stopVoiceUI(){
  listening=false;
  document.getElementById('micBtn').classList.remove('active');
  document.getElementById('micBtn').textContent='🎤 PARLER À NOVA';
}

function toggleVoice(){
  if(!speechSupported){
    initVoice();
    if(!speechSupported) return;
  }

  if(listening){
    try{recognition.stop()}catch(e){}
    stopVoiceUI();
    setLive('Écoute arrêtée.',false);
    return;
  }

  try{
    recognition.start();
  }catch(e){
    setLive('Le micro est déjà actif. Réessaie dans un instant.',false);
  }
}

async function sendText(text){
  if(!text.trim()) return;
  try{
    await fetch('/api/message',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:text.trim()})
    });
    await refresh();
  }catch(e){
    setLive('NOVA est momentanément inaccessible.',false);
  }
}

async function send(){
  const el=document.getElementById('msg');
  const text=el.value.trim();
  if(!text)return;
  el.value='';
  await sendText(text);
}

async function control(action){
  try{
    await fetch('/api/control',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action})
    });
    await refresh();
  }catch(e){}
}

function togglePhoneSpeech(){
  phoneSpeech=!phoneSpeech;
  const btn=document.getElementById('phoneVoiceBtn');

  if(phoneSpeech){
    btn.textContent='🔊 RÉPONSES SUR LE TÉLÉPHONE : ON';
    btn.classList.add('on');

    if(window.speechSynthesis){
      window.speechSynthesis.cancel();
      const u=new SpeechSynthesisUtterance('Voix téléphone activée.');
      u.lang='fr-FR';
      u.rate=1.0;
      window.speechSynthesis.speak(u);
    }
  }else{
    btn.textContent='🔊 RÉPONSES SUR LE TÉLÉPHONE : OFF';
    btn.classList.remove('on');
    if(window.speechSynthesis) window.speechSynthesis.cancel();
  }
}

function speakPhone(text){
  if(!phoneSpeech || !text || !window.speechSynthesis) return;
  if(text===lastNova) return;

  lastNova=text;
  window.speechSynthesis.cancel();

  const u=new SpeechSynthesisUtterance(text);
  u.lang='fr-FR';
  u.rate=1.0;
  u.pitch=1.0;
  window.speechSynthesis.speak(u);
}

async function refresh(){
  try{
    const d=await (await fetch('/api/status')).json();

    document.getElementById('status').textContent =
      d.active
      ? '● EN LIGNE — '+d.state+(d.fast?' — RAPIDE':'')
      : '○ EN PAUSE';

    const box=document.getElementById('chat');
    const atBottom=(box.scrollHeight-box.clientHeight-box.scrollTop)<90;
    box.textContent=d.chat;
    if(atBottom) box.scrollTop=box.scrollHeight;

    const rows=d.chat.split('\\n');
    let latest='';
    for(let i=rows.length-1;i>=0;i--){
      if(rows[i].startsWith('NOVA  >')){
        latest=rows[i].replace(/^NOVA\\s*>\\s*/,'').trim();
        break;
      }
    }

    if(latest && latest!==lastNova) speakPhone(latest);

  }catch(e){
    document.getElementById('status').textContent='○ HORS LIGNE';
  }
}

async function cmd(){
  try{
    const c=await (await fetch('/api/phone-command')).json();
    if(c.action==='ouvrir_url' && c.url) location.href=c.url;
  }catch(e){}
}

initVoice();
refresh();
setInterval(refresh,1000);
setInterval(cmd,700);
</script>
</body>
</html>"""

def phone_chat():
    with conv_lock:
        rows = conversation[-30:]

    lines = []
    for role, msg in rows:
        prefix = "NOVA  >" if role == "NOVA" else "TOI   >"
        lines.append(prefix + " " + msg)

    return "\n".join(lines)

def phone_response(conn, status, body, content_type="text/html; charset=utf-8"):
    data = body.encode("utf-8")
    response = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(data)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("utf-8") + data
    conn.sendall(response)

def phone_server():
    global nova_active, MODE_RAPIDE

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PHONE_HOST, PHONE_PORT))
    server.listen(8)
    server.settimeout(1.0)

    print()
    print("NOVA PHONE")
    print("Téléphone :", f"http://{get_local_ip()}:{PHONE_PORT}")
    print()

    while not phone_stop.is_set():
        try:
            conn, addr = server.accept()
        except socket.timeout:
            continue
        except Exception:
            break

        try:
            conn.settimeout(2)
            raw = conn.recv(16384).decode("utf-8", errors="ignore")

            if not raw:
                conn.close()
                continue

            first = raw.split("\r\n", 1)[0]
            parts = first.split(" ")
            method = parts[0] if len(parts) > 0 else ""
            path = parts[1] if len(parts) > 1 else "/"

            body = ""
            if "\r\n\r\n" in raw:
                body = raw.split("\r\n\r\n", 1)[1]

            if path == "/":
                phone_response(conn, "200 OK", phone_page())

            elif path == "/api/status":
                data = {
                    "active": nova_active,
                    "fast": MODE_RAPIDE,
                    "state": get_etat(),
                    "chat": phone_chat(),
                }
                phone_response(
                    conn,
                    "200 OK",
                    json.dumps(data, ensure_ascii=False),
                    "application/json; charset=utf-8"
                )

            elif path == "/api/phone-command" and method == "GET":
                phone_response(conn, "200 OK", json.dumps(prendre_commande_telephone() or {}, ensure_ascii=False), "application/json; charset=utf-8")

            elif path == "/api/control" and method == "POST":
                try:
                    data = json.loads(body or "{}")
                except Exception:
                    data = {}

                action = data.get("action")

                if action == "activate":
                    nova_active = True
                    set_etat("VEILLE")
                elif action == "deactivate":
                    nova_active = False
                    set_etat("PAUSE")
                elif action == "toggle_fast":
                    MODE_RAPIDE = not MODE_RAPIDE

                phone_response(
                    conn,
                    "200 OK",
                    json.dumps({"ok": True}),
                    "application/json"
                )

            elif path == "/api/message" and method == "POST":
                try:
                    data = json.loads(body or "{}")
                    text_msg = str(data.get("text", "")).strip()
                except Exception:
                    text_msg = ""

                if text_msg:
                    threading.Thread(
                        target=traiter,
                        args=(text_msg,),
                        daemon=True
                    ).start()

                phone_response(
                    conn,
                    "200 OK",
                    json.dumps({"ok": True}),
                    "application/json"
                )

            else:
                phone_response(conn, "404 Not Found", "Not found")

        except Exception as exc:
            print("Phone :", exc)

        finally:
            try:
                conn.close()
            except Exception:
                pass

    try:
        server.close()
    except Exception:
        pass

# ============================================================
# INTERFACE 2D
# ============================================================

class Particle:
    def __init__(self, width, height):
        self.reset(width, height, True)

    def reset(self, width, height, initial=False):
        self.x = random.uniform(0, width)
        self.y = random.uniform(0, height)
        self.vx = random.uniform(-0.25, 0.25)
        self.vy = random.uniform(-0.18, 0.18)
        self.size = random.choice([1, 1, 1, 2])
        self.alpha = random.uniform(0.25, 0.9)
        self.phase = random.uniform(0, math.tau)

        if initial:
            self.x = random.uniform(0, width)
            self.y = random.uniform(0, height)

class NovaUI:
    def __init__(self):
        self.root = Tk()
        self.root.title("NOVA")
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg=BG)

        self.w = self.root.winfo_screenwidth()
        self.h = self.root.winfo_screenheight()

        self.canvas = Canvas(
            self.root,
            bg=BG,
            highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.particles = [
            Particle(self.w, self.h)
            for _ in range(240)
        ]

        self.t = 0.0
        self.running = True

        self.build_overlay()
        self.root.bind("<Escape>", lambda e: self.close())

        self.animate()
        self.refresh_chat()

    def build_overlay(self):
        self.canvas.create_text(38,34,text="N O V A",anchor="w",fill=CYAN,font=("Segoe UI",23,"bold"),tags="ui")
        self.state_text=self.canvas.create_text(40,70,text="SYSTEM • VEILLE",anchor="w",fill=MUTED,font=("Consolas",10),tags="ui")
        self.live_text=self.canvas.create_text(self.w-38,36,text="● LOCAL AI",anchor="e",fill=BLUE,font=("Consolas",10,"bold"),tags="ui")
        self.fast_button=Button(self.root,text="MODE RAPIDE : OFF",command=self.toggle_fast,bg="#0b1d31",fg=CYAN,relief="flat",font=("Consolas",9,"bold"))
        self.canvas.create_window(self.w-38,76,anchor="e",window=self.fast_button)
        x,y,w,h=36,108,min(500,max(360,self.w*.34)),self.h-170
        self.canvas.create_rectangle(x,y,x+w,y+h,outline="#1a4664",fill="#06101d",tags="ui")
        self.canvas.create_text(x+18,y+21,text="CONVERSATION",anchor="w",fill=CYAN,font=("Consolas",10,"bold"),tags="ui")
        self.chat_widget=Text(self.root,bg="#06101d",fg=WHITE,relief="flat",bd=0,wrap="word",font=("Segoe UI",11),padx=14,pady=10)
        self.canvas.create_window(x+10,y+45,anchor="nw",width=w-20,height=h-55,window=self.chat_widget)
        self.cx=x+w+(self.w-(x+w))*.52;self.cy=self.h*.49;cx,cy=self.cx,self.cy
        self.canvas.create_text(cx,125,text="NOVA CORE",fill=CYAN,font=("Consolas",11,"bold"),tags="ui")
        for dx,dy in [(-250,0),(250,0),(0,-190),(0,190)]:self.canvas.create_line(cx+dx*.76,cy+dy*.76,cx+dx,cy+dy,fill=LINE,tags="ui")
        self.center_label=self.canvas.create_text(cx,cy+225,text="NOVA / READY",fill=MUTED,font=("Consolas",10),tags="ui")
        self.canvas.create_text(self.w-38,self.h-32,text="VOICE • PHONE CONTROL • MODE RAPIDE • ESC TO EXIT",anchor="e",fill=MUTED,font=("Consolas",9),tags="ui")

    def toggle_fast(self):
        global MODE_RAPIDE
        MODE_RAPIDE = not MODE_RAPIDE
        self.fast_button.config(text="MODE RAPIDE : ON" if MODE_RAPIDE else "MODE RAPIDE : OFF")

    def current_color(self):
        state = get_etat()
        if state == "ECOUTE":
            return CYAN
        if state == "REFLEXION":
            return VIOLET
        if state == "PARLE":
            return "#fbbf24"
        if state == "PAUSE":
            return "#4b5563"
        return BLUE

    def update_state(self):
        state = get_etat()
        if hasattr(self, "fast_button"):
            self.fast_button.config(text="MODE RAPIDE : ON" if MODE_RAPIDE else "MODE RAPIDE : OFF")
        color = self.current_color()

        labels = {
            "VEILLE": "SYSTEM • VEILLE",
            "ECOUTE": "SYSTEM • ÉCOUTE",
            "REFLEXION": "SYSTEM • RÉFLEXION",
            "PARLE": "SYSTEM • PAROLE",
            "PAUSE": "SYSTEM • PAUSE",
        }

        self.canvas.itemconfigure(
            self.state_text,
            text=labels.get(state, state),
            fill=color
        )

        self.canvas.itemconfigure(
            self.live_text,
            text="● LOCAL AI" if nova_active else "○ PAUSED",
            fill=color
        )

        self.canvas.itemconfigure(
            self.center_label,
            text="NOVA / " + state,
            fill=color
        )

    def draw_particles(self):
        state = get_etat()
        speed = {
            "VEILLE": 0.55,
            "ECOUTE": 1.25,
            "REFLEXION": 0.85,
            "PARLE": 1.65,
            "PAUSE": 0.15
        }.get(state, 0.55)

        color = self.current_color()

        # Nettoyage uniquement des particules de la frame précédente
        self.canvas.delete("particle")
        self.canvas.delete("trail")

        pulse = 1.0
        if state == "PARLE":
            pulse = 1.0 + 0.16 * math.sin(self.t * 9)
        elif state == "ECOUTE":
            pulse = 1.0 + 0.08 * math.sin(self.t * 6)

        for p in self.particles:
            p.x += p.vx * speed
            p.y += p.vy * speed

            if p.x < -20 or p.x > self.w + 20:
                p.x = random.uniform(0, self.w)
            if p.y < -20 or p.y > self.h + 20:
                p.y = random.uniform(0, self.h)

            # Attraction douce vers la zone IA
            dx = self.cx - p.x
            dy = self.cy - p.y
            dist = math.sqrt(dx * dx + dy * dy) + 1

            if dist < 330:
                force = (330 - dist) / 330 * 0.012 * speed
                p.vx += dx / dist * force
                p.vy += dy / dist * force

            # Limite vitesse
            p.vx = max(-1.4, min(1.4, p.vx))
            p.vy = max(-1.4, min(1.4, p.vy))

            twinkle = 0.5 + 0.5 * math.sin(
                self.t * 2 + p.phase
            )
            size = max(
                1,
                int(p.size * (0.8 + twinkle * 0.7) * pulse)
            )

            self.canvas.create_oval(
                p.x - size,
                p.y - size,
                p.x + size,
                p.y + size,
                fill=color,
                outline="",
                tags="particle"
            )

        # Flux radiaux très fins
        for i in range(18):
            angle = i * (math.tau / 18) + self.t * 0.10
            length = 90 + 35 * math.sin(
                self.t * 1.7 + i
            )

            x1 = self.cx + math.cos(angle) * 55
            y1 = self.cy + math.sin(angle) * 55
            x2 = self.cx + math.cos(angle) * length
            y2 = self.cy + math.sin(angle) * length

            self.canvas.create_line(
                x1, y1, x2, y2,
                fill=color,
                width=1,
                tags="trail"
            )

        # Oscilloscope horizontal quand NOVA parle
        if state == "PARLE":
            points = []
            base_y = self.cy + 275
            for x in range(int(self.cx - 230), int(self.cx + 230), 8):
                wave = math.sin(
                    x * 0.065 + self.t * 11
                ) * 12
                wave += math.sin(
                    x * 0.19 - self.t * 7
                ) * 5
                points.extend([x, base_y + wave])

            if len(points) >= 4:
                self.canvas.create_line(
                    *points,
                    fill=color,
                    width=1,
                    tags="trail"
                )

    def refresh_chat(self):
        if not self.running:
            return

        rows = historique_recent(14)

        self.chat_widget.config(state="normal")
        self.chat_widget.delete("1.0", END)

        for role, msg in rows:
            prefix = "NOVA  " if role == "NOVA" else "TOI   "
            self.chat_widget.insert(
                END,
                prefix + "› " + msg + "\n\n"
            )

        self.chat_widget.see(END)
        self.chat_widget.config(state="disabled")

        self.root.after(700, self.refresh_chat)

    def animate(self):
        if not self.running:
            return

        self.t += 0.035
        self.update_state()
        self.draw_particles()

        # Les particules sont au-dessus du fond mais sous les textes.
        self.canvas.tag_raise("ui")
        self.root.after(33, self.animate)

    def close(self):
        self.running = False
        audio_stop.set()
        phone_stop.set()
        try:
            self.root.destroy()
        except Exception:
            pass

# ============================================================
# MAIN
# ============================================================

def main():
    global nova_active, NOVA_UI

    print("=" * 60)
    print("NOVA PHONE - interface 2D")
    print("=" * 60)
    print("Mains : DESACTIVEES")
    print("Webcam : DESACTIVEE")
    print("Sphère 3D : SUPPRIMEE")
    print("Interface : particules 2D + conversation")
    print()

    ip = get_local_ip()
    print(f"Téléphone : http://{ip}:{PHONE_PORT}")
    if winmm:
        try:
            print("Micros Windows détectés :", int(winmm.waveInGetNumDevs()))
        except Exception:
            print("Micros Windows : impossible à vérifier")
    print("Mode rapide :", "ACTIF" if MODE_RAPIDE else "DESACTIVE")
    print("Marge compréhension : ACTIVE")
    print()

    threading.Thread(
        target=phone_server,
        daemon=True
    ).start()

    threading.Thread(
        target=voice_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=initiative_loop,
        daemon=True
    ).start()

    ui = NovaUI()
    NOVA_UI = ui

    try:
        ui.root.mainloop()
    finally:
        audio_stop.set()
        phone_stop.set()

if __name__ == "__main__":
    main()
