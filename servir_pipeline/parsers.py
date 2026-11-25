"""Funciones puras de parseo HTML, normalización y filtrado (sin I/O)."""

import re
import unicodedata

from bs4 import BeautifulSoup
import pandas as pd

from servir_pipeline.models import JobCard, JobDetail


def normalize_ws(text: str | None) -> str:
    """Colapsa espacios en blanco y elimina extremos."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str | None) -> str:
    """Elimina tildes y convierte a minúsculas para búsquedas insensibles a acentos."""
    if not isinstance(text, str):
        return ""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def parse_int(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_remuneracion(text: str) -> float | None:
    cleaned = text.replace("S/.", "").replace("S/", "").replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def parse_cards(html: str, dep_name: str, pagina: int, extracted_at: str) -> list[JobCard]:
    """Extrae tarjetas .cuadro-vacantes desde un fragmento HTML."""
    soup = BeautifulSoup(html, "lxml")
    cards: list[JobCard] = []
    for idx, card in enumerate(soup.find_all(class_="cuadro-vacantes")):
        title_el = card.select_one(".titulo-vacante label")
        ent_el = card.select_one(".nombre-entidad .detalle-sp")
        puesto = normalize_ws(title_el.get_text(" ", strip=True) if title_el else "")
        entidad = normalize_ws(ent_el.get_text(" ", strip=True) if ent_el else "")

        fields: dict[str, str] = {}
        for sub in card.select(".sub-titulo"):
            label = normalize_ws(sub.get_text(" ", strip=True)).rstrip(":").lower()
            val_el = sub.find_next_sibling(class_="detalle-sp")
            if val_el is None and sub.parent is not None:
                val_el = sub.parent.select_one(".detalle-sp")
            fields[label] = normalize_ws(val_el.get_text(" ", strip=True) if val_el else "")

        ubicacion = fields.get("ubicación", "") or fields.get("ubicacion", "")
        if " - " in ubicacion:
            departamento, provincia_distrito = ubicacion.split(" - ", 1)
        else:
            departamento, provincia_distrito = ubicacion, ""

        rem_txt = fields.get("remuneración", "") or fields.get("remuneracion", "")
        cards.append(
            JobCard(
                card_idx=idx,
                puesto=puesto,
                entidad=entidad,
                ubicacion=ubicacion,
                departamento=departamento.strip(),
                provincia_distrito=provincia_distrito.strip(),
                nro_convocatoria=(
                    fields.get("número de convocatoria", "")
                    or fields.get("numero de convocatoria", "")
                ),
                cantidad_vacantes=parse_int(fields.get("cantidad de vacantes", "")),
                remuneracion_texto=rem_txt,
                remuneracion_num=parse_remuneracion(rem_txt),
                fecha_inicio_publicacion=(
                    fields.get("fecha inicio de publicación", "")
                    or fields.get("fecha inicio de publicacion", "")
                ),
                fecha_fin_publicacion=(
                    fields.get("fecha fin de publicación", "")
                    or fields.get("fecha fin de publicacion", "")
                ),
                departamento_filtro=dep_name,
                pagina=pagina,
                posicion_en_pagina=idx + 1,
                fecha_extraccion=extracted_at,
            )
        )
    return cards


def parse_detail_html(html: str) -> JobDetail:
    """Extrae campos detallados desde detalle_ofertas_laborales.xhtml."""
    soup = BeautifulSoup(html, "lxml")
    fields: dict[str, str] = {}
    nro_aviso = ""

    for sub in soup.select(".sub-titulo-2"):
        label = normalize_ws(sub.get_text(" ", strip=True))
        m_aviso = re.match(r"^N°\s*(\d+)$", label)
        if m_aviso:
            nro_aviso = m_aviso.group(1)
            continue
        key = label.rstrip(":").lower()
        val_el = sub.find_next_sibling(class_="detalle-sp")
        if val_el is None and sub.parent is not None:
            val_el = sub.parent.select_one(".detalle-sp")
        if val_el is not None:
            fields[key] = normalize_ws(val_el.get_text(" ", strip=True))

    detalle_url = ""
    for sub in soup.select("span.sub-titulo"):
        if normalize_ws(sub.get_text(" ", strip=True)).rstrip(":").lower() == "detalle":
            val_el = sub.find_next_sibling(class_="detalle-sp")
            if val_el is None and sub.parent is not None:
                val_el = sub.parent.select_one(".detalle-sp")
            if val_el is not None:
                detalle_url = normalize_ws(val_el.get_text(" ", strip=True))
            break

    return JobDetail(
        nro_aviso=nro_aviso,
        experiencia=fields.get("experiencia", ""),
        formacion_academica=(
            fields.get("formación académica - perfil", "")
            or fields.get("formacion academica - perfil", "")
        ),
        especializacion=fields.get("especialización", "") or fields.get("especializacion", ""),
        conocimiento=fields.get("conocimiento", ""),
        competencias=fields.get("competencias", ""),
        detalle_url=detalle_url,
    )


def filter_by_career(
    df: pd.DataFrame, carrera_regex: str, solo_bachiller: bool = False
) -> pd.DataFrame:
    """Filtra un DataFrame por carrera en formacion_academica."""
    fa_norm = df["formacion_academica"].fillna("").map(normalize_text)
    mask = fa_norm.str.contains(normalize_text(carrera_regex), regex=True)
    out = df[mask].copy()
    out["incluye_bachiller"] = fa_norm[mask].str.contains(r"bachiller", regex=True)
    if solo_bachiller:
        out = out[out["incluye_bachiller"]].copy()
    return out


def filter_by_max_experience(df: pd.DataFrame, max_exp_general: int) -> pd.DataFrame:
    """Filtra ofertas con experiencia_general <= max_exp_general (o sin requisito)."""
    if max_exp_general < 0:
        return df
    exp_num = pd.to_numeric(df["experiencia_general"], errors="coerce")
    return df[exp_num.isna() | (exp_num <= max_exp_general)].copy()
