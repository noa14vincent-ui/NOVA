"""NOVA image generation via Cloudflare Workers AI / Recraft V4.1 Flash.

Render environment variables:
  API_KEY_IMAGE (or CLOUDFLARE_API_TOKEN): Cloudflare API token
  CLOUDFLARE_ACCOUNT_ID: Cloudflare account ID
"""
import base64
import os
from urllib.parse import urlparse

import requests
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

MODEL = "recraft/recraftv4-1-flash"
API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
TIMEOUT_SECONDS = 90
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _error(message: str, status: int = 502):
    return JSONResponse(
        status_code=status,
        content={"error": message, "detail": message, "image": None, "mime_type": None},
    )


def _download_image(url: str):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    # Recraft responses may use Cloudflare's examples host or public R2 image host.
    if parsed.scheme != "https" or not (
        host == "cloudflare.com"
        or host.endswith(".cloudflare.com")
        or host == "r2.dev"
        or host.endswith(".r2.dev")
    ):
        raise HTTPException(status_code=502, detail="Cloudflare a renvoyé une URL d’image inattendue.")

    with requests.get(url, timeout=TIMEOUT_SECONDS, stream=True) as response:
        response.raise_for_status()
        mime = response.headers.get("content-type", "image/png").split(";", 1)[0].strip().lower()
        if not mime.startswith("image/"):
            raise HTTPException(status_code=502, detail="La réponse du fournisseur n’est pas une image.")
        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                chunks.extend(chunk)
                if len(chunks) > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=502, detail="L’image générée est trop volumineuse.")
    if not chunks:
        raise HTTPException(status_code=502, detail="Le fournisseur a renvoyé une image vide.")
    return bytes(chunks), mime


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
        account_id = (os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()
        if not token or not account_id:
            return _error(
                "Configuration manquante : ajoute API_KEY_IMAGE et CLOUDFLARE_ACCOUNT_ID dans Render.",
                500,
            )

        try:
            body = await request.json()
        except Exception:
            return _error("La requête doit contenir un JSON valide.", 400)
        if not isinstance(body, dict):
            return _error("La requête doit être un objet JSON.", 400)

        prompt = body.get("prompt") or body.get("description") or body.get("text")
        if not isinstance(prompt, str) or not prompt.strip():
            return _error("Ajoute une description dans le champ 'prompt'.", 400)
        prompt = prompt.strip()
        if len(prompt) > 4000:
            return _error("La description est trop longue (maximum 4000 caractères).", 400)

        size = body.get("size", "1024x1024")
        allowed_sizes = {
            "1:1", "4:5", "5:4", "3:2", "2:3", "16:9", "9:16",
            "1024x1024", "1344x768", "768x1344",
