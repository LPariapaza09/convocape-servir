"""Contratos de datos tipados entre módulos."""

from dataclasses import dataclass
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ScrapeTask:
    dep_code: str
    dep_name: str
    direction: str  # "forward" | "backward"
    skip_steps: int
    start_page: int
    end_page: int
    total_pages: int


@dataclass
class JobDetail:
    nro_aviso: str = ""
    experiencia: str = ""
    formacion_academica: str = ""
    especializacion: str = ""
    conocimiento: str = ""
    competencias: str = ""
    detalle_url: str = ""


@dataclass
class JobCard:
    card_idx: int
    puesto: str
    entidad: str
    ubicacion: str
    departamento: str
    provincia_distrito: str
    nro_convocatoria: str
    cantidad_vacantes: int | None
    remuneracion_texto: str
    remuneracion_num: float | None
    fecha_inicio_publicacion: str
    fecha_fin_publicacion: str
    departamento_filtro: str
    pagina: int
    posicion_en_pagina: int
    fecha_extraccion: str

    def composite_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.puesto,
            self.entidad,
            self.nro_convocatoria,
            self.fecha_inicio_publicacion,
            self.fecha_fin_publicacion,
        )


class ExperienciaItem(BaseModel):
    id: int = Field(description="ID numérico del registro enviado en el lote.")
    experiencia_general: int | None = Field(
        description="Años de experiencia laboral general como entero (int). 0 si < 1 año, null si no se menciona."
    )
    experiencia_especifica: int | None = Field(
        description="Años de experiencia laboral específica como entero (int). 0 si < 1 año, null si no se menciona."
    )


class ExperienciaBatchResponse(BaseModel):
    resultados: list[ExperienciaItem]
