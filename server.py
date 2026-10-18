        "version": APP_VERSION,
        "gemini_configured": bool(env("GEMINI_API_KEY")),
        "model": model_list()[0] if model_list() else None,
    }


@app.get("/api/status")
def status():
    models = model_list()
    active = get_client()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "api_key_present": bool(env("GEMINI_API_KEY")),
        "client_initialized": active is not None,
        "models_configured": len(models),
        "primary_model": models[0] if models else None,
        "fallback_models": models[1:],
    }


@app.get("/api/test-gemini")
def test_gemini():
    try:
        raw = generate(json.dumps({
            "instructions": "Réponds exclusivement avec un objet JSON valide.",
            "message": 'Réponds avec ce JSON : {"reply":"NOVA fonctionne","memory_updates":[]}',
        }, ensure_ascii=False), fast=True)
        result = parse_response(raw)
        return {
            "status": "ok",
            "gemini": "connected",
            "version": APP_VERSION,
            "model_configured": model_list()[0] if model_list() else None,
            "reply": result.reply,
        }
    except Exception as exc:
        logger.exception("Échec du test Gemini.")
        msg = str(exc).lower()
        if "429" in msg or "resource_exhausted" in msg:
            detail = "Quota Gemini atteint. Réessaie plus tard ou vérifie les quotas."
        elif "api key" in msg or "unauthenticated" in msg:
            detail = "Gemini a refusé la clé API. Vérifie GEMINI_API_KEY dans Render."
        elif "not found" in msg or "does not exist" in msg:
            detail = "Modèle introuvable ou inaccessible. Vérifie les modèles configurés."
        else:
            detail = "Gemini ne répond pas actuellement. Consulte les logs Render."
        raise HTTPException(status_code=503, detail=detail) from exc


@app.post("/api/chat", response_model=ChatResponse)
def chat(data: ChatRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Le message est vide.")
    if get_client() is None:
        raise HTTPException(status_code=503, detail="Clé Gemini absente ou client indisponible. Vérifie Render.")
    try:
        return parse_response(generate(make_prompt(data), fast=data.fast))
    except Exception as exc:
        logger.exception("Erreur pendant le traitement du chat.")
        msg = str(exc).lower()
        if "429" in msg or "resource_exhausted" in msg:
            detail = "La limite Gemini est atteinte. Réessaie dans un instant."
        elif "api key" in msg or "unauthenticated" in msg:
            detail = "Gemini a refusé la clé API. Vérifie la configuration Render."
        elif "not found" in msg or "does not exist" in msg:
            detail = "Le modèle Gemini est introuvable ou inaccessible. Vérifie les modèles de secours."
        else:
            detail = "NOVA ne peut pas répondre pour le moment. Réessaie dans un instant."
        raise HTTPException(status_code=503, detail=detail) from exc


@app.get("/api/weather")
def weather(city: str = "Grenoble"):
    city = city.strip()[:100]
    if not city:
        raise HTTPException(status_code=400, detail="Ville manquante.")
    try:
        response = requests.get(
            "https://wttr.in/" + requests.utils.quote(city, safe=""),
            params={"format": "j1"},
            timeout=8,
            headers={"User-Agent": "NOVA-Weather/1.0"},
        )
        response.raise_for_status()
        result = response.json()
        conditions = result.get("current_condition", [])
        if not conditions:
            raise ValueError("Réponse météo incomplète.")
        current = conditions[0]
        descriptions = current.get("weatherDesc", [])
        description = descriptions[0].get("value", "Inconnue") if descriptions else "Inconnue"
        return {
            "city": city,
            "temperature": current.get("temp_C"),
            "feels_like": current.get("FeelsLikeC"),
            "description": description,
            "humidity": current.get("humidity"),
            "wind_kph": current.get("windspeedKmph"),
            "source": "wttr.in",
        }
    except Exception as exc:
        logger.warning("Erreur météo pour %s : %s", city, str(exc)[:200])
        raise HTTPException(status_code=502, detail="La météo est momentanément indisponible.") from exc

@app.get("/api/calendar/capabilities")
def calendar_capabilities():
    """Décrit honnêtement les capacités d'intégration calendrier du client web."""
    return {
        "status": "ok",
        "integration": "ics-import-export",
        "can_export_events": True,
        "can_import_ics": True,
        "direct_ios_calendar_access": False,
        "message": (
            "Depuis Safari, NOVA peut importer/exporter des fichiers .ics. "
            "La lecture et l'écriture directes du calendrier iOS nécessitent "
            "une application iOS native ou une intégration autorisée séparée."
        ),
    }
