"""Routes de génération d’images NOVA avec Gemini."""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("nova.images")

IMAGE_MODEL = os.getenv("NOVA_IMAGE_MODEL", "gemini-2.5-flash-image").strip()
ALLOWED_STYLES = {
    "cinematic", "photorealistic", "3d render", "illustration",
    "editorial design", "watercolor", "minimalist vector art",
}
STYLE_ASPECT = {
    "1:1": "square 1:1",
    "4:5": "portrait 4:5",
    "3:4": "portrait 3:4",
    "9:16": "vertical 9:16",
    "16:9": "landscape 16:9",
    "3:2": "landscape 3:2",
    "2:3": "portrait 2:3",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_LIMIT_PER_MIN = max(1, min(int(os.getenv("NOVA_IMAGE_LIMIT_PER_MIN", "2")), 10))
IMAGE_LIMIT_PER_HOUR = max(1, min(int(os.getenv("NOVA_IMAGE_LIMIT_PER_HOUR", "8")), 40))
IMAGE_GLOBAL_LIMIT_PER_HOUR = max(1, min(int(os.getenv("NOVA_IMAGE_GLOBAL_LIMIT_PER_HOUR", "60")), 500))

_hits: dict[str, deque[float]] = defaultdict(deque)
_global_hits: deque[float] = deque()
_lock = threading.Lock()


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=1400)
    style: str = Field(default="cinematic", max_length=40)
    aspect_ratio: Literal["1:1", "4:5", "3:4", "9:16", "16:9", "3:2", "2:3"] = "1:1"


def _consume_rate_limit(ip: str) -> None:
    now = time.monotonic()
    with _lock:
        while _global_hits and now - _global_hits[0] >= 3600:
            _global_hits.popleft()
        bucket = _hits[ip]
        while bucket and now - bucket[0] >= 3600:
            bucket.popleft()
        if sum(1 for stamp in bucket if now - stamp < 60) >= IMAGE_LIMIT_PER_MIN:
            raise HTTPException(429, "Trop de générations rapprochées. Attends une minute.", headers={"Retry-After": "60"})
        if len(bucket) >= IMAGE_LIMIT_PER_HOUR:
            raise HTTPException(429, "La limite horaire de génération d’images est atteinte.", headers={"Retry-After": "3600"})
        if len(_global_hits) >= IMAGE_GLOBAL_LIMIT_PER_HOUR:
            raise HTTPException(503, "Le quota temporaire d’images est atteint.", headers={"Retry-After": "3600"})
        bucket.append(now)
        _global_hits.append(now)


def install_image_routes(
    app: FastAPI,
    get_client: Callable[[], object | None],
    check_access: Callable[[Request], None],
) -> None:
    @app.get("/api/images/health")
    def image_health():
        return {"status": "ok", "model": IMAGE_MODEL}

    @app.post("/api/images/generate")
    def generate_image(payload: ImageGenerateRequest, request: Request):
        check_access(request)
        client = get_client()
        if client is None:
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY manquante ou invalide.")

        prompt = "".join(ch for ch in payload.prompt.strip() if ch in "\n\t" or ord(ch) >= 32)
        if len(prompt) < 4:
            raise HTTPException(status_code=422, detail="Décris un peu plus l’image à créer.")
        prompt = prompt[:1400]
        style = payload.style.strip().lower()
        if style not in ALLOWED_STYLES:
            style = "cinematic"
        image_prompt = (
            f"Create one image. Visual style: {style}. Composition and aspect ratio: "
            f"{STYLE_ASPECT[payload.aspect_ratio]}. User description: {prompt}"
        )

        ip = request.client.host if request.client else "unknown"
        _consume_rate_limit(ip)

        try:
            # Gemini image models return image data in a response part's inline_data.
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=image_prompt,
                config={"response_modalities": ["IMAGE", "TEXT"]},
            )
        except Exception as exc:
            logger.exception("Échec de génération d’image avec %s", IMAGE_MODEL)
            message = str(exc).lower()
            if "api key" in message or "unauthorized" in message or "permission" in message:
                raise HTTPException(status_code=502, detail="Le fournisseur d’images a refusé l’accès. Vérifie la clé Gemini côté serveur.")
            if "not found" in message or "not supported" in message:
                raise HTTPException(status_code=502, detail="Le modèle d’images configuré n’est pas disponible pour cette clé.")
            raise HTTPException(status_code=502, detail="La génération d’image a échoué. Réessaie plus tard.")

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in (getattr(content, "parts", None) or []):
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                mime_type = getattr(inline, "mime_type", None) if inline else None
                if not data:
                    continue
                if isinstance(data, str):
                    try:
                        raw = base64.b64decode(data, validate=True)
                    except Exception:
                        continue
                else:
                    raw = bytes(data)
                if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                    mime_type = "image/png"
                if len(raw) > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=502, detail="L’image générée dépasse la taille autorisée.")
                return {
                    "image": "data:" + mime_type + ";base64," + base64.b64encode(raw).decode("ascii"),
                    "mime_type": mime_type,
                    "model": IMAGE_MODEL,
                }

        logger.warning("Le modèle %s n’a retourné aucune image.", IMAGE_MODEL)
        raise HTTPException(status_code=502, detail="Le modèle n’a retourné aucune image. Essaie une autre description.")
