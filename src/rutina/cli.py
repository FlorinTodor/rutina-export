"""Punto de entrada:  python -m rutina <comando>"""

from __future__ import annotations

import argparse
import json
import os
import logging
import sys
from datetime import date
from pathlib import Path

from .config import Config
from .models import to_jsonable

log = logging.getLogger("rutina")

RAW = Path("data/raw")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname).1s %(name)s: %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --------------------------------------------------------------- fuentes

def _recortar(workouts, desde: str):
    """Deja fuera lo anterior al cambio de gimnasio."""
    if not desde:
        return workouts
    try:
        corte = date.fromisoformat(desde)
    except ValueError:
        log.warning("hevy.desde = %r no es una fecha válida; no recorto", desde)
        return workouts
    dentro = [w for w in workouts if w.day >= corte]
    fuera = len(workouts) - len(dentro)
    if fuera:
        log.info("Recortados %d entrenos anteriores al %s (siguen en Hevy)", fuera, corte)
    return dentro


def load_workouts(cfg: Config):
    if cfg.hevy.mode == "csv":
        # El export CSV no trae template_id: sin catalogo, se agrupa por titulo.
        from .sources.hevy_csv import load_workouts as load_csv
        return _recortar(load_csv(cfg.hevy.csv_path), cfg.hevy.desde), {}

    from .sources.hevy_api import HevyAPI
    api = HevyAPI(cfg.hevy.api_key)
    who = api.user_info()
    log.info("Hevy conectado como %s (%d entrenos)", who.get("name"), api.workout_count())

    workouts = list(api.iter_workouts())

    # El catalogo de plantillas es la fuente del nombre canonico de cada
    # ejercicio, del grupo muscular y del equipamiento.
    try:
        templates = api.exercise_templates()
        log.info("Catalogo de ejercicios: %d plantillas", len(templates))
    except Exception as exc:  # es un extra, no debe tumbar la sincronizacion
        log.warning("No pude leer el catalogo de ejercicios: %s", exc)
        templates = {}

    return _recortar(workouts, cfg.hevy.desde), templates


def load_health(cfg: Config):
    """El historico de salud, tal cual esta en el repositorio.

    Los datos los sube el movil a data/inbox/ y los importa el workflow antes
    de llegar aqui (o el PC con scripts/pull_movil.py). Fundir con una lista
    vacia devuelve el historico entero.
    """
    from .history import merge_fields
    from .models import BodyMeasurement, DailyHealth

    daily = merge_fields([], DailyHealth, "health_daily.jsonl")
    body = merge_fields([], BodyMeasurement, "body.jsonl")
    log.info("Salud: %d dias, %d pesajes (historico local)", len(daily), len(body))
    return daily, body


def load_tape(cfg: Config):
    """Las medidas de cinta que haya en el historico.

    Las mete la app del movil a mano: ni la bascula ni Health Connect miden
    perimetros. Si nunca se ha metido ninguna, la lista viene vacia y el
    dashboard no pinta esa seccion.
    """
    from .history import merge_fields
    from .models import TapeMeasurement

    tape = merge_fields([], TapeMeasurement, "medidas.jsonl")
    if tape:
        log.info("Medidas de cinta: %d tandas, la ultima %s", len(tape), tape[-1].day)
    return tape


