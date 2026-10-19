"""NOVA image generation using Cloudflare Workers AI (Recraft V4.1 Flash).

Required Render environment variables:
  API_KEY_IMAGE          Cloudflare API token (or use CLOUDFLARE_API_TOKEN)
  CLOUDFLARE_ACCOUNT_ID   Cloudflare account ID

Keeps the POST /api/images/generate endpoint and returns a data URI for the UI.
"""

import base64
import os
from urllib.parse import urlparse

import requests
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

MODEL = "recraft/recraftv4-1-flash"
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
TIMEOUT_SECONDS = 90


def _json_error(message: str, status_code: int = 502):
    return JSONResponse(status_code=status_code, content={"error": message})


def _download_image(image_url: str):
    parsed = urlparse(image_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host.endswith(".cloudflare.com") or host.endswith(".r2.dev")
    ):
        raise HTTPException(status_code=502, detail="Le fournisseur a renvoyé une URL d’image inattendue.")

    with requests.get(image_url, timeout=TIMEOUT_SECONDS, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "image/png").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=502, detail="La réponse reçue n’est pas une image.")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if chunk:
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=502, detail="L’image générée est trop volumineuse.")
    if not data:
        raise HTTPException(status_code=502, detail="Le fournisseur a renvoyé une image vide.")
    return bytes(data), content_type


def install_image_routes(app, get_client=None, check_access=None):
    """Register the Cloudflare-backed image endpoint on the existing FastAPI app."""

    @app.post("/api/images/generate")
    async def generate_image(request: Request):
        if check_access is not None:
            try:
                access_result = check_access(request)
                if hasattr(access_result, "__await__"):
                    access_result = await access_result
                # Some projects use check_access to raise on failure; others return False.
                if access_result is False:
                    raise HTTPException(status_code=403, detail="Accès refusé.")
            except HTTPException:
                raise
            except Exception:
                # If the project's access checker signals denial by exception, preserve it;
                # otherwise don't expose internal details to the client.
                raise HTTPException(status_code=403, detail="Vérification d’accès impossible.")

        token = os.getenv("API_KEY_IMAGE") or os.getenv("CLOUDFLARE_API_TOKEN")
        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        if not token or not account_id:
            return _json_error(
                "Configuration manquante : ajoute API_KEY_IMAGE (jeton Cloudflare) et CLOUDFLARE_ACCOUNT_ID dans Render.",
                500,
            )

        try:
            body = await request.json()
        except Exception:
            return _json_error("La requête doit contenir un JSON valide.", 400)

        prompt = body.get("prompt") or body.get("description") or body.get("text")
        if not isinstance(prompt, str) or not prompt.strip():
            return _json_error("Ajoute une description de l’image dans le champ 'prompt'.", 400)
        prompt = prompt.strip()
        if len(prompt) > 4000:
            return _json_error("La description est trop longue (maximum 4000 caractères).", 400)

        size = body.get("size", "1024x1024")
        # Keep accepted values simple and avoid passing arbitrary values upstream.
        allowed_sizes = {"1:1", "4:5", "5:4", "3:2", "2:3", "16:9", "9:16", "1024x1024", "1344x768", "768x1344"}
        if not isinstance(size, str) or size not in allowed_sizes:
            size = "1024x1024"
