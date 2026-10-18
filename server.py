import json
import logging
import os
import random
import time
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

APP_VERSION = "6.1.0"
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nova")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def model_list() -> list[str]:
    candidates = [
        env("GEMINI_MODEL", "gemini-2.5-flash"),
        env("GEMINI_FALLBACK_MODEL"),
        *env("GEMINI_EXTRA_MODELS").split(","),
    ]
    result = []
    for item in candidates:
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return result


client = None
client_key = ""


def get_client():
    global client, client_key
    key = env("GEMINI_API_KEY")
    if not key:
        client, client_key = None, ""
        return None
    if client is None or key != client_key:
        try:
            client = genai.Client(api_key=key)
            client_key = key
        except Exception:
            client = None
            logger.exception("Initialisation du client Gemini impossible.")
    return client


app = FastAPI(title="NOVA API", version=APP_VERSION, description="API de NOVA")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    fast: bool = False
    device: str = "unknown"
    memory_context: Any = ""
    history: list[Any] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    memory_updates: list[Any] = Field(default_factory=list)


def retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(x in msg for x in (
        "429", "500", "502", "503", "504", "unavailable",
        "resource_exhausted", "deadline_exceeded", "temporarily",
        "timeout", "timed out", "connection reset", "overloaded",
        "service unavailable", "rate limit", "internal server error",
    ))


def permanent(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(x in msg for x in (
        "api key not valid", "invalid api key", "permission_denied",
        "unauthenticated", "api_key_invalid", "model not found",
