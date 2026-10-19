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
                json={"input": {"prompt": prompt}},
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

            result = data.get("result") or {}
            # Workers AI image models may return an image URL or a base64/data URI.
            image_value = result.get("image") or result.get("url")
            if isinstance(image_value, str) and image_value.startswith("data:image/") and "," in image_value:
                header, encoded = image_value.split(",", 1)
                mime_type = header[5:].split(";")[0]
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except Exception:
                    return _error("Cloudflare a renvoyé une image encodée invalide.", 502)
            elif isinstance(image_value, str) and image_value.startswith("https://"):
                try:
                    img_response = requests.get(image_value, timeout=TIMEOUT_SECONDS, stream=True)
                    img_response.raise_for_status()
                    mime_type = img_response.headers.get("content-type", "image/png").split(";")[0].lower()
                    if not mime_type.startswith("image/"):
                        return _error("L’URL renvoyée par Cloudflare ne contient pas une image.", 502)
                    chunks = bytearray()
                    for chunk in img_response.iter_content(64 * 1024):
                        if chunk:
                            chunks.extend(chunk)
                            if len(chunks) > MAX_IMAGE_BYTES:
                                return _error("L’image générée est trop volumineuse.", 502)
                    image_bytes = bytes(chunks)
                except requests.RequestException:
                    return _error("Impossible de récupérer l’image générée par Cloudflare.", 502)
                finally:
                    try:
                        img_response.close()
                    except Exception:
                        pass
            elif isinstance(result.get("image"), (bytes, bytearray)):
                image_bytes = bytes(result["image"])
                mime_type = "image/png"

        if not image_bytes:
            # Return a clear JSON error instead of null, so the UI can show a useful message.
            return _error("Cloudflare n’a pas renvoyé d’image exploitable. Vérifie les logs Render et le format de réponse du modèle.", 502)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return _error("L’image générée est trop volumineuse.", 502)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/png"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{encoded}"
        return JSONResponse(content={"image": data_uri, "mime_type": mime_type, "model": MODEL})
