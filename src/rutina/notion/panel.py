"""Construye la pagina Rutina como panel de entrada.

Notion cobra las graficas nativas (1 gratis por workspace) y los dashboards
(plan Business), asi que el panel se compone de:
  * graficas renderizadas como PNG y subidas via file_uploads
  * vistas enlazadas de tipo calendario y tabla, que si son gratuitas

Es idempotente: borra el panel anterior y lo reconstruye.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from ..models import BodyMeasurement, DailyHealth
from ..transform.metrics import DayRow, ExerciseStats, build_exercise_stats
from .client import NotionClient, upload_file
from .views import ViewBuilder, by_date, chart as chart_cfg

log = logging.getLogger(__name__)

MARKER = "​"  # marca invisible para reconocer los bloques del panel


class PanelBuilder:
    def __init__(self, client: NotionClient, db_ids: dict[str, str], page_id: str):
        self.c = client
        self.c.session.headers["Notion-Version"] = "2025-09-03"
        self.db = db_ids
        self.page = page_id
        self.vb = ViewBuilder(client, db_ids)
        self.anchor: str | None = None   # parrafo de intro: el panel va detras
        self.pending: list[dict] = []
        self.n = 0

    # --- limpieza ---

    def wipe(self) -> int:
        """Borra el panel anterior, conservando las cinco bases de datos."""
        keep_dbs = {d.replace("-", "") for d in self.db.values()}
        removed = 0
        res = self.c.request("GET", f"/blocks/{self.page}/children?page_size=100")
        for b in res.get("results", []):
            if b["type"] == "child_database" and b["id"].replace("-", "") in keep_dbs:
                continue
            if b["type"] == "paragraph":
                self.anchor = b["id"]  # la intro se conserva y ancla el panel
                continue
            self.c.request("DELETE", f"/blocks/{b['id']}")
            removed += 1
        return removed

    # --- insercion encadenada, para que el panel quede sobre las bases ---

    def add(self, block: dict) -> None:
        """Encola un bloque. Se envian todos juntos en `flush`."""
        self.pending.append(block)

    def flush(self) -> None:
        """Un unico PATCH con todos los bloques, anclado tras la intro.

        Insertarlos de uno en uno no vale: este endpoint devuelve TODOS los
        hijos de la pagina, no el bloque creado, asi que encadenar por la
        respuesta es fragil. Con una sola llamada el orden esta garantizado.
        """
        if not self.pending:
            return
        body = {"children": self.pending}
        if self.anchor:
            body["after"] = self.anchor
        self.c.request("PATCH", f"/blocks/{self.page}/children", json=body)
        self.n += len(self.pending)
        self.pending = []

    def heading(self, text: str) -> None:
        self.add({"object": "block", "type": "heading_2", "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}]}})

    def heading_end(self, text: str) -> None:
        """Titular al final de la pagina, junto a las vistas enlazadas."""
        self.c.request("PATCH", f"/blocks/{self.page}/children", json={"children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": text}}]}}]})
        self.n += 1

    def callout(self, text: str, emoji: str = "\U0001f4a1",
                link: str | None = None, link_text: str = "") -> None:
        rich = [{"type": "text", "text": {"content": text}}]
        if link:
            rich.append({"type": "text",
                         "text": {"content": link_text or link, "link": {"url": link}},
                         "annotations": {"bold": True}})
        self.add({"object": "block", "type": "callout", "callout": {
            "rich_text": rich, "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background"}})

    def bookmark(self, url: str, caption: str = "") -> None:
        """Tarjeta de enlace. A diferencia de `embed`, no necesita que la URL
        sea publica para renderizar algo util."""
        block = {"object": "block", "type": "bookmark", "bookmark": {"url": url}}
        if caption:
            block["bookmark"]["caption"] = [
                {"type": "text", "text": {"content": caption}}]
        self.add(block)

    def embed(self, url: str) -> None:
        self.add({"object": "block", "type": "embed", "embed": {"url": url}})

    def bullets(self, lineas: list[str]) -> None:
        for t in lineas:
            self.add({"object": "block", "type": "bulleted_list_item",
                      "bulleted_list_item": {
                          "rich_text": [{"type": "text", "text": {"content": t}}]}})

    def image(self, path, caption: str = "") -> None:
        if not path or not Path(path).exists():
            return
        fid = upload_file(self.c, path)
        block = {"object": "block", "type": "image", "image": {
            "type": "file_upload", "file_upload": {"id": fid}}}
        if caption:
            block["image"]["caption"] = [{"type": "text", "text": {"content": caption}}]
        self.add(block)
        log.info("  imagen: %s", Path(path).name)

    def linked_view(self, key: str, name: str, vtype: str, configuration: dict,
                    filter_: dict | None = None, sorts: list | None = None) -> None:
        """Crea la vista enlazada y la reposiciona junto al resto del panel."""
        body = {
            "create_database": {"parent": {"type": "page_id", "page_id": self.page}},
            "data_source_id": self.vb.ds(key), "name": name, "type": vtype,
            "configuration": configuration,
        }
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        try:
            self.c.request("POST", "/views", json=body)
            self.n += 1
            log.info("  vista: %s", name)
        except Exception as exc:  # noqa: BLE001
            log.warning("  FALLO vista %s: %s", name, exc)


def build(client: NotionClient, db_ids: dict[str, str], page_id: str,
          rows: list[DayRow], stats: list[ExerciseStats],
          progression: dict[str, list], dashboard_url: str | None = None,
          workouts: list | None = None, templates: dict | None = None) -> int:
    """Regenera la pagina Rutina.

    Ya no se generan imagenes: todas las graficas viven en el dashboard, que
    es interactivo y cubre entrenos, actividad y composicion corporal. Aqui
    quedan el resumen, el dashboard embebido y las tablas nativas, que es lo
    que Notion hace bien.
    """
    p = PanelBuilder(client, db_ids, page_id)

    log.info("Limpiando panel anterior")
    log.info("  %d bloques eliminados", p.wipe())

    from ..transform import insights

    total_vol = sum(r.volume_kg for r in rows) / 1000
    sessions = sum(1 for r in rows if r.trained)
    ins = insights.construir(rows, workouts or [], templates or {}, stats, progression)
    sem, prev = ins["semana"], ins["semana_previa"]
    rac, cue = ins["rachas"], ins["cuerpo"]

    p.callout(f"{sessions} sesiones · {total_vol:,.0f} toneladas · {len(stats)} ejercicios"
              f" · racha de {rac['actual']} semanas (mejor: {rac['mejor']})."
              " Se actualiza solo cada dia a las 21:00.", "\U0001f3cb\ufe0f")

    # comparaciones: un numero suelto no dice nada sin su referencia
    def cambio(a, b, unidad="", dec=0):
        if a is None or b is None:
            return "sin dato"
        d = a - b
        signo = "+" if d > 0 else ""
        return f"{a:,.{dec}f}{unidad} ({signo}{d:,.{dec}f} vs semana anterior)"

    lineas = [
        f"Sesiones: {cambio(sem['sesiones'], prev['sesiones'])}",
        f"Volumen: {cambio(sem['volumen'], prev['volumen'], ' kg')}",
        f"Series: {cambio(sem['series'], prev['series'])}",
        f"Pasos/dia: {cambio(sem['pasos'], prev['pasos'])}",
        f"Sueno: {cambio(sem['sueno'], prev['sueno'], ' h', 2)}",
    ]
    if cue.get("peso", {}).get("actual"):
        g, m = cue.get("grasa_kg", {}), cue.get("musculo", {})
        lineas.append(
            f"Peso: {cue['peso']['actual']} kg"
            + (f" ({cue['peso']['d30']:+.1f} en 30 dias)" if cue["peso"].get("d30") else ""))
        if g.get("d90") is not None and m.get("d90") is not None:
            lineas.append(f"En 90 dias: {g['d90']:+.1f} kg de grasa, "
                          f"{m['d90']:+.1f} kg de musculo")

    p.heading("Esta semana")
    p.bullets(lineas)

    olvidados = [x for x in ins["musculos"] if x["desvio"] < -4][:3]
    if olvidados:
        p.callout("Estas descuidando: " + ", ".join(
            f"{x['musculo']} ({x['pct_reciente']}% este mes frente a "
            f"{x['pct_historico']}% historico)" for x in olvidados), "\U0001f9b5")

    if dashboard_url:
        p.heading("Panel")
        p.embed(dashboard_url)
        p.callout("Fuerza, actividad y cuerpo. Si el panel se ve pequeno, "
                  "abrelo a pantalla completa desde el propio bloque.",
                  "\U0001f4a1")

    p.flush()

    # --- vistas enlazadas: solo pueden anadirse al final ---
    d = p.vb.props("db_dias")
    e = p.vb.props("db_entrenos")
    x = p.vb.props("db_ejercicios")

    p.heading_end("Calendario")
    p.linked_view("db_dias", "Calendario", "calendar", {
        "type": "calendar", "date_property_id": d["Dia"],
        "view_range": "month", "show_weekends": True})

    p.heading_end("Ultimos records")
    p.linked_view("db_entrenos", "PRs", "table", {"type": "table"},
                  filter_={"property": e["PRs"], "multi_select": {"is_not_empty": True}},
                  sorts=[{"property": e["Fecha"], "direction": "descending"}])

    p.heading_end("Ejercicios")
    p.linked_view("db_ejercicios", "Ranking", "table", {"type": "table"},
                  sorts=[{"property": x["Volumen total (kg)"],
                          "direction": "descending"}])

    return p.n
