"""Creacion de vistas (calendario, graficas, panel) via la Views API.

Requiere Notion-Version 2025-09-03 o superior, mas nueva que la que usa el
resto del cliente. En esa version las propiedades y las consultas cuelgan del
"data source", no de la base, de ahi la indireccion de `ds()`.

Es idempotente: las vistas se reconocen por nombre y se omiten si ya existen.
"""

from __future__ import annotations

import logging

from .client import NotionClient, NotionError

log = logging.getLogger(__name__)

VIEWS_VERSION = "2025-09-03"


class ViewBuilder:
    def __init__(self, client: NotionClient, db_ids: dict[str, str]):
        self.c = client
        self.db = db_ids
        self.c.session.headers["Notion-Version"] = VIEWS_VERSION
        self._ds: dict[str, str] = {}
        self._props: dict[str, dict[str, str]] = {}
        self._existing: dict[str, dict[str, str]] = {}
        self.created = 0
        self.skipped = 0
        self.failed: list[str] = []

    # --- metadatos ---

    def ds(self, key: str) -> str:
        if key not in self._ds:
            db = self.c.request("GET", f"/databases/{self.db[key]}")
            self._ds[key] = db["data_sources"][0]["id"]
        return self._ds[key]

    def props(self, key: str) -> dict[str, str]:
        if key not in self._props:
            data = self.c.request("GET", f"/data_sources/{self.ds(key)}")
            self._props[key] = {n: p["id"] for n, p in data["properties"].items()}
        return self._props[key]

    def existing(self, key: str) -> dict[str, str]:
        """Mapa {nombre: id} de las vistas ya existentes.

        Ojo: `GET /views?database_id=` devuelve los objetos SIN `name` ni
        `type` (ambos llegan a null), asi que hay que pedir cada vista por
        separado. Sin esto la deteccion de duplicados no funciona y cada
        ejecucion vuelve a crear todas las vistas.
        """
        if key not in self._existing:
            res = self.c.request("GET", f"/views?database_id={self.db[key]}")
            found: dict[str, str] = {}
            for v in res.get("results", []):
                full = self.c.request("GET", f"/views/{v['id']}")
                if full.get("name"):
                    found.setdefault(full["name"], full["id"])
            self._existing[key] = found
        return self._existing[key]

    def dedupe(self, key: str) -> int:
        """Borra vistas repetidas por nombre, conservando la mas reciente."""
        res = self.c.request("GET", f"/views?database_id={self.db[key]}")
        by_name: dict[str, list[str]] = {}
        for v in res.get("results", []):
            full = self.c.request("GET", f"/views/{v['id']}")
            if full.get("name"):
                by_name.setdefault(full["name"], []).append(full["id"])
        removed = 0
        for name, ids in by_name.items():
            for vid in ids[:-1]:  # el ultimo es el bueno
                self.c.request("DELETE", f"/views/{vid}")
                log.info("  duplicado borrado: %s (%s)", name, vid[:8])
                removed += 1
        self._existing.pop(key, None)
        return removed

    # --- creacion ---

    def add(self, key: str, name: str, vtype: str, configuration: dict,
            filter_: dict | None = None, sorts: list | None = None) -> str | None:
        """Crea una vista de primer nivel en la base. Omite si ya existe."""
        if name in self.existing(key):
            self.skipped += 1
            return self.existing(key)[name]  # devolvemos el id: el panel lo reutiliza
        body = {
            "database_id": self.db[key],
            "data_source_id": self.ds(key),
            "name": name,
            "type": vtype,
        }
        # un dashboard se crea sin `configuration`; el resto la necesita
        if configuration:
            body["configuration"] = configuration
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        try:
            v = self.c.request("POST", "/views", json=body)
            self.existing(key)[name] = v["id"]
            self.created += 1
            log.info("  vista creada: %-24s (%s)", name, vtype)
            return v["id"]
        except NotionError as e:
            self.failed.append(f"{name}: {e.body[:160]}")
            log.warning("  FALLO %-24s %s", name, e.body[:200])
            return None

    def add_widget(self, dashboard_id: str, key: str, name: str, vtype: str,
                   configuration: dict, placement: dict | None = None,
                   filter_: dict | None = None, sorts: list | None = None) -> None:
        body = {
            "view_id": dashboard_id,
            "data_source_id": self.ds(key),
            "name": name,
            "type": vtype,
            "configuration": configuration,
            "placement": placement or {"type": "new_row"},
        }
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        try:
            self.c.request("POST", "/views", json=body)
            self.created += 1
            log.info("    widget: %s", name)
        except NotionError as e:
            self.failed.append(f"widget {name}: {e.body[:160]}")
            log.warning("    FALLO widget %-20s %s", name, e.body[:200])

    def add_linked(self, page_id: str, key: str, name: str, vtype: str,
                   configuration: dict, filter_: dict | None = None) -> str | None:
        """Crea una vista enlazada dentro de una pagina (p. ej. la ficha de un
        ejercicio con su propia grafica de progresion)."""
        body = {
            "create_database": {"parent": {"type": "page_id", "page_id": page_id}},
            "data_source_id": self.ds(key),
            "name": name,
            "type": vtype,
            "configuration": configuration,
        }
        if filter_:
            body["filter"] = filter_
        try:
            v = self.c.request("POST", "/views", json=body)
            self.created += 1
            return v["id"]
        except NotionError as e:
            self.failed.append(f"linked {name}: {e.body[:160]}")
            return None


