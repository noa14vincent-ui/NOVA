"""NOVA image generation via Cloudflare Workers AI FLUX.1-schnell."""
import base64
import logging
import os

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
MODEL = os.getenv("NOVA_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell").strip()
API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
TIMEOUT_SECONDS = min(max(int(os.getenv("NOVA_IMAGE_TIMEOUT_SECONDS", "120")), 15), 180)
MAX_PROMPT_LENGTH = 2048


def _configured():
    return bool(os.getenv("API_KEY_IMAGE", "").strip() and
                os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip())


async def _check_access(check_access, request):
    if not check_access:
        return
    result = check_access(request)
    if hasattr(result, "__await__"):
        result = await result
    if result is False:
        raise HTTPException(status_code=403, detail="Accès refusé.")


def _error_message(status_code, data):
    detail = ""
    if isinstance(data, dict):
        detail = str(data.get("errors") or data.get("error") or data.get("message") or "")
    if status_code == 400 and "size" in detail.lower():
        return "Le modèle FLUX ne prend pas en charge le paramètre size. Vérifie que le dernier fichier est déployé."
    if status_code == 400:
        return f"Requête refusée par Cloudflare (400) : {detail[:450] or 'paramètres non acceptés'}"
    if status_code == 401:
        return "Clé API Cloudflare invalide. Vérifie API_KEY_IMAGE dans Render."
    if status_code == 403:
        return "Cloudflare refuse l'accès. Vérifie les autorisations du jeton Workers AI."
    if status_code == 429:
        return "Limite de requêtes ou quota gratuit Cloudflare atteint. Réessaie plus tard."
    if status_code == 402:
        return "Cloudflare indique un problème de quota ou de facturation."
    return f"Erreur Cloudflare ({status_code}) : {detail[:450]}" if detail else f"Cloudflare a renvoyé une erreur HTTP {status_code}."


def install_image_routes(app, get_client=None, check_access=None):
    # get_client reste dans la signature pour compatibilité avec server.py.
    @app.get("/api/images/health")
    async def image_health():
        return {
            "ok": True,
            "provider": "cloudflare",
            "model": MODEL,
            "configured": _configured(),
            "api_key_configured": bool(os.getenv("API_KEY_IMAGE", "").strip()),
            "account_id_configured": bool(os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()),
        }

    async def _generate(request: Request):
        await _check_access(check_access, request)
        if not _configured():
            raise HTTPException(status_code=503, detail="Ajoute API_KEY_IMAGE et CLOUDFLARE_ACCOUNT_ID dans Render.")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Corps JSON invalide.")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Requête invalide.")

        prompt = str(payload.get("prompt") or payload.get("description") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Écris une description pour l'image.")
        if len(prompt) > MAX_PROMPT_LENGTH:
            raise HTTPException(status_code=400, detail=f"Description limitée à {MAX_PROMPT_LENGTH} caractères.")

        style = str(payload.get("style") or "").strip()
        full_prompt = f"{prompt}\nStyle visuel souhaité : {style[:100]}." if style else prompt

        # FLUX.1-schnell n'accepte que prompt et steps ; surtout pas "size".
        body = {"prompt": full_prompt, "steps": 4}
        endpoint = API_URL.format(
            account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip(),
            model=MODEL,
        )
        headers = {
            "Authorization": f"Bearer {os.getenv('API_KEY_IMAGE', '').strip()}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=10.0)) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                 try:
                data = response.json()
            except Exception:
                data = {}
            if response.status_code >= 400:
                logger.warning("Cloudflare image API returned HTTP %s", response.status_code)
                raise HTTPException(status_code=502, detail=_error_message(response.status_code, data))

            result = data.get("result", data) if isinstance(data, dict) else {}
            encoded = result.get("image") if isinstance(result, dict) else None
            if not isinstance(encoded, str) or not encoded:
                raise HTTPException(status_code=502, detail="Cloudflare n'a pas renvoyé d'image. Vérifie le modèle et le quota.")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except Exception:
                raise HTTPException(status_code=502, detail="La réponse Cloudflare ne contient pas une image Base64 valide.")
            if not image_bytes:
                raise HTTPException(status_code=502, detail="L'image reçue est vide.")

            mime = "image/jpeg"
            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
                mime = "image/webp"
            return JSONResponse({
                "ok": True,
                "image": f"data:{mime};base64,{encoded}",
                "image_base64": encoded,
                "mime_type": mime,
                "model": MODEL,
                "aspect_ratio": payload.get("aspect_ratio") or payload.get("aspectRatio") or "1:1",
            })
        except HTTPException:
            raise
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="La génération a pris trop de temps. Réessaie.")
        except httpx.RequestError:
            logger.exception("Connection error while calling Cloudflare Workers AI")
            raise HTTPException(status_code=502, detail="Impossible de contacter Cloudflare.")
        except Exception:
            logger.exception("Unexpected image generation error")
            raise HTTPException(status_code=500, detail="Erreur inattendue pendant la génération d'image.")

    @app.post("/api/images/generate")
    async def generate_image(request: Request):
        return await _generate(request)

    @app.post("/api/image/generate")
    async def generate_image_legacy(request: Request):
        return await _generate(request)
