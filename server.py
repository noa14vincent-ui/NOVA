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
    "abbeville': 'Abbeville',
    'acheres': 'Achères',
    'agde': 'Agde',
    'agen': 'Agen',
    'aigueze': 'Aiguèze',
    'ainhoa': 'Ainhoa',
    'aix en provence': 'Aix-en-Provence',
    'aix les bains': 'Aix-les-Bains',
    'ajaccio': 'Ajaccio',
    'albi': 'Albi',
    'alencon': 'Alençon',
    'ales': 'Alès',
    'alfortville': 'Alfortville',
    'allauch': 'Allauch',
    'amiens': 'Amiens',
    'angers': 'Angers',
    'angles sur l anglin': "Angles-sur-l'Anglin",
    'anglet': 'Anglet',
    'angouleme': 'Angoulême',
    'annecy': 'Annecy',
    "Régnié-Durrette: "Régnié-Durrette",
    'annemasse': 'Annemasse',
    'antibes': 'Antibes',
    'antony': 'Antony',
    'arcueil': 'Arcueil',
    'argenteuil': 'Argenteuil',
    'arles': 'Arles',
    'armentieres': 'Armentières',
    'arras': 'Arras',
    'asnieres sur seine': 'Asnières-sur-Seine',
    'athis mons': 'Athis-Mons',
    'aubagne': 'Aubagne',
    'aubervilliers': 'Aubervilliers',
    'auch': 'Auch',
    'aulnay sous bois': 'Aulnay-sous-Bois',
    'aurillac': 'Aurillac',
    'autoire': 'Autoire',
    'auxerre': 'Auxerre',
    'avignon': 'Avignon',
    'bagneux': 'Bagneux',
    'bagnolet': 'Bagnolet',
    'baie mahault': 'Baie-Mahault',
    'balazuc': 'Balazuc',
    'barfleur': 'Barfleur',
    'bastia': 'Bastia',
    'baume les dames': 'Baume-les-Dames',
    'baume les messieurs': 'Baume-les-Messieurs',
    'bayonne': 'Bayonne',
    'beaune': 'Beaune',
    'beaupreau en mauges': 'Beaupréau-en-Mauges',
    'beauvais': 'Beauvais',
    'begles': 'Bègles',
    'belcastel': 'Belcastel',
    'belfort': 'Belfort',
    'bergerac': 'Bergerac',
    'besancon': 'Besançon',
    'bethune': 'Béthune',
    'beuvron en auge': 'Beuvron-en-Auge',
    'beynac et cazenac': 'Beynac-et-Cazenac',
    'beziers': 'Béziers',
    'bezons': 'Bezons',
    'biarritz': 'Biarritz',
    'blagnac': 'Blagnac',
    'blois': 'Blois',
    'bobigny': 'Bobigny',
    'bois colombes': 'Bois-Colombes',
    'bondy': 'Bondy',
    'bonnieux': 'Bonnieux',
    'bordeaux': 'Bordeaux',
    'bouguenais': 'Bouguenais',
    'boulogne billancourt': 'Boulogne-Billancourt',
    'boulogne sur mer': 'Boulogne-sur-Mer',
    'bourg en bresse': 'Bourg-en-Bresse',
    'bourg la reine': 'Bourg-la-Reine',
    'bourges': 'Bourges',
    'bourgoin jallieu': 'Bourgoin-Jallieu',
    'brest': 'Brest',
    'bretigny sur orge': 'Brétigny-sur-Orge',
    'brive la gaillarde': 'Brive-la-Gaillarde',
    'bron': 'Bron',
    'brousse le chateau': 'Brousse-le-Château',
    'bruay la buissiere': 'Bruay-la-Buissière',
    'bruges': 'Bruges',
    'bruniquel': 'Bruniquel',
    'brunoy': 'Brunoy',
    'bussy saint georges': 'Bussy-Saint-Georges',
    'cachan': 'Cachan',
    'caen': 'Caen',
    'cagnes sur mer': 'Cagnes-sur-Mer',
    'cahors': 'Cahors',
    'calais': 'Calais',
    'caluire et cuire': 'Caluire-et-Cuire',
    'cambrai': 'Cambrai',
    'camon': 'Camon',
    'candes saint martin': 'Candes-Saint-Martin',
    'cannes': 'Cannes',
    'carcassonne': 'Carcassonne',
    'carennac': 'Carennac',
    'carpentras': 'Carpentras',
    'carquefou': 'Carquefou',
    'carrieres sous poissy': 'Carrières-sous-Poissy',
    'castelmoron d albret': "Castelmoron-d'Albret",
    'castelnau le lez': 'Castelnau-le-Lez',
    'castelnaud la chapelle': 'Castelnaud-la-Chapelle',
    'castres': 'Castres',
    'cavaillon': 'Cavaillon',
    'cayenne': 'Cayenne',
    'cenon': 'Cenon',
    'cergy': 'Cergy',
    'challans': 'Challans',
    'chalon sur saone': 'Chalon-sur-Saône',
    'chalons en champagne': 'Châlons-en-Champagne',
    'chambery': 'Chambéry',
    'champigny sur marne': 'Champigny-sur-Marne',
    'champs sur marne': 'Champs-sur-Marne',
    'charenton le pont': 'Charenton-le-Pont',
    'charleville mezieres': 'Charleville-Mézières',
    'charroux': 'Charroux',
    'chartres': 'Chartres',
    'chateau chalon': 'Château-Chalon',
    'chateauroux': 'Châteauroux',
    'chatellerault': 'Châtellerault',
    'chatenay malabry': 'Châtenay-Malabry',
    'chatillon': 'Châtillon',
    'chatou': 'Chatou',
    'chaumont': 'Chaumont',
    'chaville': 'Chaville',
    'chelles': 'Chelles',
    'chemille en anjou': 'Chemillé-en-Anjou',
    'cherbourg en cotentin': 'Cherbourg-en-Cotentin',
    'chilly mazarin': 'Chilly-Mazarin',
    'choisy le roi': 'Choisy-le-Roi',
    'cholet': 'Cholet',
    'clamart': 'Clamart',
    'clermont ferrand': 'Clermont-Ferrand',
    'clichy': 'Clichy',
    'clichy sous bois': 'Clichy-sous-Bois',
    'coaraze': 'Coaraze',
    'collonges la rouge': 'Collonges-la-Rouge',
    'colmar': 'Colmar',
    'colombes': 'Colombes',
    'colomiers': 'Colomiers',
    'combs la ville': 'Combs-la-Ville',
    'compiegne': 'Compiègne',
    'concarneau': 'Concarneau',
    'conflans sainte honorine': 'Conflans-Sainte-Honorine',
    'conques': 'Conques',
    'corbeil essonnes': 'Corbeil-Essonnes',
    'cordes sur ciel': 'Cordes-sur-Ciel',
    'cormeilles en parisis': 'Cormeilles-en-Parisis',
    'coudekerque branche': 'Coudekerque-Branche',
    'coueron': 'Couëron',
    'courbevoie': 'Courbevoie',
    'creil': 'Creil',
    'creteil': 'Créteil',
    'crissay sur manse': 'Crissay-sur-Manse',
    'croix': 'Croix',
    'cugnaux': 'Cugnaux',
    'curemonte': 'Curemonte',
    'dammarie les lys': 'Dammarie-les-Lys',
    'dax': 'Dax',
    'decines charpieu': 'Décines-Charpieu',
    'denain': 'Denain',
    'deuil la barre': 'Deuil-la-Barre',
    'dieppe': 'Dieppe',
    'dijon': 'Dijon',
    'dole': 'Dole',
    'domme': 'Domme',
    'douai': 'Douai',
    'draguignan': 'Draguignan',
    'drancy': 'Drancy',
    'draveil': 'Draveil',
    'dreux': 'Dreux',
    'dumbea': 'Dumbéa',
    'dunkerque': 'Dunkerque',
    'eaubonne': 'Eaubonne',
    'echirolles': 'Échirolles',
    'eguisheim': 'Eguisheim',
    'elancourt': 'Élancourt',
    'epernay': 'Épernay',
    'epinal': 'Épinal',
    'epinay sur seine': 'Épinay-sur-Seine',
    'ermont': 'Ermont',
    'espelette': 'Espelette',
    'estaing': 'Estaing',
    'etampes': 'Étampes',
    'etretat': 'Étretat',
    'evreux': 'Évreux',
    'evry courcouronnes': 'Évry-Courcouronnes',
    'eysines': 'Eysines',
    'eze': 'Èze',
    'faʻaʻā': 'Faʻaʻā',
    'flavigny sur ozerain': 'Flavigny-sur-Ozerain',
    'fleury les aubrais': 'Fleury-les-Aubrais',
    'fontaine': 'Fontaine',
    'fontenay aux roses': 'Fontenay-aux-Roses',
    'fontenay sous bois': 'Fontenay-sous-Bois',
    'forbach': 'Forbach',
    'fort de france': 'Fort-de-France',
    'fougeres': 'Fougères',
    'franconville': 'Franconville',
    'frejus': 'Fréjus',
    'fresnes': 'Fresnes',
    'frontignan': 'Frontignan',
    'gagny': 'Gagny',
    'gap': 'Gap',
    'gardanne': 'Gardanne',
    'garges les gonesse': 'Garges-lès-Gonesse',
    'gennevilliers': 'Gennevilliers',
    'gerberoy': 'Gerberoy',
    'gif sur yvette': 'Gif-sur-Yvette',
    'givors': 'Givors',
    'gonesse': 'Gonesse',
    'gordes': 'Gordes',
    'gourdon': 'Gourdon',
    'goussainville': 'Goussainville',
    'gradignan': 'Gradignan',
    'grande synthe': 'Grande-Synthe',
    'grasse': 'Grasse',
    'grenoble': 'Grenoble',
    'grigny': 'Grigny',
    'gujan mestras': 'Gujan-Mestras',
    'guyancourt': 'Guyancourt',
    'haguenau': 'Haguenau',
    'halluin': 'Halluin',
    'hazebrouck': 'Hazebrouck',
    'henin beaumont': 'Hénin-Beaumont',
    'herblay sur seine': 'Herblay-sur-Seine',
    'herouville saint clair': 'Hérouville-Saint-Clair',
    'houilles': 'Houilles',
    'hunawihr': 'Hunawihr',
    'hunspach': 'Hunspach',
    'hyeres': 'Hyères',
    'illkirch graffenstaden': 'Illkirch-Graffenstaden',
    'issy les moulineaux': 'Issy-les-Moulineaux',
    'istres': 'Istres',
    'ivry sur seine': 'Ivry-sur-Seine',
    'joinville le pont': 'Joinville-le-Pont',
    'joue les tours': 'Joué-lès-Tours',
    'kaysersberg': 'Kaysersberg',
    'kaysersberg vignoble': 'Kaysersberg-Vignoble',
    'koungou': 'Koungou',
    'kourou': 'Kourou',
    'l haÿ les roses': "L'Haÿ-les-Roses",
    'l isle sur la sorgue': "L'Isle-sur-la-Sorgue",
    'la celle saint cloud': 'La Celle-Saint-Cloud',
    'la chapelle sur erdre': 'La Chapelle-sur-Erdre',
    'la ciotat': 'La Ciotat',
    'la courneuve': 'La Courneuve',
    'la garde': 'La Garde',
    'la garenne colombes': 'La Garenne-Colombes',
    'la madeleine': 'La Madeleine',
    'la possession': 'La Possession',
    'la roche guyon': 'La Roche-Guyon',
    'la roche sur yon': 'La Roche-sur-Yon',
    'la rochelle': 'La Rochelle',
    'la roque gageac': 'La Roque-Gageac',
    'la seyne sur mer': 'La Seyne-sur-Mer',
    'la teste de buch': 'La Teste-de-Buch',
    'la valette du var': 'La Valette-du-Var',
    'lacoste': 'Lacoste',
    'lagny sur marne': 'Lagny-sur-Marne',
    'lagrasse': 'Lagrasse',
    'lambersart': 'Lambersart',
    'lanester': 'Lanester',
    'lannion': 'Lannion',
    'laon': 'Laon',
    'laval': 'Laval',
    'lavardin': 'Lavardin',
    'le blanc mesnil': 'Le Blanc-Mesnil',
    'le bouscat': 'Le Bouscat',
    'le cannet': 'Le Cannet',
    'le chesnay rocquencourt': 'Le Chesnay-Rocquencourt',
    'le creusot': 'Le Creusot',
    'le gosier': 'Le Gosier',
    'le grand quevilly': 'Le Grand-Quevilly',
    'le havre': 'Le Havre',
    'le kremlin bicetre': 'Le Kremlin-Bicêtre',
    'le lamentin': 'Le Lamentin',
    'le mans': 'Le Mans',
    'le mont dore': 'Le Mont-Dore',
    'le moule': 'Le Moule',
    'le perreux sur marne': 'Le Perreux-sur-Marne',
    'le petit quevilly': 'Le Petit-Quevilly',
    'le plessis robinson': 'Le Plessis-Robinson',
    'le plessis trevise': 'Le Plessis-Trévise',
    'le port': 'Le Port',
    'le robert': 'Le Robert',
    'le tampon': 'Le Tampon',
    'lens': 'Lens',
    'les abymes': 'Les Abymes',
    'les baux de provence': 'Les Baux-de-Provence',
    'les lilas': 'Les Lilas',
    'les mureaux': 'Les Mureaux',
    'les pavillons sous bois': 'Les Pavillons-sous-Bois',
    'les pennes mirabeau': 'Les Pennes-Mirabeau',
    'les sables d olonne': "Les Sables-d'Olonne",
    'les ulis': 'Les Ulis',
    'levallois perret': 'Levallois-Perret',
    'libourne': 'Libourne',
    'lievin': 'Liévin',
    'lille': 'Lille',
    'limeil brevannes': 'Limeil-Brévannes',
    'limeuil': 'Limeuil',
    'limoges': 'Limoges',
    'lingolsheim': 'Lingolsheim',
    'livry gargan': 'Livry-Gargan',
    'locronan': 'Locronan',
    'longjumeau': 'Longjumeau',
    'loos': 'Loos',
    'lorient': 'Lorient',
    'lormont': 'Lormont',
    'loubressac': 'Loubressac',
    'lourmarin': 'Lourmarin',
    'lunel': 'Lunel',
    'lyon': 'Lyon',
    'macon': 'Mâcon',
    'maisons alfort': 'Maisons-Alfort',
    'maisons laffitte': 'Maisons-Laffitte',
    'malakoff': 'Malakoff',
    'mamoudzou': 'Mamoudzou',
    'mandelieu la napoule': 'Mandelieu-la-Napoule',
    'manosque': 'Manosque',
    'mantes la jolie': 'Mantes-la-Jolie',
    'mantes la ville': 'Mantes-la-Ville',
    'marcq en barœul': 'Marcq-en-Barœul',
    'marignane': 'Marignane',
    'marseille': 'Marseille',
    'martel': 'Martel',
    'martigues': 'Martigues',
    'massy': 'Massy',
    'matoury': 'Matoury',
    'maubeuge': 'Maubeuge',
    'maurepas': 'Maurepas',
    'meaux': 'Meaux',
    'melun': 'Melun',
    'menerbes': 'Ménerbes',
    'menton': 'Menton',
    'merignac': 'Mérignac',
    'metz': 'Metz',
    'meudon': 'Meudon',
    'meyzieu': 'Meyzieu',
    'millau': 'Millau',
    'minerve': 'Minerve',
    'miramas': 'Miramas',
    'mirepoix': 'Mirepoix',
    'mitry mory': 'Mitry-Mory',
    'moncontour': 'Moncontour',
    'monflanquin': 'Monflanquin',
    'monpazier': 'Monpazier',
    'mons en barœul': 'Mons-en-Barœul',
    'mont de marsan': 'Mont-de-Marsan',
    'mont saint aignan': 'Mont-Saint-Aignan',
    'montaigu vendee': 'Montaigu-Vendée',
    'montauban': 'Montauban',
    'montbeliard': 'Montbéliard',
    'montelimar': 'Montélimar',
    'montereau fault yonne': 'Montereau-Fault-Yonne',
    'montfermeil': 'Montfermeil',
    'montgeron': 'Montgeron',
    'montigny le bretonneux': 'Montigny-le-Bretonneux',
    'montigny les cormeilles': 'Montigny-lès-Cormeilles',
    'montigny les metz': 'Montigny-lès-Metz',
    'montlucon': 'Montluçon',
    'montmorency': 'Montmorency',
    'montpellier': 'Montpellier',
    'montresor': 'Montrésor',
    'montreuil': 'Montreuil',
    'montrouge': 'Montrouge',
    'montsoreau': 'Montsoreau',
    'morsang sur orge': 'Morsang-sur-Orge',
    'moustiers sainte marie': 'Moustiers-Sainte-Marie',
    'mulhouse': 'Mulhouse',
    'muret': 'Muret',
    'najac': 'Najac',
    'nancy': 'Nancy',
    'nanterre': 'Nanterre',
    'nantes': 'Nantes',
    'narbonne': 'Narbonne',
    'neuilly plaisance': 'Neuilly-Plaisance',
    'neuilly sur marne': 'Neuilly-sur-Marne',
    'neuilly sur seine': 'Neuilly-sur-Seine',
    'nevers': 'Nevers',
    'nice': 'Nice',
    'niedermorschwihr': 'Niedermorschwihr',
    'nimes': 'Nîmes',
    'niort': 'Niort',
    'nogent sur marne': 'Nogent-sur-Marne',
    'nogent sur oise': 'Nogent-sur-Oise',
    'noisy le grand': 'Noisy-le-Grand',
    'noisy le sec': 'Noisy-le-Sec',
    'noumea': 'Nouméa',
    'noyers sur serein': 'Noyers-sur-Serein',
    'olivet': 'Olivet',
    'oppede': 'Oppède',
    'orange': 'Orange',
    'orleans': 'Orléans',
    'orly': 'Orly',
    'orvault': 'Orvault',
    'oullins pierre benite': 'Oullins-Pierre-Bénite',
    'oyonnax': 'Oyonnax',
    'ozoir la ferriere': 'Ozoir-la-Ferrière',
    'paita': 'Païta',
    'palaiseau': 'Palaiseau',
    'pantin': 'Pantin',
    'papeete': 'Papeete',
    'paris': 'Paris',
    'pau': 'Pau',
    'penne': 'Penne',
    'penne d agenais': "Penne-d'Agenais",
    'perigueux': 'Périgueux',
    'perouges': 'Pérouges',
    'perpignan': 'Perpignan',
    'pessac': 'Pessac',
    'petit bourg': 'Petit-Bourg',
    'peyre': 'Peyre',
    'plaisance du touch': 'Plaisance-du-Touch',
    'plaisir': 'Plaisir',
    'poissy': 'Poissy',
    'poitiers': 'Poitiers',
    'pontault combault': 'Pontault-Combault',
    'pontoise': 'Pontoise',
    'pujols': 'Pujols',
    'punaʻauia': 'Punaʻauia',
    'puteaux': 'Puteaux',
    'puycelsi': 'Puycelsi',
    'quimper': 'Quimper',
    'rambouillet': 'Rambouillet',
    'reims': 'Reims',
    'remire montjoly': 'Remire-Montjoly',
    'rennes': 'Rennes',
    'reze': 'Rezé',
    'ribeauville': 'Ribeauvillé',
    'rillieux la pape': 'Rillieux-la-Pape',
    'riquewihr': 'Riquewihr',
    'ris orangis': 'Ris-Orangis',
    'roanne': 'Roanne',
    'rocamadour': 'Rocamadour',
    'rochefort': 'Rochefort',
    'rochefort en terre': 'Rochefort-en-Terre',
    'rodez': 'Rodez',
    'roissy en brie': 'Roissy-en-Brie',
    'romainville': 'Romainville',
    'romans sur isere': 'Romans-sur-Isère',
    'rosny sous bois': 'Rosny-sous-Bois',
    'roubaix': 'Roubaix',
    'rouen': 'Rouen',
    'roussillon': 'Roussillon',
    'rueil malmaison': 'Rueil-Malmaison',
    'saint amand de coly': 'Saint-Amand-de-Coly',
    'saint andre': 'Saint-André',
    'saint benoit': 'Saint-Benoît',
    'saint bertrand de comminges': 'Saint-Bertrand-de-Comminges',
    'saint brieuc': 'Saint-Brieuc',
    'saint chamond': 'Saint-Chamond',
    'saint cirq lapopie': 'Saint-Cirq-Lapopie',
    'saint cloud': 'Saint-Cloud',
    'saint cyr l ecole': "Saint-Cyr-l'École",
    'saint denis': 'Saint-Denis',
    'saint dizier': 'Saint-Dizier',
    'saint etienne': 'Saint-Étienne',
    'saint etienne du rouvray': 'Saint-Étienne-du-Rouvray',
    'saint genis laval': 'Saint-Genis-Laval',
    'saint germain en laye': 'Saint-Germain-en-Laye',
    'saint gratien': 'Saint-Gratien',
    'saint herblain': 'Saint-Herblain',
    'saint jean de braye': 'Saint-Jean-de-Braye',
    'saint jean de cole': 'Saint-Jean-de-Côle',
    'saint jean pied de port': 'Saint-Jean-Pied-de-Port',
    'saint joseph': 'Saint-Joseph',
    'saint laurent du maroni': 'Saint-Laurent-du-Maroni',
    'saint laurent du var': 'Saint-Laurent-du-Var',
    'saint leon sur vezere': 'Saint-Léon-sur-Vézère',
    'saint leu': 'Saint-Leu',
    'saint louis': 'Saint-Louis',
    'saint malo': 'Saint-Malo',
    'saint mande': 'Saint-Mandé',
    'saint martin': 'Saint-Martin',
    'saint martin d heres': "Saint-Martin-d'Hères",
    'saint maur des fosses': 'Saint-Maur-des-Fossés',
    'saint medard en jalles': 'Saint-Médard-en-Jalles',
    'saint michel sur orge': 'Saint-Michel-sur-Orge',
    'saint nazaire': 'Saint-Nazaire',
    'saint ouen l aumone': "Saint-Ouen-l'Aumône",
    'saint ouen sur seine': 'Saint-Ouen-sur-Seine',
    'saint paul': 'Saint-Paul',
    'saint paul de vence': 'Saint-Paul-de-Vence',
    'saint pierre': 'Saint-Pierre',
    'saint priest': 'Saint-Priest',
    'saint quentin': 'Saint-Quentin',
    'saint raphael': 'Saint-Raphaël',
    'saint sebastien sur loire': 'Saint-Sébastien-sur-Loire',
    'saint suliac': 'Saint-Suliac',
    'sainte anne': 'Sainte-Anne',
    'sainte enimie': 'Sainte-Enimie',
    'sainte foy les lyon': 'Sainte-Foy-lès-Lyon',
    'sainte genevieve des bois': 'Sainte-Geneviève-des-Bois',
    'sainte marie': 'Sainte-Marie',
    'sainte suzanne': 'Sainte-Suzanne',
    'saintes': 'Saintes',
    'salers': 'Salers',
    'salon de provence': 'Salon-de-Provence',
    'sannois': 'Sannois',
    'sarcelles': 'Sarcelles',
    'sare': 'Sare',
    'sarreguemines': 'Sarreguemines',
    'sartrouville': 'Sartrouville',
    'saumur': 'Saumur',
    'sauveterre de rouergue': 'Sauveterre-de-Rouergue',
    'savigny le temple': 'Savigny-le-Temple',
    'savigny sur orge': 'Savigny-sur-Orge',
    'sceaux': 'Sceaux',
    'schiltigheim': 'Schiltigheim',
    'segur le chateau': 'Ségur-le-Château',
    'seguret': 'Séguret',
    'semur en auxois': 'Semur-en-Auxois',
    'semur en brionnais': 'Semur-en-Brionnais',
    'sens': 'Sens',
    'sete': 'Sète',
    'sevran': 'Sevran',
    'sevremoine': 'Sèvremoine',
    'sevres': 'Sèvres',
    'six fours les plages': 'Six-Fours-les-Plages',
    'soissons': 'Soissons',
    'sospel': 'Sospel',
    'sotteville les rouen': 'Sotteville-lès-Rouen',
    'stains': 'Stains',
    'strasbourg': 'Strasbourg',
    'sucy en brie': 'Sucy-en-Brie',
    'suresnes': 'Suresnes',
    'talence': 'Talence',
    'talloires': 'Talloires',
    'tarbes': 'Tarbes',
    'tassin la demi lune': 'Tassin-la-Demi-Lune',
    'taverny': 'Taverny',
    'thiais': 'Thiais',
    'thionville': 'Thionville',
    'thonon les bains': 'Thonon-les-Bains',
    'torcy': 'Torcy',
    'toulon': 'Toulon',
    'toulouse': 'Toulouse',
    'tourcoing': 'Tourcoing',
    'tournefeuille': 'Tournefeuille',
    'tournemire': 'Tournemire',
    'tournon d agenais': "Tournon-d'Agenais",
    'tourrettes sur loup': 'Tourrettes-sur-Loup',
    'tours': 'Tours',
    'trappes': 'Trappes',
    'tremblay en france': 'Tremblay-en-France',
    'troyes': 'Troyes',
    'turckheim': 'Turckheim',
    'turenne': 'Turenne',
    'valence': 'Valence',
    'valenciennes': 'Valenciennes',
    'vallauris': 'Vallauris',
    'vandœuvre les nancy': 'Vandœuvre-lès-Nancy',
    'vannes': 'Vannes',
    'vanves': 'Vanves',
    'vaulx en velin': 'Vaulx-en-Velin',
    'velizy villacoublay': 'Vélizy-Villacoublay',
    'venasque': 'Venasque',
    'venissieux': 'Vénissieux',
    'vernon': 'Vernon',
    'versailles': 'Versailles',
    'vertou': 'Vertou',
    'vetheuil': 'Vétheuil',
    'veules les roses': 'Veules-les-Roses',
    'vezelay': 'Vézelay',
    'vichy': 'Vichy',
    'vienne': 'Vienne',
    'vierzon': 'Vierzon',
    'vigneux sur seine': 'Vigneux-sur-Seine',
    'villefranche sur saone': 'Villefranche-sur-Saône',
    'villejuif': 'Villejuif',
    'villemomble': 'Villemomble',
    'villenave d ornon': "Villenave-d'Ornon",
    'villeneuve d ascq': "Villeneuve-d'Ascq",
    'villeneuve la garenne': 'Villeneuve-la-Garenne',
    'villeneuve le roi': 'Villeneuve-le-Roi',
    'villeneuve saint georges': 'Villeneuve-Saint-Georges',
    'villeneuve sur lot': 'Villeneuve-sur-Lot',
    'villeparisis': 'Villeparisis',
    'villepinte': 'Villepinte',
    'villereal': 'Villeréal',
    'villeurbanne': 'Villeurbanne',
    'villiers le bel': 'Villiers-le-Bel',
    'villiers sur marne': 'Villiers-sur-Marne',
    'vincennes': 'Vincennes',
    'viry chatillon': 'Viry-Châtillon',
    'vitrolles': 'Vitrolles',
    'vitry sur seine': 'Vitry-sur-Seine',
    'vogue': 'Vogüé',
    'voiron': 'Voiron',
    'wasquehal': 'Wasquehal',
    'wattrelos': 'Wattrelos',
    'yerres': 'Yerres',
    'yerville': 'Yerville',
    'yevre le chatel': 'Yèvre-le-Châtel',
    'yvoire': 'Yvoire',
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
