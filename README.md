# NOVA — iPhone / PWA

Version mobile 2D futuriste de NOVA.

## Contenu

- `server.py` : backend FastAPI
- `index.html` : interface mobile NOVA
- `manifest.webmanifest` : installation sur l'écran d'accueil de l'iPhone
- `sw.js` : cache PWA
- `requirements.txt` : dépendances Python
- `render.yaml` : configuration Render
- `static/icon-192.png` et `static/icon-512.png` : icônes
- `.gitignore` : fichiers locaux à ignorer

## Fonctions

- Parler à NOVA avec le micro du navigateur quand la reconnaissance vocale est disponible
- Écrire et envoyer un message
- Mode rapide ON/OFF
- Voix NOVA ON/OFF
- Ouverture de sites sur l'iPhone depuis une commande comme « ouvre Netflix »
- Météo avec Open-Meteo
- Marchés avec Yahoo Finance
- Interface mobile volontairement minimaliste : uniquement les commandes utiles

## Lancer en local

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn server:app --reload
```

Puis ouvrir `http://127.0.0.1:8000`.

## Déployer sur Render

Le service doit utiliser :

- Build : `pip install -r requirements.txt`
- Start : `uvicorn server:app --host 0.0.0.0 --port $PORT`

Après déploiement, Render fournit une URL publique `onrender.com`. Render documente ce mode de déploiement FastAPI et le redéploiement automatique depuis GitHub. Les services gratuits peuvent s'arrêter après une période d'inactivité puis redémarrer à la prochaine requête. 

## Important

Ne place jamais une clé d'API privée directement dans `index.html` ou dans GitHub.

La reconnaissance vocale dépend du navigateur et du système de l'iPhone. Si le bouton micro n'est pas supporté dans le navigateur utilisé, le champ texte reste disponible.
