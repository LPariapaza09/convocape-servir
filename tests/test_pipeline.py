"""Pruebas unitarias aisladas para los módulos puros y de persistencia."""

from pathlib import Path
import threading

import pandas as pd

from servir_pipeline.models import JobCard, JobDetail
from servir_pipeline.parsers import (
    filter_by_career,
    filter_by_max_experience,
    normalize_text,
    parse_remuneracion,
)
from servir_pipeline.storage import (
    deduplicate_postings,
    init_db,
    insert_postings,
    load_existing_keys,
)


def test_parse_remuneracion_and_normalize():
    assert parse_remuneracion("S/. 7,664.19") == 7664.19
    assert parse_remuneracion("Sin especificar") is None
    assert normalize_text("Economía Pública") == "economia publica"


def test_filter_by_career_and_max_experience():
    df = pd.DataFrame(
        [
            {"id": 1, "formacion_academica": "Bachiller en Economía", "experiencia_general": 1},
            {"id": 2, "formacion_academica": "Título en Derecho", "experiencia_general": 1},
            {"id": 3, "formacion_academica": "Economista", "experiencia_general": 3},
            {"id": 4, "formacion_academica": "Bachiller en Economía", "experiencia_general": None},
        ]
    )
    res_econ = filter_by_career(df, "econom", solo_bachiller=False)
    assert set(res_econ["id"]) == {1, 3, 4}

    res_exp = filter_by_max_experience(res_econ, max_exp_general=1)
    assert set(res_exp["id"]) == {1, 4}


def test_storage_idempotency_and_dedup(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = init_db(db_file, reset=True)
    lock = threading.Lock()
    card = JobCard(
        card_idx=0, puesto="ANALISTA", entidad="MEF", ubicacion="LIMA - LIMA",
        departamento="LIMA", provincia_distrito="LIMA", nro_convocatoria="CAS-001",
        cantidad_vacantes=1, remuneracion_texto="S/. 5,000.00", remuneracion_num=5000.0,
        fecha_inicio_publicacion="25/11/2026", fecha_fin_publicacion="30/11/2026",
        departamento_filtro="LIMA", pagina=1, posicion_en_pagina=1,
        fecha_extraccion="2026-11-25T00:00:00Z",
    )
    detail = JobDetail(nro_aviso="999001", experiencia="1 año", formacion_academica="Bachiller en Economía")
    insert_postings(conn, lock, [(card, detail), (card, detail)])
    assert deduplicate_postings(conn) == 1
    assert card.composite_key() in load_existing_keys(conn)
    conn.close()