# --- atajos para construir configuraciones ---

def by_date(pid: str, group: str = "week", asc: bool = True) -> dict:
    return {"type": "date", "property_id": pid, "group_by": group,
            "sort": {"type": "ascending" if asc else "descending"},
            "start_day_of_week": 1}


def by_prop(kind: str, pid: str) -> dict:
    cfg = {"type": kind, "property_id": pid, "sort": {"type": "manual"}}
    if kind == "title":  # los ejes de texto exigen group_by explicito
        cfg["group_by"] = "exact"
    return cfg


def agg(fn: str, pid: str) -> dict:
    return {"aggregator": fn, "property_id": pid}


def chart(kind: str, x: dict | None, y: dict | None, *, theme: str = "blue",
          height: str = "medium", labels: bool = False, **extra) -> dict:
    cfg = {"type": "chart", "chart_type": kind, "color_theme": theme,
           "height": height, "show_data_labels": labels}
    if x is not None:
        cfg["x_axis"] = x
    if y is not None:
        cfg["y_axis"] = y
    cfg.update(extra)
    return cfg


def number(value: dict, *, theme: str = "blue") -> dict:
    return {"type": "chart", "chart_type": "number", "value": value,
            "color_theme": theme}


def build_all(client: NotionClient, db_ids: dict[str, str],
              per_exercise: bool = True, done: set[str] | None = None) -> ViewBuilder:
    """Monta el conjunto completo de vistas. `done` lleva las fichas de
    ejercicio que ya tienen grafica, para no duplicarlas al reejecutar."""
    b = ViewBuilder(client, db_ids)
    done = done if done is not None else set()

    # ---------------------------------------------------------- DIAS
    d = b.props("db_dias")
    log.info("Dias")
    b.add("db_dias", "Calendario", "calendar", {
        "type": "calendar", "date_property_id": d["Dia"],
        "view_range": "month", "show_weekends": True})
    b.add("db_dias", "Volumen semanal", "chart",
          chart("column", by_date(d["Dia"]), agg("sum", d["Volumen (kg)"]),
                theme="blue"))
    b.add("db_dias", "Pasos", "chart",
          chart("line", by_date(d["Dia"], "day"), agg("average", d["Pasos"]),
                theme="green", smooth_line=True))
    b.add("db_dias", "Peso", "chart",
          chart("line", by_date(d["Dia"], "day"), agg("average", d["Peso (kg)"]),
                theme="orange", smooth_line=True))
    b.add("db_dias", "Sueno", "chart",
          chart("column", by_date(d["Dia"]), agg("average", d["Sueno (h)"]),
                theme="purple"))
    b.add("db_dias", "Dias entrenados", "chart",
          chart("column", by_date(d["Dia"], "month"), agg("checked", d["Entreno"]),
                theme="pink", labels=True))

    # ------------------------------------------------------ ENTRENOS
    e = b.props("db_entrenos")
    log.info("Entrenos")
    b.add("db_entrenos", "Volumen por mes", "chart",
          chart("column", by_date(e["Fecha"], "month"), agg("sum", e["Volumen (kg)"]),
                theme="blue", labels=True))
    b.add("db_entrenos", "Densidad", "chart",
          chart("line", by_date(e["Fecha"]), agg("average", e["Densidad (kg/min)"]),
                theme="red", smooth_line=True))
    b.add("db_entrenos", "Reparto muscular", "chart",
          chart("donut", by_prop("multi_select", e["Musculatura"]),
                {"aggregator": "count"}, theme="green",
                donut_labels="name_and_value"))
    b.add("db_entrenos", "Solo PRs", "table", {"type": "table"},
          filter_={"property": e["PRs"], "multi_select": {"is_not_empty": True}},
          sorts=[{"property": e["Fecha"], "direction": "descending"}])
    b.add("db_entrenos", "Recientes", "table", {"type": "table"},
          sorts=[{"property": e["Fecha"], "direction": "descending"}])

    # -------------------------------------------------------- SERIES
    s = b.props("db_series")
    log.info("Series")
    b.add("db_series", "Progresion e1RM", "chart",
          chart("line", by_date(s["Fecha"]), agg("max", s["e1RM (kg)"]),
                theme="green", smooth_line=True))
    b.add("db_series", "Tipos de serie", "chart",
          chart("donut", by_prop("select", s["Tipo"]), {"aggregator": "count"},
                theme="gray", donut_labels="name_and_value"))
    b.add("db_series", "Todas", "table", {"type": "table"},
          sorts=[{"property": s["Fecha"], "direction": "descending"}])

    # ---------------------------------------------------- EJERCICIOS
    x = b.props("db_ejercicios")
    log.info("Ejercicios")
    b.add("db_ejercicios", "Ranking por volumen", "chart",
          chart("bar", by_prop("title", x["Ejercicio"]),
                agg("sum", x["Volumen total (kg)"]), theme="blue", height="large"))
    b.add("db_ejercicios", "Por grupo muscular", "chart",
          chart("donut", by_prop("select", x["Grupo muscular"]),
                agg("sum", x["Volumen total (kg)"]), theme="orange",
                donut_labels="name_and_value"))
    b.add("db_ejercicios", "Records", "table", {"type": "table"},
          sorts=[{"property": x["PR e1RM (kg)"], "direction": "descending"}])
    b.add("db_ejercicios", "Sin tocar hace tiempo", "table", {"type": "table"},
          sorts=[{"property": x["Ultima vez"], "direction": "ascending"}])

    # ------------------------------------------------------- MEDIDAS
    m = b.props("db_medidas")
    log.info("Medidas")
    b.add("db_medidas", "Peso", "chart",
          chart("line", by_date(m["Fecha"], "day"), agg("average", m["Peso (kg)"]),
                theme="orange", smooth_line=True))
    b.add("db_medidas", "Grasa corporal", "chart",
          chart("line", by_date(m["Fecha"], "day"), agg("average", m["Grasa (%)"]),
                theme="red", smooth_line=True))
    b.add("db_medidas", "Musculo", "chart",
          chart("line", by_date(m["Fecha"], "day"), agg("average", m["Musculo (kg)"]),
                theme="green", smooth_line=True))

    # ------------------- grafica de progresion en cada ficha de ejercicio
    if per_exercise:
        log.info("Graficas por ejercicio")
        pages = []
        cursor = None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            res = client.request("POST", f"/data_sources/{b.ds('db_ejercicios')}/query",
                                 json=body)
            pages.extend(res["results"])
            if not res.get("has_more"):
                break
            cursor = res["next_cursor"]

        pending = [p for p in pages if p["id"] not in done]
        log.info("  %d fichas, %d sin grafica", len(pages), len(pending))
        for p in pending:
            name = "".join(t["plain_text"] for t in p["properties"]["Ejercicio"]["title"])
            ok = b.add_linked(p["id"], "db_series", f"Progresion · {name}"[:80], "chart",
                              chart("line", by_date(s["Fecha"]),
                                    agg("max", s["e1RM (kg)"]), theme="green",
                                    height="small", smooth_line=True),
                              filter_={"property": s["Ejercicio"],
                                       "relation": {"contains": p["id"]}})
            if ok:
                done.add(p["id"])

    return b


