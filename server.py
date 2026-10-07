    }


@app.get("/api/weather")
def weather_endpoint(city: str = "Grenoble"):
    data = get_weather(city)
    if not data:
        return JSONResponse({"error": "Météo indisponible"}, status_code=503)
    return data


@app.get("/api/market")
def market_endpoint(symbol: str = "AAPL"):
    data = get_market(symbol.upper())
    if not data:
        return JSONResponse({"error": "Marché indisponible"}, status_code=503)
    return data


@app.post("/api/chat")
def chat(payload: ChatRequest):
    text = payload.text.strip()

    # 1. Ouverture d'applis (téléphone ou PC) : zéro appel Gemini.
    opened = try_open(text, payload.device)
    if opened:
        remember("user", text)
        remember("assistant", opened["reply"])
        return opened

    # 2. Météo : API directe.
    if wants_weather(text):
        city = extract_city(text)
        data = get_weather(city)
        if data:
            reply = (
                f"À {data['city']}, il fait {data['temperature']} °C. "
                f"{data['description']}, humidité {data['humidity']} %."
            )
            remember("user", text)
            remember("assistant", reply)
            return {"reply": reply, "action": {"type": "weather", "data": data}}

    # 3. Marchés : API directe.
    if wants_market(text):
        symbol = None
        lowered = text.lower()
        for name, ticker in MARKETS.items():
            if name in lowered or ticker.lower() in lowered:
                symbol = ticker
                break
        symbol = symbol or "AAPL"

        data = get_market(symbol)
        if data and data.get("price") is not None:
            pct = data.get("change_percent")
            change_text = f"{pct:+.2f} %" if pct is not None else "variation indisponible"
            reply = f"{symbol} est à {data['price']} {data['currency']} ({change_text})."
            remember("user", text)
            remember("assistant", reply)
            return {"reply": reply, "action": {"type": "market", "data": data}}

    # 4. Heure (heure de Paris) : zéro appel Gemini.
    if wants_time(text):
        now = datetime.now(PARIS) if PARIS else datetime.now()
        reply = f"Il est {now.strftime('%H:%M')}."
        remember("user", text)
        remember("assistant", reply)
        return {"reply": reply}

    # 5. Conversation locale ultra-rapide pour les phrases simples.
    simple_patterns = [
        r"^(salut|bonjour|bonsoir|hello|hey|coucou)[!. ]*$",
        r"^(merci|merci beaucoup)[!. ]*$",
        r"^(ça va|ca va|comment ça va|comment ca va)[?! .]*$",
        r"^(ok|okay|d'accord|daccord)[!. ]*$",
    ]
    if any(re.match(pattern, text.lower()) for pattern in simple_patterns):
        reply = local_conversation(text)
        remember("user", text)
        remember("assistant", reply)
        return {"reply": reply}

    # 6. Mode rapide : moteur local en priorité pour éviter d'attendre Gemini.
    if payload.fast:
        reply = local_conversation(text)
        remember("user", text)
        remember("assistant", reply)
        return {
            "reply": reply,
            "fast_local": True,
            "note": "Mode rapide : aucune requête Gemini pour cette réponse.",
        }

    # 7. Gemini.
    remember("user", text)
    reply = ask_gemini(text, payload.fast)

    # 8. Si Gemini échoue, on le dit clairement.
    if not reply:
        remember("assistant", "Gemini indisponible")
        return {
            "reply": "Mon cerveau Gemini est indisponible pour le moment (quota atteint ou erreur). Réessaie plus tard.",
            "fallback": True,
            "gemini_error": True,
        }

    remember("assistant", reply)
    return {"reply": reply, "gemini": True}


# ============================================================
# LANCEMENT LOCAL
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
