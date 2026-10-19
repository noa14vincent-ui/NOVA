                    "mime_type": "image/jpeg",
                    "aspect_ratio": req.aspect_ratio,
                    "image_size": "1K",
                },
            )
            # Les versions du SDK peuvent exposer l'image directement dans
            # output_image ou dans un bloc image de steps/model_output.
            def field(obj: object, name: str, default=None):
                if isinstance(obj, dict):
                    return obj.get(name, default)
                return getattr(obj, name, default)

            candidates = []
            output_image = field(interaction, "output_image")
            if output_image is not None:
                candidates.append(output_image)

            steps = field(interaction, "steps", []) or []
            if not isinstance(steps, (list, tuple)):
                steps = [steps]
            for step in steps:
                step_type = str(field(step, "type", "") or "").lower()
                contents = field(step, "content", None)
                if contents is None:
                    contents = field(step, "output", None)
                if contents is None:
                    contents = field(step, "parts", None)
                if contents is None and step_type in {"image", "output_image"}:
                    candidates.append(step)
                    continue
                if contents is None:
                    continue
                if not isinstance(contents, (list, tuple)):
                    contents = [contents]
                for block in contents:
                    block_type = str(field(block, "type", "") or "").lower()
                    if block_type in {"image", "output_image"} or field(block, "data") is not None:
                        candidates.append(block)

            raw_bytes = None
            mime = "image/jpeg"
            for candidate in candidates:
                data = field(candidate, "data")
                candidate_mime = str(
                    field(candidate, "mime_type")
                    or field(candidate, "mimeType")
                    or field(candidate, "media_type")
                    or "image/jpeg"
                ).lower()
                if not data:
                    continue
                try:
                    if isinstance(data, bytes):
                        candidate_bytes = data
                    elif isinstance(data, str):
                        value = data.strip()
                        if value.startswith("data:") and "," in value:
                            header, value = value.split(",", 1)
                            if ";" in header:
                                candidate_mime = header[5:].split(";", 1)[0].lower() or candidate_mime
                        candidate_bytes = base64.b64decode(value, validate=True)
                    else:
                        continue
                except (binascii.Error, ValueError):
                    continue
                if candidate_bytes:
                    raw_bytes = candidate_bytes
                    mime = candidate_mime
                    break

            if raw_bytes is None:
                raise RuntimeError("Aucune donnée image exploitable dans la réponse du modèle.")

            # Détecte le format réel à partir de la signature du fichier.
            if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif raw_bytes.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
                mime = "image/webp"
            elif mime not in ALLOWED_MIME_TYPES:
                raise RuntimeError("Le format de l’image reçue n’est pas pris en charge.")
            if not raw_bytes or len(raw_bytes) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=502, detail="L’image générée dépasse la taille autorisée ou est vide.")
            # Ré-encode pour garantir une base64 sans caractères parasites.
            encoded = base64.b64encode(raw_bytes).decode("ascii")
            logger.info("image generated model=%s bytes=%d ratio=%s", model, len(raw_bytes), req.aspect_ratio)
            return {
                "image_base64": encoded,
                "mime_type": mime,
                "model": model,
                "aspect_ratio": req.aspect_ratio,
            }
        except HTTPException:
            raise
        except (binascii.Error, ValueError) as exc:
            logger.warning("Image generation returned invalid image data (%s).", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Le service d’images a renvoyé un résultat illisible.")
        except Exception as exc:
            # Ne renvoie pas les détails fournisseur, ni les prompts, au navigateur.
            logger.warning("Image generation failed model=%s error_type=%s", model, type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail="La génération d’images a échoué. Vérifie le modèle configuré et sa disponibilité pour ta clé API.",
            )

    @app.get("/api/images/health")
    def image_health():
        return {
            "status": "ok",
            "feature": "image-generation",
            "model_configured": os.getenv("NOVA_IMAGE_MODEL", "gemini-nano-banana-2.1").strip() or "gemini-nano-banana-2.1",
            "key_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "limits": {
                "per_minute": IMAGE_LIMIT_PER_MIN,
                "per_hour": IMAGE_LIMIT_PER_HOUR,
                "service_per_hour": IMAGE_GLOBAL_LIMIT_PER_HOUR,
            },
        }
