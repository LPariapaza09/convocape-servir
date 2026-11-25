"""Configuración centralizada e inyectable para el pipeline SERVIR."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppConfig:
    base_url: str = (
        "https://app.servir.gob.pe/DifusionOfertasExterno/"
        "faces/consultas/ofertas_laborales.xhtml"
    )
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    db_path: Path = BASE_DIR / "ofertas_servir.db"
    output_json: Path = BASE_DIR / "ofertas_economia.json"
    departamentos: tuple[str, ...] = ("AREQUIPA", "LIMA")
    max_workers: int = 12
    carrera_regex: str = "econom"
    solo_bachiller: bool = False
    max_exp_general: int = 1
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_batch_size: int = 200
    gemini_api_key: str | None = None
    reset_db: bool = False
    skip_scrape: bool = False


def load_config(
    *,
    departamentos: list[str] | None = None,
    carrera: str = "econom",
    solo_bachiller: bool = False,
    max_exp_general: int = 1,
    output_json: Path | None = None,
    db_path: Path | None = None,
    reset_db: bool = False,
    skip_scrape: bool = False,
) -> AppConfig:
    """Carga configuración desde .env y parámetros explícitos."""
    load_dotenv(BASE_DIR / ".env")
    return AppConfig(
        db_path=db_path or (BASE_DIR / "ofertas_servir.db"),
        output_json=output_json or (BASE_DIR / "ofertas_economia.json"),
        departamentos=tuple(d.upper() for d in (departamentos or ["AREQUIPA", "LIMA"])),
        carrera_regex=carrera,
        solo_bachiller=solo_bachiller,
        max_exp_general=max_exp_general,
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        reset_db=reset_db,
        skip_scrape=skip_scrape,
    )