def dump_raw(name: str, items: list) -> None:
    """Historico crudo en JSONL: es lo que hace el repo la fuente de verdad."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{name}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(to_jsonable(item), ensure_ascii=False) + "\n")
    log.info("Guardado %s (%d registros)", path, len(items))


# --------------------------------------------------------------- comandos

def cmd_init_notion(cfg: Config, args) -> int:
    from .notion.client import NotionClient
    from .notion.schema import create_all

    parent = args.parent_page or cfg.notion.parent_page_id
    if not parent:
        log.error("Falta --parent-page (el id de la pagina de Notion donde crear las bases)")
        return 1

    client = NotionClient(cfg.notion.token)
    ids = create_all(client, parent.replace("-", ""))
    cfg.save_db_ids(ids)

    print("\nBases creadas. Anota estos ids como Secrets de GitHub:\n")
    for k, v in ids.items():
        print(f"  {k.upper():22} {v}")
    return 0


def _aviso_estado_desfasado() -> None:
    """El estado dice qué páginas existen ya en Notion. Si el remoto va por
    delante, sincronizar en local crea duplicados: la copia local no sabe de
    las páginas que creó el workflow."""
    import subprocess

    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"],
                       capture_output=True, timeout=30)
        r = subprocess.run(["git", "rev-list", "--count", "HEAD..origin/main"],
                           capture_output=True, text=True, timeout=15)
        pendientes = int((r.stdout or "0").strip() or 0)
    except Exception:  # sin red o sin remoto: no es motivo para parar
        return
    if pendientes:
        log.warning("=" * 66)
        log.warning("El remoto va %d commit(s) por delante.", pendientes)
        log.warning("Sincronizar ahora DUPLICARIA filas en Notion: tu estado")
        log.warning("local no conoce las paginas que creo el workflow.")
        log.warning("Ejecuta antes:  git pull --rebase origin main")
        log.warning("=" * 66)


def cmd_sync(cfg: Config, args) -> int:
    from collections import defaultdict

    from .notion.client import NotionClient
    from .notion.sync import NotionSync
    from .transform.metrics import build_day_rows, build_exercise_stats, find_prs

    if not args.dry_run:
        _aviso_estado_desfasado()

    workouts, templates = load_workouts(cfg)
    health, body = load_health(cfg)
    tape = load_tape(cfg)

    if args.since:
        cutoff = date.fromisoformat(args.since)
        workouts = [w for w in workouts if w.day >= cutoff]
        health = [h for h in health if h.day >= cutoff]
        body = [b for b in body if b.day >= cutoff]
        tape = [t for t in tape if t.day >= cutoff]
        log.info("Filtrado desde %s", cutoff)

    log.info("Total: %d entrenos, %d series, %d dias de salud, %d pesajes",
             len(workouts), sum(len(w.sets) for w in workouts), len(health), len(body))

    dump_raw("workouts", workouts)
    dump_raw("health_daily", health)
    dump_raw("body", body)
    if tape:
        dump_raw("medidas", tape)

    if args.dry_run:
        rows = build_day_rows(workouts, health, body, tape)
        print(f"\n[dry-run] Se escribirian {len(rows)} dias, {len(workouts)} entrenos, "
              f"{sum(1 for w in workouts for s in w.sets if s.set_type != 'warmup')} series, "
              f"{len(build_exercise_stats(workouts, templates))} ejercicios, {len(body)} pesajes.")
        for r in rows[-7:]:
            h = r.health
            print(f"  {r.day}  pasos={getattr(h,'steps',None) or '-':>6}  "
                  f"entreno={'si' if r.trained else 'no':2}  vol={r.volume_kg or '-'}")
        return 0

    if not cfg.notion.configured:
        log.error("Notion sin configurar. Ejecuta antes: python -m rutina init-notion --parent-page <id>")
        return 1

    client = NotionClient(cfg.notion.token)

    # Ampliar el esquema antes de escribir: Notion no crea una columna al
    # escribirla, responde 400 y se pierde la sincronizacion entera.
    from .notion.schema import asegurar_columnas
    asegurar_columnas(client, cfg.notion.db_ids)

    sync = NotionSync(client, cfg.notion.db_ids)

    rows = build_day_rows(workouts, health, body, tape)
    day_pages = sync.sync_dias(rows, rebuild=args.rebuild_index)
    from .transform import insights
    ex_stats = build_exercise_stats(workouts, templates)
    progresion: dict[str, list] = defaultdict(list)
    canon = {e.key: e.title for e in ex_stats}
    for w in workouts:
        mejor: dict[str, float] = {}
        for st in w.sets:
            if st.e1rm_kg and st.e1rm_kg > mejor.get(st.exercise_key, 0):
                mejor[st.exercise_key] = st.e1rm_kg
        for k, v in mejor.items():
            progresion[canon.get(k, k)].append((w.day, v))
    hoy = rows[-1].day if rows else date.today()
    estados = {e["titulo"]: e for e in insights.estado_ejercicios(ex_stats, hoy, progresion)}

    ex_pages = sync.sync_ejercicios(ex_stats, estados, rebuild=args.rebuild_index)
    wo_pages = sync.sync_entrenos(workouts, day_pages, find_prs(workouts, templates),
                                  templates, rebuild=args.rebuild_index)
    sync.sync_series(workouts, wo_pages, ex_pages, templates,
                     skip_warmups=not args.include_warmups, rebuild=args.rebuild_index)
    sync.sync_medidas(body, day_pages, tape=tape, rebuild=args.rebuild_index)
    sync.save_state()

    log.info("Sincronizacion completa · %d peticiones a Notion", client.calls)
    return 0


def cmd_build_views(cfg: Config, args) -> int:
    """Crea calendario, graficas y panel. Idempotente: no duplica vistas."""
    from .notion.client import NotionClient
    from .notion.sync import State
    from .notion.views import build_all

    if not cfg.notion.configured:
        log.error("Notion sin configurar")
        return 1

    # Fichero de estado propio: el sync reescribe el suyo entero y una
    # ejecucion concurrente borraria este registro.
    state = State.load("data/state/views.json")
    bucket = state.bucket("views_per_exercise")
    done = set(bucket) if not args.rebuild else set()

    client = NotionClient(cfg.notion.token)

    if args.dedupe:
        from .notion.views import ViewBuilder
        vb = ViewBuilder(client, cfg.notion.db_ids)
        total = sum(vb.dedupe(k) for k in cfg.notion.db_ids)
        print(f"{total} vistas duplicadas eliminadas")
        return 0

    b = build_all(client, cfg.notion.db_ids,
                  per_exercise=not args.no_per_exercise, done=done)

    for page_id in done:
        bucket[page_id] = {"page_id": page_id, "hash": "view"}
    state.save()

    print(f"\n{b.created} vistas creadas, {b.skipped} ya existian, "
          f"{len(b.failed)} fallidas · {client.calls} peticiones")
    for f in b.failed[:15]:
        print("  FALLO:", f)
    return 0


def cmd_gifs(cfg: Config, args) -> int:
    """Pone el GIF de cada ejercicio como icono de su ficha y dentro de ella."""
    from .notion.client import NotionClient
    from .notion.sync import State
    from .transform.gifs import gif_url, load_both, match
    from .transform.metrics import build_exercise_stats

    workouts, templates = load_workouts(cfg)
    stats = {e.title: e for e in build_exercise_stats(workouts, templates)}
    catalog = load_both()

    client = NotionClient(cfg.notion.token)
    client.session.headers["Notion-Version"] = "2025-09-03"
    ds = client.request("GET", f"/databases/{cfg.notion.db_ejercicios}")["data_sources"][0]["id"]

    state = State.load("data/state/views.json")
    done = state.bucket("gifs")

    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = client.request("POST", f"/data_sources/{ds}/query", json=body)
        pages.extend(res["results"])
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]

    applied = skipped = missing = 0
    for page in pages:
        title = "".join(t["plain_text"] for t in page["properties"]["Ejercicio"]["title"])
        if page["id"] in done and not args.rebuild:
            skipped += 1
            continue
        e = stats.get(title)
        found, sc = match(title, e.muscle_group if e else None, catalog,
                          equip=e.equipment if e else None)
        if not found:
            log.info("sin GIF: %s", title)
            missing += 1
            continue
        url = gif_url(found)
        # el icono es lo que se ve en la tabla; la imagen, al abrir la ficha
        client.request("PATCH", f"/pages/{page['id']}",
                       json={"icon": {"type": "external", "external": {"url": url}}})
        if not args.icon_only:
            client.request("PATCH", f"/blocks/{page['id']}/children", json={"children": [
                {"object": "block", "type": "image", "image": {
                    "type": "external", "external": {"url": url},
                    "caption": [{"type": "text", "text": {"content": found["name"]}}]}}
            ]})
        done[page["id"]] = {"page_id": page["id"], "hash": url}
        applied += 1
        log.info("%-36s -> %s (%.2f)", title[:36], found["name"][:34], sc)

    state.save()
    print(f"\n{applied} GIFs aplicados, {skipped} ya tenian, {missing} sin GIF disponible "
          f"· {client.calls} peticiones")
    return 0


def cmd_panel(cfg: Config, args) -> int:
    """Regenera la pagina Rutina: graficas en imagen + vistas enlazadas."""
    from collections import defaultdict

    from .notion.client import NotionClient
    from .notion.panel import build
    from .transform.metrics import build_day_rows, build_exercise_stats

    workouts, templates = load_workouts(cfg)
    health, body = load_health(cfg)
    rows = build_day_rows(workouts, health, body, load_tape(cfg))
    stats = build_exercise_stats(workouts, templates)

    # mejor e1RM por ejercicio y sesion, para las graficas de progresion
    canon = {e.key: e.title for e in stats}
    progression: dict[str, list] = defaultdict(list)
    for w in workouts:
        best: dict[str, float] = {}
        for st in w.sets:
            if st.e1rm_kg and st.e1rm_kg > best.get(st.exercise_key, 0):
                best[st.exercise_key] = st.e1rm_kg
        for k, v in best.items():
            progression[canon.get(k, k)].append((w.day, v))

    from .dashboard import build_payload, render
    # docs/ es lo que sirve GitHub Pages; la subcarpeta es la ruta secreta
    render(build_payload(workouts, rows, stats, templates, desde=cfg.hevy.desde),
           os.environ.get("DASHBOARD_OUT", "docs/dashboard/index.html"))

    client = NotionClient(cfg.notion.token)
    n = build(client, cfg.notion.db_ids, cfg.notion.parent_page_id,
              rows, stats, progression, dashboard_url=args.dashboard_url,
              workouts=workouts, templates=templates)
    print(f"\nPanel reconstruido: {n} bloques · {client.calls} peticiones")
    return 0


def cmd_import_fitdays(cfg: Config, args) -> int:
    """Importa el export propio de FitDays, con las 15 metricas.

    Health Connect solo deja pasar 5, asi que esta es la unica via para grasa
    visceral y subcutanea, musculo esqueletico, proteina y edad corporal.
    """
    import json

    from .history import merge_fields
    from .models import BodyMeasurement
    from .sources.fitdays_export import load

    fresh = load(args.path)
    if not fresh:
        log.error("El fichero no tiene pesajes utiles")
        return 1

    merged = merge_fields(fresh, BodyMeasurement, "body.jsonl")
    RAW.mkdir(parents=True, exist_ok=True)
    with (RAW / "body.jsonl").open("w", encoding="utf-8") as fh:
        for m in merged:
            fh.write(json.dumps(to_jsonable(m), ensure_ascii=False) + "\n")

    rich = sum(1 for m in merged if m.visceral_fat is not None)
    print(f"\n{len(fresh)} pesajes importados · {len(merged)} en el histórico · "
          f"{rich} con las métricas completas")
    print("Ejecuta ahora:  python -m rutina sync && python -m rutina panel")
    return 0


def cmd_import_health(cfg: Config, args) -> int:
    """Importa el JSON que deja la app propia en el movil.

    Misma via que la Google Sheet pero sin la hoja: la app lee Health Connect
    directamente. `--dry-run` no escribe nada y ensena en que se diferencia lo
    que trae el movil de lo que ya hay guardado, que es como se comprueba que
    la convencion de sueno coincide con la del historico viejo.
    """
    from .history import merge_fields
    from .models import BodyMeasurement, DailyHealth
    from .sources.health_app import load

    dias, cuerpo = load(args.path, sueno_por=args.sueno_por)
    if not dias and not cuerpo:
        log.error("El fichero no trae ningun dia utilizable")
        return 1

    if args.dry_run:
        return _comparar(dias)

    RAW.mkdir(parents=True, exist_ok=True)
    # Campo a campo y no fila entera: el historico viejo viene de la Google
    # Sheet, que traia columnas que la app no da (pisos, VO2max). Reemplazar
    # la fila las borraria; asi lo que la app trae manda y lo que no trae se
    # queda como estaba.
    merged = merge_fields(dias, DailyHealth, "health_daily.jsonl")
    with (RAW / "health_daily.jsonl").open("w", encoding="utf-8") as fh:
        for d in merged:
            fh.write(json.dumps(to_jsonable(d), ensure_ascii=False) + "\n")

    n_cuerpo = 0
    if cuerpo:
        # campo a campo: el export de FitDays trae 15 metricas y esto 7, y en
        # una fecha dada puede que una fuente tenga lo que a la otra le falta
        fundido = merge_fields(cuerpo, BodyMeasurement, "body.jsonl")
        with (RAW / "body.jsonl").open("w", encoding="utf-8") as fh:
            for m in fundido:
                fh.write(json.dumps(to_jsonable(m), ensure_ascii=False) + "\n")
        n_cuerpo = len(fundido)

    print(f"\n{len(dias)} dias del movil · {len(merged)} en el historico"
          + (f" · {len(cuerpo)} pesajes ({n_cuerpo} en total)" if cuerpo else ""))
    return 0


def _comparar(dias) -> int:
    """Enfrenta lo recien leido con lo guardado, sin tocar nada."""
    from .history import _load
    from .models import DailyHealth

    guardado = _load(RAW / "health_daily.jsonl", DailyHealth)
    campos = ("steps", "sleep_hours", "total_kcal", "distance_km", "resting_hr")
    print(f"\n{'dia':<12} {'campo':<13} {'guardado':>10} {'movil':>10}")
    print("-" * 48)
    cambios = 0
    for d in dias:
        viejo = guardado.get(d.day)
        for c in campos:
            nuevo_v = getattr(d, c, None)
            viejo_v = getattr(viejo, c, None) if viejo else None
            if nuevo_v == viejo_v:
                continue
            cambios += 1
            print(f"{d.day!s:<12} {c:<13} {_fmt(viejo_v):>10} {_fmt(nuevo_v):>10}")
    if not cambios:
        print("(identico a lo que ya hay guardado)")
    print("\nNada escrito. Quita --dry-run para aplicarlo.")
    return 0


def _fmt(v) -> str:
    return "-" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)


def cmd_import_medidas(cfg: Config, args) -> int:
    """Importa las medidas de cinta que sube la app del movil."""
    from .history import merge_fields
    from .models import TapeMeasurement
    from .sources.medidas_app import load

    fresh = load(args.path)
    if not fresh:
        log.error("El fichero no trae ninguna medida utilizable")
        return 1

    # campo a campo: una tanda puede traer solo pecho y abdomen, y no tiene
    # que borrar los brazos que se midieron ese mismo dia en otra tanda
    merged = merge_fields(fresh, TapeMeasurement, "medidas.jsonl")
    RAW.mkdir(parents=True, exist_ok=True)
    with (RAW / "medidas.jsonl").open("w", encoding="utf-8") as fh:
        for m in merged:
            fh.write(json.dumps(to_jsonable(m), ensure_ascii=False) + "\n")

    ultima = merged[-1]
    medidos = [f.replace("_cm", "").replace("_", " ") for f in
               TapeMeasurement.__dataclass_fields__
               if f.endswith("_cm") and getattr(ultima, f) is not None]
    print(f"\n{len(fresh)} tandas importadas · {len(merged)} en el historico")
    print(f"Ultima, {ultima.day}: {', '.join(medidos) or 'ninguna medida'}")
    return 0


def cmd_check(cfg: Config, args) -> int:
    ok = True
    print("Hevy    :", end=" ")
    try:
        if cfg.hevy.mode == "csv":
            p = Path(cfg.hevy.csv_path)
            print(f"modo CSV, {'encontrado' if p.exists() else 'FALTA'} {p}")
            ok &= p.exists()
        else:
            from .sources.hevy_api import HevyAPI
            info = HevyAPI(cfg.hevy.api_key).user_info()
            print(f"OK, conectado como {info.get('name')}")
    except Exception as exc:
        print(f"FALLO · {exc}")
        ok = False

    print("Salud   :", end=" ")
    try:
        from .history import _load
        from .models import DailyHealth
        hist = _load(RAW / "health_daily.jsonl", DailyHealth)
        ultimo = max(hist) if hist else None
        print(f"{len(hist)} dias en el historico, el ultimo {ultimo or 'ninguno'}")
        print("          se actualiza solo desde el movil (android/)")
    except Exception as exc:
        print(f"FALLO · {exc}")
        ok = False

    print("Notion  :", end=" ")
    try:
        from .notion.client import NotionClient
        c = NotionClient(cfg.notion.token)
        c.request("GET", "/users/me")
        print("OK" if cfg.notion.configured else "token OK, faltan ids de bases")
        ok &= cfg.notion.configured
    except Exception as exc:
        print(f"FALLO · {exc}")
        ok = False

    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rutina", description="Rutina -> Notion")
    p.add_argument("-c", "--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-notion", help="crea las 5 bases de datos en Notion")
    s.add_argument("--parent-page", help="id de la pagina donde crearlas")
    s.set_defaults(fn=cmd_init_notion)

    s = sub.add_parser("sync", help="sincroniza todo hacia Notion")
    s.add_argument("--dry-run", action="store_true", help="no escribe en Notion")
    s.add_argument("--since", help="solo desde esta fecha (YYYY-MM-DD)")
    s.add_argument("--rebuild-index", action="store_true",
                   help="reconstruye el indice preguntando a Notion (si se perdio el estado)")
    s.add_argument("--include-warmups", action="store_true",
                   help="sube tambien las series de calentamiento")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("build-views", help="crea calendario, graficas y panel en Notion")
    s.add_argument("--no-per-exercise", action="store_true",
                   help="omite la grafica dentro de cada ficha de ejercicio")
    s.add_argument("--rebuild", action="store_true",
                   help="reintenta las graficas por ejercicio ya marcadas como hechas")
    s.add_argument("--dedupe", action="store_true",
                   help="borra vistas repetidas por nombre y sale")
    s.set_defaults(fn=cmd_build_views)

    s = sub.add_parser("import-fitdays",
                       help="importa el export propio de FitDays (15 metricas)")
    s.add_argument("path", help="fichero exportado desde la app")
    s.set_defaults(fn=cmd_import_fitdays)

    s = sub.add_parser("import-health",
                       help="importa el JSON de la app del movil (Health Connect)")
    s.add_argument("path", help="fichero health.json traido del movil")
    s.add_argument("--dry-run", action="store_true",
                   help="no escribe: solo ensena las diferencias con el historico")
    s.add_argument("--sueno-por", choices=("fin", "inicio"), default="fin",
                   dest="sueno_por",
                   help="a que dia va una noche: al que te despiertas (por defecto)")
    s.set_defaults(fn=cmd_import_health)

    s = sub.add_parser("import-medidas",
                       help="importa las medidas de cinta del movil")
    s.add_argument("path", help="fichero medidas.json traido del movil")
    s.set_defaults(fn=cmd_import_medidas)

    s = sub.add_parser("panel", help="regenera la pagina Rutina con graficas")
    s.add_argument("--dashboard-url", default=os.environ.get("DASHBOARD_URL"),
                   help="enlace al dashboard interactivo")
    s.set_defaults(fn=cmd_panel)

    s = sub.add_parser("gifs", help="pone el GIF de cada ejercicio en su ficha")
    s.add_argument("--rebuild", action="store_true", help="reaplica los ya puestos")
    s.add_argument("--icon-only", action="store_true", help="solo el icono, sin imagen dentro")
    s.set_defaults(fn=cmd_gifs)

    s = sub.add_parser("check", help="comprueba credenciales y conexiones")
    s.set_defaults(fn=cmd_check)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(Config.load(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
