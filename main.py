"""Orquestador principal CLI del pipeline SERVIR."""

import argparse
import logging
import sys

import pandas as pd

from servir_pipeline.config import load_config
from servir_pipeline.enrichment import enrich_experience
from servir_pipeline.parsers import (
    filter_active_postings,
    filter_by_career,
    filter_by_max_experience,
)
from servir_pipeline.scraper import run_scrape
from servir_pipeline.storage import (
    deduplicate_postings,
    export_json,
    init_db,
    load_existing_keys,
    load_exp_cache,
    read_all_postings,
    save_exp_cache,
    update_experience_batch,
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    configure_logging()
    logger = logging.getLogger("main")

    parser = argparse.ArgumentParser(description="Pipeline modular de ofertas SERVIR.")
    parser.add_argument("--departamentos", nargs="+", default=["AREQUIPA", "LIMA"])
    parser.add_argument("--carrera", default="econom")
    parser.add_argument("--solo-bachiller", action="store_true")
    parser.add_argument("--max-exp-general", type=int, default=1)
    parser.add_argument("--reset", action="store_true", help="Recrear BD desde cero.")
    parser.add_argument("--skip-scrape", action="store_true", help="Usar BD existente sin scrapear.")
    args = parser.parse_args()

    config = load_config(
        departamentos=args.departamentos,
        carrera=args.carrera,
        solo_bachiller=args.solo_bachiller,
        max_exp_general=args.max_exp_general,
        reset_db=args.reset,
        skip_scrape=args.skip_scrape,
    )

    conn = init_db(config.db_path, reset=config.reset_db)
    try:
        if not config.skip_scrape:
            existing_keys = load_existing_keys(conn)
            logger.info("Iniciando scraping (registros previos en BD: %d)", len(existing_keys))
            nuevos = run_scrape(config, conn, existing_keys)
            dups = deduplicate_postings(conn)
            logger.info("Scraping completado: %d nuevos, %d duplicados eliminados", nuevos, dups)

        df = read_all_postings(conn)
        df_filtered = filter_by_career(df, config.carrera_regex, config.solo_bachiller)
        logger.info("Ofertas filtradas por '%s': %d", config.carrera_regex, len(df_filtered))

        # Aplicar caché persistente de Gemini (por nro_aviso) para no reprocesar ofertas ya vistas
        exp_cache = load_exp_cache(config.cache_json)
        if exp_cache:
            aviso_series = df_filtered["nro_aviso"].fillna("").astype(str)
            cached_gen = aviso_series.map(lambda a: exp_cache[a][0] if a in exp_cache else None)
            cached_esp = aviso_series.map(lambda a: exp_cache[a][1] if a in exp_cache else None)
            df_filtered["experiencia_general"] = df_filtered["experiencia_general"].combine_first(cached_gen)
            df_filtered["experiencia_especifica"] = df_filtered["experiencia_especifica"].combine_first(cached_esp)

        # Re-check rows where nro_aviso is not in cache and not yet in DB
        pending_mask = (
            df_filtered["experiencia"].fillna("").str.strip().ne("")
            & ~df_filtered["nro_aviso"].fillna("").astype(str).isin(exp_cache.keys())
            & (df_filtered["experiencia_general"].isna() | df_filtered["experiencia_especifica"].isna())
        )
        pending_rows = [
            (int(r["id"]), str(r["experiencia"]))
            for _, r in df_filtered[pending_mask].iterrows()
        ]

        if pending_rows:
            if not config.gemini_api_key:
                raise RuntimeError("Falta GEMINI_API_KEY en .env para procesar registros pendientes.")
            updates = enrich_experience(
                pending_rows,
                api_key=config.gemini_api_key,
                model=config.gemini_model,
                batch_size=config.gemini_batch_size,
            )
            update_experience_batch(
                conn, [(v[0], v[1], k) for k, v in updates.items()]
            )
            gen_map = {k: v[0] for k, v in updates.items()}
            esp_map = {k: v[1] for k, v in updates.items()}
            df_filtered["experiencia_general"] = (
                df_filtered["id"].map(gen_map).combine_first(df_filtered["experiencia_general"])
            )
            df_filtered["experiencia_especifica"] = (
                df_filtered["id"].map(esp_map).combine_first(df_filtered["experiencia_especifica"])
            )

        # Actualizar caché con todos los registros procesados que tengan nro_aviso
        for _, r in df_filtered.iterrows():
            aviso = str(r.get("nro_aviso") or "").strip()
            if aviso and aviso not in exp_cache:
                eg = r["experiencia_general"]
                ee = r["experiencia_especifica"]
                if pd.notna(eg) or pd.notna(ee):
                    exp_cache[aviso] = (
                        int(eg) if pd.notna(eg) else None,
                        int(ee) if pd.notna(ee) else None,
                    )
        save_exp_cache(config.cache_json, exp_cache)

        df_final = filter_active_postings(df_filtered)
        df_final = filter_by_max_experience(df_final, config.max_exp_general)
        df_final = df_final.sort_values(
            ["incluye_bachiller", "remuneracion_num"], ascending=[False, False]
        )
        export_json(df_final, config.output_json)
        logger.info("Exportado %d registros a %s", len(df_final), config.output_json)
        return 0
    except Exception as exc:
        logger.exception("Error fatal en el pipeline: %s", exc)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
