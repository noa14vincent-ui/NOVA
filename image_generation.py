"""NOVA image generation using Cloudflare Workers AI (Recraft V4.1 Flash).

Render environment variables:
  API_KEY_IMAGE          Cloudflare API token
  CLOUDFLARE_ACCOUNT_ID   Cloudflare account ID
"""
import base64
import os
import requests
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

MODEL = "recraft/recraftv4-1-flash"
API_URL = "https://api.cloudflare.com/client/v4/accounts/{}/ai/run"
TIMEOUT_SECONDS = 90
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _error(message: str, status: int = 502):
    return JSONResponse(status_code=status, content={"error": message, "detail": message})


def install_image_routes(app, get_client=None, check_access=None):
    @app.post("/api/images/generate")
    async def generate_image(request: Request):
        if check_access is not None:
            try:
                result = check_access(request)
                if hasattr(result, "__await__"):
                    result = await result
                if result is False:
                    raise HTTPException(status_code=403, detail="Accès refusé.")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=403, detail="Vérification d’accès impossible.")

        token = os.getenv("API_KEY_IMAGE") or os.getenv("CLOUDFLARE_API_TOKEN")
        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        if not token or not account_id:
            return _error("Configuration manquante : ajoute API_KEY_IMAGE et CLOUDFLARE_ACCOUNT_ID dans Render.", 500)

        try:
            body = await request.json()
        except Exception:
            return _error("La requête doit contenir un JSON valide.", 400)
        if not isinstance(body, dict):
            return _error("La requête doit être un objet JSON.", 400)

        prompt = body.get("prompt") or body.get("description") or body.get("text")
        if not isinstance(prompt, str) or not prompt.strip():
            return _error("Ajoute une description de l’image dans le champ 'prompt'.", 400)
        prompt = prompt.strip()
        if len(prompt) > 4000:
            return _error("La description est trop longue (maximum 4000 caractères).", 400)

        # Cloudflare Recraft expects a prompt; keep request payload compatible.
        payload = {"model": MODEL, "prompt": prompt}
        try:
            response = requests.post(
                API_URL.format(account_id),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"prompt": prompt},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            return _error("Impossible de contacter Cloudflare. Réessaie dans un instant.", 502)

        if not response.ok:
            try:
                details = response.json()
                errors = details.get("errors") or []
                message = "; ".join(str(e.get("message", e)) for e in errors) or f"Cloudflare a répondu HTTP {response.status_code}."
            except Exception:
                message = f"Cloudflare a répondu HTTP {response.status_code}."
            status = 429 if response.status_code == 429 else 502
            return _error(message[:700], status)

        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        image_bytes = None
        mime_type = None

        # Some Workers AI models return image bytes directly.
        if content_type.startswith("image/"):
            image_bytes = response.content
            mime_type = content_type
        else:
            try:
                data = response.json()
            except Exception:
                return _error("Cloudflare a renvoyé une réponse illisible.", 502)

            if data.get("success") is False:
                errors = data.get("errors") or []
                msg = "; ".join(str(e.get("message", e)) for e in errors) or "La génération Cloudflare a échoué."
                return _error(msg[:700], 502)
