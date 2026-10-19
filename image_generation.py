"""Endpoint de génération d'images pour NOVA v7.

Intégration dans server.py :
    from image_generation import install_image_routes
    install_image_routes(app, get_client, check_access)

Le fournisseur d'IA et sa clé restent exclusivement côté serveur.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("nova.images")

ALLOWED_ASPECT_RATIOS = {"1:1", "4:5", "3:4", "9:16", "16:9", "3:2", "2:3"}
ALLOWED_STYLES = {
    "cinematic",
    "photorealistic",
    "3d render",
    "illustration",
    "editorial design",
    "watercolor",
    "minimalist vector art",
}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Génération coûteuse : limites en mémoire, en plus de celles déjà appliquées au chat.
IMAGE_LIMIT_PER_MIN = max(1, min(int(os.getenv("NOVA_IMAGE_LIMIT_PER_MIN", "2")), 10))
IMAGE_LIMIT_PER_HOUR = max(1, min(int(os.getenv("NOVA_IMAGE_LIMIT_PER_HOUR", "8")), 40))
IMAGE_GLOBAL_LIMIT_PER_HOUR = max(1, min(int(os.getenv("NOVA_IMAGE_GLOBAL_LIMIT_PER_HOUR", "60")), 500))
_image_hits: dict[str, deque[float]] = defaultdict(deque)
_global_hits: deque[float] = deque()
_image_lock = threading.Lock()


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=1400)
    style: str = Field(default="cinematic", max_length=40)
    aspect_ratio: Literal["1:1", "4:5", "3:4", "9:16", "16:9", "3:2", "2:3"] = "1:1"


def _normalise_prompt(prompt: str) -> str:
    # Supprime les caractères de contrôle inutiles sans altérer les retours à la ligne utiles.
    cleaned = "".join(ch for ch in prompt.strip() if ch in "\n\t" or ord(ch) >= 32)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    if len(cleaned) < 4:
        raise HTTPException(status_code=422, detail="Décris un peu plus l’image à créer.")
    return cleaned[:1400]


def _client_key(request: Request) -> str:
    # Ne fait pas confiance à X-Forwarded-For, qui peut être falsifié si le proxy
    # n'est pas explicitement configuré comme proxy de confiance.
    return request.client.host if request.client else "unknown"


def _consume_rate_limit(ip_key: str) -> None:
    now = time.monotonic()
    with _image_lock:
        while _global_hits and now - _global_hits[0] >= 3600:
            _global_hits.popleft()
        bucket = _image_hits[ip_key]
        while bucket and now - bucket[0] >= 3600:
            bucket.popleft()
        minute_count = sum(1 for ts in bucket if now - ts < 60)
        if minute_count >= IMAGE_LIMIT_PER_MIN:
            raise HTTPException(
                status_code=429,
                detail="Trop de générations rapprochées. Attends une minute avant de réessayer.",
                headers={"Retry-After": "60"},
            )
        if len(bucket) >= IMAGE_LIMIT_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="La limite horaire de génération d’images est atteinte.",
                headers={"Retry-After": "3600"},
            )
        if len(_global_hits) >= IMAGE_GLOBAL_LIMIT_PER_HOUR:
            raise HTTPException(
                status_code=503,
                detail="Le quota temporaire de génération d’images du service est atteint.",
                headers={"Retry-After": "3600"},
            )
        bucket.append(now)
