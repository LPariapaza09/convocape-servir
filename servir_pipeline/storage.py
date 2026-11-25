"""Capa de persistencia SQLite y exportación a Excel."""

from pathlib import Path
import sqlite3
import threading

import pandas as pd

from servir_pipeline.models import JobCard, JobDetail

TABLE_NAME = "ofertas_laborales"

INSERT_COLS = [
    "nro_aviso", "puesto", "entidad", "ubicacion", "departamento",
    "provincia_distrito", "nro_convocatoria", "cantidad_vacantes",
    "remuneracion_texto", "remuneracion_num", "fecha_inicio_publicacion",
    "fecha_fin_publicacion", "experiencia", "formacion_academica",
    "especializacion", "conocimiento", "competencias", "detalle_url",
    "departamento_filtro", "pagina", "posicion_en_pagina", "fecha_extraccion",
]


def init_db(db_path: Path, reset: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    if reset:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_aviso TEXT, puesto TEXT, entidad TEXT, ubicacion TEXT,
            departamento TEXT, provincia_distrito TEXT, nro_convocatoria TEXT,
            cantidad_vacantes INTEGER, remuneracion_texto TEXT, remuneracion_num REAL,
            fecha_inicio_publicacion TEXT, fecha_fin_publicacion TEXT,
            experiencia TEXT, formacion_academica TEXT, especializacion TEXT,
            conocimiento TEXT, competencias TEXT, detalle_url TEXT,
            departamento_filtro TEXT, pagina INTEGER, posicion_en_pagina INTEGER,
            fecha_extraccion TEXT,
            experiencia_general INTEGER, experiencia_especifica INTEGER
        )
        """
    )
    conn.commit()
    return conn


def load_existing_keys(conn: sqlite3.Connection) -> set[tuple[str, str, str, str, str]]:
    rows = conn.execute(
        f"SELECT puesto, entidad, nro_convocatoria, fecha_inicio_publicacion, fecha_fin_publicacion FROM {TABLE_NAME}"
    ).fetchall()
    return {tuple(r) for r in rows}


def insert_postings(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    items: list[tuple[JobCard, JobDetail]],
) -> None:
    if not items:
        return
    placeholders = ", ".join(["?"] * len(INSERT_COLS))
    sql = f"INSERT INTO {TABLE_NAME} ({', '.join(INSERT_COLS)}) VALUES ({placeholders})"
    values = [
        [
            detail.nro_aviso, card.puesto, card.entidad, card.ubicacion,
            card.departamento, card.provincia_distrito, card.nro_convocatoria,
            card.cantidad_vacantes, card.remuneracion_texto, card.remuneracion_num,
            card.fecha_inicio_publicacion, card.fecha_fin_publicacion,
            detail.experiencia, detail.formacion_academica, detail.especializacion,
            detail.conocimiento, detail.competencias, detail.detalle_url,
            card.departamento_filtro, card.pagina, card.posicion_en_pagina,
            card.fecha_extraccion,
        ]
        for card, detail in items
    ]
    with lock:
        conn.executemany(sql, values)
        conn.commit()


def deduplicate_postings(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    before = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    cur.execute(
        f"""
        DELETE FROM {TABLE_NAME}
        WHERE nro_aviso != '' AND id NOT IN (
            SELECT MIN(id) FROM {TABLE_NAME} WHERE nro_aviso != '' GROUP BY nro_aviso
        )
        """
    )
    conn.commit()
    after = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    return before - after


def read_all_postings(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {TABLE_NAME}", conn)


def update_experience_batch(
    conn: sqlite3.Connection, updates: list[tuple[int | None, int | None, int]]
) -> None:
    if not updates:
        return
    conn.executemany(
        f"UPDATE {TABLE_NAME} SET experiencia_general = ?, experiencia_especifica = ? WHERE id = ?",
        updates,
    )
    conn.commit()


def export_json(df: pd.DataFrame, path: Path) -> None:
    df.to_json(path, orient="records", force_ascii=False, indent=2)
