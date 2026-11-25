"""Capa de integración con Gemini API para extracción estructurada de experiencia."""

import json
import logging
import time

from google import genai
from google.genai import types

from servir_pipeline.models import ExperienciaBatchResponse, ExperienciaItem

logger = logging.getLogger(__name__)


def _process_batch(
    client: genai.Client, model: str, batch: list[tuple[int, str]]
) -> list[ExperienciaItem]:
    payload_text = json.dumps(
        [{"id": row_id, "experiencia": exp_txt} for row_id, exp_txt in batch],
        ensure_ascii=False,
    )
    prompt = (
        "Extrae de cada texto de 'experiencia' dos valores numéricos enteros (años):\n"
        "1) experiencia_general: años de experiencia general como entero (int).\n"
        "2) experiencia_especifica: años de experiencia específica como entero (int).\n"
        "Si se indica en meses menor a 1 año, usa 0. Si no se menciona, devuelve null. "
        "Mantén exactamente el mismo 'id' de cada elemento.\n\n"
        f"Lote:\n{payload_text}"
    )

    last_err: Exception | None = None
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExperienciaBatchResponse,
                    temperature=0.0,
                ),
            )
            parsed = ExperienciaBatchResponse.model_validate_json(response.text)
            return parsed.resultados
        except Exception as exc:
            last_err = exc
            wait = 2 ** (attempt + 1)
            logger.warning("Error en lote Gemini (intento %d/4): %s", attempt + 1, exc)
            time.sleep(wait)
    raise RuntimeError(f"Falló el lote de Gemini tras 4 intentos: {last_err}")


def enrich_experience(
    pending_rows: list[tuple[int, str]],
    *,
    api_key: str,
    model: str,
    batch_size: int,
) -> dict[int, tuple[int | None, int | None]]:
    """Procesa lotes pendientes con Gemini y devuelve {id: (exp_general, exp_especifica)}."""
    if not pending_rows:
        return {}

    client = genai.Client(api_key=api_key)
    updates: dict[int, tuple[int | None, int | None]] = {}
    total = len(pending_rows)

    for start in range(0, total, batch_size):
        batch = pending_rows[start : start + batch_size]
        items = _process_batch(client, model, batch)
        for it in items:
            updates[it.id] = (it.experiencia_general, it.experiencia_especifica)
        logger.info("Gemini procesados: %d/%d", min(start + len(batch), total), total)

    return updates
