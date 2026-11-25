"""Capa de red (I/O) para extracción paralela e incremental de SERVIR."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
import re
import sqlite3
import threading
import time

from bs4 import BeautifulSoup
import requests

from servir_pipeline.config import AppConfig
from servir_pipeline.models import JobDetail, ScrapeTask
from servir_pipeline.parsers import parse_cards, parse_detail_html
from servir_pipeline.storage import insert_postings

logger = logging.getLogger(__name__)

AJAX_HEADERS_BASE = {
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def _base_payload(view_state: str, dep_code: str) -> dict[str, str]:
    return {
        "frmLstOfertsLabo": "frmLstOfertsLabo",
        "frmLstOfertsLabo:modalidadAcceso": "03",
        "frmLstOfertsLabo:txtPerfil": "",
        "frmLstOfertsLabo:cboDep_focus": "",
        "frmLstOfertsLabo:cboDep_input": dep_code,
        "frmLstOfertsLabo:txtPuesto": "",
        "frmLstOfertsLabo:autocompletar_input": "",
        "frmLstOfertsLabo:autocompletar_hinput": "",
        "frmLstOfertsLabo:txtNroConv": "",
        "javax.faces.ViewState": view_state,
    }


def _ajax_post(
    session: requests.Session, config: AppConfig, view_state: str, dep_code: str, source_id: str
) -> tuple[str, str]:
    payload = _base_payload(view_state, dep_code)
    payload.update(
        {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": source_id,
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "frmLstOfertsLabo:mensaje frmLstOfertsLabo",
            source_id: source_id,
        }
    )
    headers = {**AJAX_HEADERS_BASE, "Referer": config.base_url}
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = session.post(config.base_url, data=payload, headers=headers, timeout=30)
            if resp.status_code in (403, 429):
                raise RuntimeError(f"HTTP {resp.status_code} WAF/rate-limit")
            resp.raise_for_status()
            xml_soup = BeautifulSoup(resp.text, "xml")
            new_vs = view_state
            for upd in xml_soup.find_all("update"):
                if "javax.faces.ViewState" in upd.get("id", "") and upd.text:
                    new_vs = upd.text.strip()
            form_upd = xml_soup.find("update", {"id": "frmLstOfertsLabo"})
            if not form_upd or not form_upd.text:
                raise RuntimeError("Falta frmLstOfertsLabo en respuesta AJAX")
            return form_upd.text, new_vs
        except Exception as exc:
            last_err = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"AJAX POST {source_id} falló para dep={dep_code}: {last_err}")


def _fetch_detail(
    session: requests.Session, config: AppConfig, view_state: str, dep_code: str, card_idx: int
) -> JobDetail:
    payload = _base_payload(view_state, dep_code)
    payload[f"frmLstOfertsLabo:idPnlRepeatPuestos:{card_idx}:j_idt78"] = ""
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = session.post(config.base_url, data=payload, timeout=30, allow_redirects=True)
            if resp.status_code in (403, 429):
                raise RuntimeError(f"HTTP {resp.status_code} WAF/rate-limit")
            resp.raise_for_status()
            return parse_detail_html(resp.text)
        except Exception as exc:
            last_err = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Detalle falló dep={dep_code} idx={card_idx}: {last_err}")


def _build_dep_tasks(code: str, name: str, total_pages: int) -> list[ScrapeTask]:
    if total_pages == 0:
        return []
    if total_pages < 10:
        return [ScrapeTask(code, name, "forward", 0, 1, total_pages, total_pages)]
    if total_pages >= 40:
        q1, q2, q3 = total_pages // 4, total_pages // 2, (3 * total_pages) // 4
        return [
            ScrapeTask(code, name, "forward", 0, 1, q1, total_pages),
            ScrapeTask(code, name, "forward", q1, q1 + 1, q2, total_pages),
            ScrapeTask(code, name, "backward", total_pages - q3, q3, q2 + 1, total_pages),
            ScrapeTask(code, name, "backward", 0, total_pages, q3 + 1, total_pages),
        ]
    mid = (total_pages + 1) // 2
    return [
        ScrapeTask(code, name, "forward", 0, 1, mid, total_pages),
        ScrapeTask(code, name, "backward", 0, total_pages, mid + 1, total_pages),
    ]


def discover_tasks(config: AppConfig) -> tuple[int, list[ScrapeTask]]:
    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})
    resp = session.get(config.base_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    target_set = None if "TODOS" in config.departamentos else set(config.departamentos)
    deps = [
        (opt.get("value"), opt.get_text(strip=True))
        for opt in soup.select("#frmLstOfertsLabo\\:cboDep_input option")
        if opt.get("value") and opt.get("value") != "0"
    ]
    if target_set is not None:
        deps = [(c, n) for c, n in deps if n.upper() in target_set]

    def probe_dep(dep_tuple: tuple[str, str]) -> tuple[str, str, int, int]:
        code, name = dep_tuple
        ss = requests.Session()
        ss.headers.update({"User-Agent": config.user_agent})
        rr = ss.get(config.base_url, timeout=30)
        rr.raise_for_status()
        vs = BeautifulSoup(rr.text, "lxml").find("input", {"name": "javax.faces.ViewState"})["value"]
        html, _ = _ajax_post(ss, config, vs, code, "frmLstOfertsLabo:j_idt46")
        m_pag = re.search(r"Página\s+\d+\s+de\s+(\d+)", html)
        m_tot = re.search(r"(\d+)\s+puesto\(s\)\s+encontrado\(s\)\s+en\s+total\.", html)
        return code, name, int(m_pag.group(1)) if m_pag else 0, int(m_tot.group(1)) if m_tot else 0

    with ThreadPoolExecutor(max_workers=min(10, max(1, len(deps)))) as ex:
        dep_info = list(ex.map(probe_dep, deps))

    reported_total = 0
    tasks: list[ScrapeTask] = []
    for code, name, pages, count in dep_info:
        reported_total += count
        logger.info("Departamento %s: %d ofertas (%d páginas)", name, count, pages)
        tasks.extend(_build_dep_tasks(code, name, pages))

    tasks.sort(key=lambda t: -(abs(t.end_page - t.start_page) + 1))
    return reported_total, tasks


def _run_worker(
    task: ScrapeTask,
    config: AppConfig,
    conn: sqlite3.Connection,
    db_lock: threading.Lock,
    existing_keys: set[tuple[str, str, str, str, str]],
) -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})
    resp = session.get(config.base_url, timeout=30)
    resp.raise_for_status()
    vs = BeautifulSoup(resp.text, "lxml").find("input", {"name": "javax.faces.ViewState"})["value"]

    html, vs = _ajax_post(session, config, vs, task.dep_code, "frmLstOfertsLabo:j_idt46")
    if task.direction == "backward":
        html, vs = _ajax_post(session, config, vs, task.dep_code, "frmLstOfertsLabo:j_idt63")
        pages_iter = range(task.start_page, task.end_page - 1, -1)
        step_btn = "frmLstOfertsLabo:j_idt60"
    else:
        pages_iter = range(task.start_page, task.end_page + 1)
        step_btn = "frmLstOfertsLabo:j_idt62"

    for _ in range(task.skip_steps):
        html, vs = _ajax_post(session, config, vs, task.dep_code, step_btn)

    saved = 0
    for i, expected_page in enumerate(pages_iter):
        if i > 0:
            html, vs = _ajax_post(session, config, vs, task.dep_code, step_btn)
        m_pag = re.search(r"Página\s+(\d+)\s+de\s+(\d+)", html)
        actual_page = int(m_pag.group(1)) if m_pag else expected_page
        cards = parse_cards(html, task.dep_name, actual_page, datetime.now(timezone.utc).isoformat())
        new_cards = [c for c in cards if c.composite_key() not in existing_keys]
        if not new_cards:
            if task.direction == "forward" and existing_keys:
                logger.info("Early-stop %s (%s) en pág %d", task.dep_name, task.direction, actual_page)
                break
            continue

        batch_items = [
            (card, _fetch_detail(session, config, vs, task.dep_code, card.card_idx))
            for card in new_cards
        ]
        insert_postings(conn, db_lock, batch_items)
        saved += len(batch_items)

    return saved


def run_scrape(
    config: AppConfig,
    conn: sqlite3.Connection,
    existing_keys: set[tuple[str, str, str, str, str]],
) -> int:
    """Ejecuta el scraping paralelo y lanza RuntimeError si algún worker falla."""
    reported_total, tasks = discover_tasks(config)
    logger.info("Total reportado: %d | Tareas paralelas: %d", reported_total, len(tasks))

    db_lock = threading.Lock()
    total_saved = 0
    errors: list[tuple[ScrapeTask, Exception]] = []

    with ThreadPoolExecutor(max_workers=min(config.max_workers, max(1, len(tasks)))) as ex:
        futures = {
            ex.submit(_run_worker, task, config, conn, db_lock, existing_keys): task
            for task in tasks
        }
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                total_saved += fut.result()
            except Exception as exc:
                errors.append((task, exc))
                logger.error("Worker falló (%s %s): %s", task.dep_name, task.direction, exc)

    if errors:
        raise RuntimeError(f"{len(errors)} worker(s) fallaron durante el scraping.")
    return total_saved