def build_panel_page(client: NotionClient, db_ids: dict[str, str],
                     page_id: str) -> int:
    """Monta el panel de entrada como pagina con vistas enlazadas.

    Las vistas de tipo `dashboard` existen en la API pero Notion exige plan
    Business para verlas, asi que el panel se construye con bloques normales
    mas vistas enlazadas (`create_database`), que funcionan en cualquier plan.
    """
    b = ViewBuilder(client, db_ids)
    d, e = b.props("db_dias"), b.props("db_entrenos")
    s, x = b.props("db_series"), b.props("db_ejercicios")
    m = b.props("db_medidas")

    def heading(text: str, emoji: str) -> None:
        client.request("PATCH", f"/blocks/{page_id}/children", json={"children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": f"{emoji}  {text}"}}]}}
        ]})

    def linked(key: str, name: str, vtype: str, configuration: dict,
               filter_: dict | None = None, sorts: list | None = None) -> None:
        body = {
            "create_database": {"parent": {"type": "page_id", "page_id": page_id}},
            "data_source_id": b.ds(key), "name": name, "type": vtype,
            "configuration": configuration,
        }
        if filter_:
            body["filter"] = filter_
        if sorts:
            body["sorts"] = sorts
        try:
            client.request("POST", "/views", json=body)
            b.created += 1
            log.info("  panel: %s", name)
        except NotionError as err:
            b.failed.append(f"panel {name}: {err.body[:160]}")
            log.warning("  FALLO panel %-22s %s", name, err.body[:200])

    heading("Calendario de entrenos", "📅")
    linked("db_dias", "Calendario", "calendar", {
        "type": "calendar", "date_property_id": d["Dia"],
        "view_range": "month", "show_weekends": True})

    heading("Volumen semanal", "📈")
    linked("db_dias", "Volumen semanal", "chart",
           chart("column", by_date(d["Dia"]), agg("sum", d["Volumen (kg)"]),
                 theme="blue", height="medium"))

    heading("Ultimos records", "🏆")
    linked("db_entrenos", "PRs", "table", {"type": "table"},
           filter_={"property": e["PRs"], "multi_select": {"is_not_empty": True}},
           sorts=[{"property": e["Fecha"], "direction": "descending"}])

    heading("Progresion de fuerza", "💪")
    linked("db_series", "e1RM maximo", "chart",
           chart("line", by_date(s["Fecha"]), agg("max", s["e1RM (kg)"]),
                 theme="green", smooth_line=True))

    heading("Reparto por grupo muscular", "🥧")
    linked("db_ejercicios", "Volumen por musculo", "chart",
           chart("donut", by_prop("select", x["Grupo muscular"]),
                 agg("sum", x["Volumen total (kg)"]), theme="orange",
                 donut_labels="name_and_value"))

    heading("Ejercicios y records", "📋")
    linked("db_ejercicios", "Ranking", "table", {"type": "table"},
           sorts=[{"property": x["Volumen total (kg)"], "direction": "descending"}])

    heading("Peso y composicion", "⚖️")
    linked("db_medidas", "Peso", "chart",
           chart("line", by_date(m["Fecha"], "day"), agg("average", m["Peso (kg)"]),
                 theme="orange", smooth_line=True))
    linked("db_medidas", "Grasa corporal", "chart",
           chart("line", by_date(m["Fecha"], "day"), agg("average", m["Grasa (%)"]),
                 theme="red", smooth_line=True))

    heading("Actividad diaria", "👟")
    linked("db_dias", "Pasos", "chart",
           chart("line", by_date(d["Dia"], "day"), agg("average", d["Pasos"]),
                 theme="green", smooth_line=True))
    linked("db_dias", "Sueno", "chart",
           chart("column", by_date(d["Dia"]), agg("average", d["Sueno (h)"]),
                 theme="purple"))

    for f in b.failed:
        log.warning("fallo: %s", f)
    return b.created
