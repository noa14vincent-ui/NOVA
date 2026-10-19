"""
Routes de génération d'images NOVA avec Cloudflare Workers AI / Recraft.
Ce module remplace l'ancien appel Gemini pour les images uniquement.
Le chat Gemini dans server.py n'est pas modifié.
"""

import asyncio
import base64
import logging
import os
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

MODEL = os.getenv("NOVA_IMAGE_MODEL", "recraft/recraftv4-1-flash")
CLOUDFLARE_API_URL = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
)
DEFAULT_TIMEOUT = min(
    max(int(os.getenv("NOVA_IMAGE_TIMEOUT_SECONDS", "120")), 15), 180
)
MAX_PROMPT_LENGTH = 4000
MAX_IMAGE_BYTES = 15 * 1024 * 1024

ASPECT_RATIOS = {
    "1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "4:5", "5:4"
}


def _env_ready():
    return bool(os.getenv("API_KEY_IMAGE") and os.getenv("CLOUDFLARE_ACCOUNT_ID"))


def _error_message(status_code, body):
    if status_code == 429:
        return "Cloudflare a atteint sa limite de requêtes. Réessaie plus tard."
    if status_code == 401:
        return "La clé API Cloudflare est invalide. Vérifie API_KEY_IMAGE dans Render."
    if status_code == 403:
        return "La clé API Cloudflare n'a pas les autorisations nécessaires."
    if status_code in (402,):
        return "Le compte Cloudflare ne dispose pas du quota ou de la facturation nécessaire."
    try:
        detail = body.get("errors") or body.get("error") or body.get("message")
        if detail:
            return f"Erreur Cloudflare ({status_code}) : {str(detail)[:500]}"
    except Exception:
        pass
    return f"Cloudflare a renvoyé une erreur HTTP {status_code}."


def _mime_from_bytes(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


async def _check_access(check_access, request):
    """Appelle le contrôle d'accès existant sans modifier server.py."""
    if not check_access:
        return
    result = check_access(request)
    if hasattr(result, "__await__"):
        result = await result
    # Les contrôles qui lèvent HTTPException fonctionnent aussi.
    if result is False:
        raise HTTPException(status_code=403, detail="Accès refusé.")


async def _download_image(client, image_url):
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=502, detail="URL d'image Cloudflare invalide.")
    # Empêche de télécharger une URL arbitraire retournée par une réponse inattendue.
    host = parsed.hostname.lower()
    allowed = (
        host == "examples.aig.cloudflare.com"
        or host.endswith(".r2.dev")
        or host.endswith(".r2.cloudflarestorage.com")
        or host.endswith(".cloudflare.com")
    )
    if not allowed:
        raise HTTPException(
            status_code=502,
            detail="Cloudflare a retourné une adresse d'image non autorisée."
        )
    async with client.stream("GET", image_url, follow_redirects=False) as response:
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Impossible de récupérer l'image générée."
            )
        chunks = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=502, detail="Image générée trop volumineuse.")
            chunks.append(chunk)
        data = b"".join(chunks)
        mime = response.headers.get("content-type", "").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = _mime_from_bytes(data)
        return data, mime


def install_image_routes(app, get_client=None, check_access=None):
    """
    Enregistre les routes image.
    get_client est conservé pour compatibilité avec server.py, mais les images
    n'utilisent plus le client Gemini.
    """

    @app.get("/api/images/health")
    async def image_health():
        return {
            "ok": True,
            "provider": "cloudflare",
            "model": MODEL,
            "configured": _env_ready(),
            "api_key_configured": bool(os.getenv("API_KEY_IMAGE")),
            "account_id_configured": bool(os.getenv("CLOUDFLARE_ACCOUNT_ID")),
        }

    @app.post("/api/images/generate")
    async def generate_image(request: Request):
        await _check_access(check_access, request)

        if not _env_ready():
            raise HTTPException(
                status_code=503,
                detail=(
                    "La génération d'images n'est pas configurée. "
                    "Ajoute API_KEY_IMAGE et CLOUDFLARE_ACCOUNT_ID dans Render."
                ),
            )

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
            raise HTTPException(
                status_code=400,
                detail=f"La description est trop longue (maximum {MAX_PROMPT_LENGTH} caractères).",
            )

        aspect_ratio = str(
            payload.get("aspect_ratio") or payload.get("aspectRatio") or payload.get("size") or "1:1"
        ).strip()
        if aspect_ratio not in ASPECT_RATIOS:
            aspect_ratio = "1:1"

        style = str(payload.get("style") or "").strip()
        if style:
            style = style[:120]
            full_prompt = f"{prompt}\nStyle visuel souhaité : {style}."
        else:
            full_prompt = prompt

        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = os.getenv("API_KEY_IMAGE", "").strip()
        endpoint = CLOUDFLARE_API_URL.format(account_id=account_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "model": MODEL,
            "input": {
                "prompt": full_prompt,
                "size": aspect_ratio,
            },
        }

        timeout = httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                try:
                    result = response.json()
                except Exception:
                    result = {}

                if response.status_code >= 400:
                    logger.warning(
                        "Cloudflare image API returned HTTP %s", response.status_code
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=_error_message(response.status_code, result),
                    )

                # La réponse Workers AI peut contenir une URL d'image ou des octets base64.
                result_data = result.get("result", result) if isinstance(result, dict) else {}
                image_url = result_data.get("image") if isinstance(result_data, dict) else None

                if isinstance(image_url, str) and image_url.startswith("https://"):
                    image_bytes, mime_type = await _download_image(client, image_url)
                elif isinstance(image_url, str):
                    try:
                        image_bytes = base64.b64decode(image_url, validate=True)
                    except Exception:
                        raise HTTPException(
                            status_code=502,
                            detail="Cloudflare n'a pas renvoyé une image exploitable.",
                        )
                    mime_type = _mime_from_bytes(image_bytes)
                else:
                    raise HTTPException(
                        status_code=502,
                        detail="Cloudflare n'a pas renvoyé d'image. Vérifie le modèle et le quota.",
                    )

        except HTTPException:
            raise
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="La génération a pris trop de temps. Réessaie dans un instant.",
            )
        except httpx.RequestError:
            logger.exception("Connection error while calling Cloudflare image API")
            raise HTTPException(
                status_code=502,
                detail="Impossible de contacter Cloudflare pour générer l'image.",
            )
        except Exception:
            logger.exception("Unexpected image generation error")
            raise HTTPException(
                status_code=500,
                detail="Erreur inattendue pendant la génération de l'image.",
            )

        encoded = base64.b64encode(image_bytes).decode("ascii")
        return JSONResponse({
            "ok": True,
            "image": f"data:{mime_type};base64,{encoded}",
            "image_base64": encoded,
            "mime_type": mime_type,
            "model": MODEL,
            "aspect_ratio": aspect_ratio,
        })

    # Alias conservé au cas où une ancienne version de l'interface l'utiliserait.
    @app.post("/api/image/generate")
    async def generate_image_legacy(request: Request):
        return await generate_image(request)
