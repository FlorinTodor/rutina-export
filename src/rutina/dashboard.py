"""Renderiza el dashboard interactivo inyectando los datos en la plantilla.

Las graficas nativas de Notion son de pago (1 gratis por workspace), asi que la
capa explorable vive en esta pagina y se embebe en Notion. Aqui si hay hover,
busqueda y filtros sobre los 74 ejercicios.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
import unicodedata
from collections import defaultdict
from pathlib import Path

from .models import Workout
from .transform.gifs import gif_url, load_both, match, load_hevy, match_hevy
from .transform import insights
from .transform.metrics import DayRow, ExerciseStats

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "dashboard.html"
PLACEHOLDER = "/*__DATA__*/"


# Ejercicios personalizados: Hevy no les pone animacion, solo las iniciales,
# asi que se reutiliza la del equivalente de catalogo. Es la misma maquina,
# hecha a un brazo.
ALIAS = {
    "Remo Bajo Unilateral": "Iso-Lateral Row (Machine)",
    "Jalon Unilateral Maquina": "Single Arm Lat Pulldown",
}


def _slug(nombre: str) -> str:
    """El mismo nombre de fichero que usa scripts/hevy_grabar.py."""
    n = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")


def build_payload(workouts: list[Workout], rows: list[DayRow],
                  stats: list[ExerciseStats], templates: dict | None = None,
                  desde: str = "") -> dict:
    catalog = load_both()
    # las de Hevy primero: son las que el usuario ve al entrenar
    hevy_cat = load_hevy()
    # y por encima de todo, lo grabado de la propia app (scripts/hevy_grabar.py):
    # ahi no hay que adivinar nada, es literalmente su pantalla
    grabadas = {f.stem for f in Path("docs/media/hevy").glob("*.mp4")}

    # por ejercicio y dia: mejor e1RM, su serie, volumen y numero de series
    sessions: dict[str, dict[str, dict]] = defaultdict(dict)
    for w in sorted(workouts, key=lambda x: x.start_time):
        agg: dict[str, dict] = defaultdict(
            lambda: {"e1rm": 0.0, "vol": 0.0, "w": 0, "r": 0, "sets": 0})
        for s in w.sets:
            if s.set_type == "warmup":
                continue
            a = agg[s.exercise_key]
            a["vol"] += s.volume_kg
            a["sets"] += 1
            if s.e1rm_kg and s.e1rm_kg > a["e1rm"]:
                a["e1rm"] = s.e1rm_kg
                a["w"] = s.weight_kg or 0
                a["r"] = s.reps or 0
        for k, a in agg.items():
            if a["sets"]:
                sessions[k][w.day.isoformat()] = a

    exercises = []
    for e in stats:
        pts = [{"d": d, "e": round(a["e1rm"], 1), "v": round(a["vol"]),
                "w": a["w"], "r": a["r"], "s": a["sets"]}
               for d, a in sorted(sessions[e.key].items())]
        if not pts:
            continue
        ys = [p["e"] for p in pts if p["e"]]
        n = min(3, len(ys) // 2) or 1
        trend = (sum(ys[-n:]) / n - sum(ys[:n]) / n) if len(ys) >= 2 else 0.0
        found, _ = match(e.title, e.muscle_group, catalog, equip=e.equipment)
        anim = match_hevy(e.title, hevy_cat, e.equipment)
        clave = _slug(ALIAS.get(e.title, e.title))
        propia = clave if clave in grabadas else None
        exercises.append({
            "name": e.title,
            "muscle": (e.muscle_group or "otros").replace("_", " "),
            "equip": e.equipment or "",
            # por orden de fiabilidad: lo grabado de la app, la animacion del
            # CDN emparejada por nombre, y el GIF aproximado del repositorio
            # ../ porque el html vive en docs/<slug>/ y los videos en
            # docs/media/. Sin eso el navegador los buscaria dentro de <slug>/
            # y saldrian huecos en negro.
            "anim": (f"../media/hevy/{propia}.mp4" if propia
                     else anim["url"] if anim else None),
            "propia": bool(propia),
            "gif": gif_url(found) if found else None,
            "sessions": e.sessions, "vol": round(e.total_volume_kg),
            "prW": e.best_weight_kg, "prR": e.best_weight_reps,
            "prE": e.best_e1rm_kg,
            "prDate": e.best_e1rm_date.isoformat() if e.best_e1rm_date else None,
            "last": e.last_performed.isoformat() if e.last_performed else None,
            "trend": round(trend, 1), "pts": pts, "aliases": e.aliases,
        })

    # una fila por dia con TODO: entreno, actividad y composicion corporal
    daily = []
    for r in rows:
        h, b, t = r.health, r.body, r.tape
        entry = {"d": r.day.isoformat()}
        if r.trained:
            entry.update({"v": round(r.volume_kg), "s": r.working_sets,
                          "m": r.training_min, "t": r.workout_titles})
        if h:
            for key, attr in (("st", "steps"), ("km", "distance_km"),
                              ("kc", "total_kcal"), ("ka", "active_kcal"),
                              ("sl", "sleep_hours"), ("sd", "sleep_deep_h"),
                              ("sr", "sleep_rem_h"), ("sli", "sleep_light_h"),
                              ("sa", "sleep_awake_h"), ("hr", "resting_hr"),
                              ("hv", "hrv_ms")):
                v = getattr(h, attr, None)
                if v is not None:
                    entry[key] = v
        if b:
            # las 15 de FitDays, no solo las que sobreviven a Health Connect
            for key, attr in (("kg", "weight_kg"), ("fa", "fat_percent"),
                              ("mu", "muscle_mass_kg"), ("bo", "bone_mass_kg"),
                              ("wa", "water_percent"), ("bmi", "bmi"),
                              ("vi", "visceral_fat"), ("sc", "subcutaneous_fat_percent"),
                              ("sk", "skeletal_muscle_percent"), ("pr", "protein_percent"),
                              ("br", "bmr_kcal"), ("ag", "body_age"),
                              ("fm", "fat_mass_kg"), ("lm", "lean_mass_kg")):
                v = getattr(b, attr, None)
                if v is not None:
                    entry[key] = v
        if t:
            # prefijo "t" de cinta: "br" ya es el metabolismo basal y "pr"
            # la proteina, asi que sin prefijo habria colisiones
            for key, attr in (("tcu", "cuello_cm"), ("tpe", "pecho_cm"),
                              ("tci", "cintura_cm"), ("tab", "abdomen_cm"),
                              ("tca", "cadera_cm"),
                              ("tbi", "brazo_izq_cm"), ("tbd", "brazo_der_cm"),
                              ("tai", "antebrazo_izq_cm"), ("tad", "antebrazo_der_cm"),
                              ("tmi", "muslo_izq_cm"), ("tmd", "muslo_der_cm"),
                              ("tgi", "gemelo_izq_cm"), ("tgd", "gemelo_der_cm")):
                v = getattr(t, attr, None)
                if v is not None:
                    entry[key] = v
            if t.nota:
                entry["tn"] = t.nota
        daily.append(entry)

    trained = [d for d in daily if "v" in d]
    weighed = [d for d in daily if "kg" in d]
    stepped = [d for d in daily if "st" in d]
    slept = [d for d in daily if "sl" in d]

    progresion = defaultdict(list)
    for e in exercises:
        progresion[e["name"]] = [(date.fromisoformat(p["d"]), p["e"])
                                 for p in e["pts"] if p["e"]]

    return {
        # desde cuando cuentan los entrenos, para que la pagina lo diga en vez
        # de que parezca que falta historia
        "desde": desde,
        "exercises": exercises,
        "daily": daily,
        "insights": insights.construir(rows, workouts, templates or {},
                                       stats, progresion),
        "totals": {
            "sessions": len(trained),
            "volume": round(sum(d["v"] for d in trained)),
            "exercises": len(exercises),
            "from": daily[0]["d"] if daily else None,
            "to": daily[-1]["d"] if daily else None,
            "hasSteps": len(stepped),
            "hasSleep": len(slept),
            "hasBody": len(weighed),
            "weight": weighed[-1]["kg"] if weighed else None,
            "fat": weighed[-1].get("fa") if weighed else None,
        },
    }


# La plantilla es solo el cuerpo. Para servirla en GitHub Pages hace falta un
# documento completo, y con noindex: la URL es secreta por ser impredecible, no
# queremos que un buscador la publique.
SHELL = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive,nosnippet,noimageindex,notranslate">
<meta name="googlebot" content="noindex,nofollow,noarchive,nosnippet,noimageindex">
<meta name="bingbot" content="noindex,nofollow,noarchive,nosnippet">
<meta name="referrer" content="no-referrer">
{head}
</head>
<body>
{body}
</body>
</html>
"""


def render(payload: dict, out: str | Path = "data/dashboard.html",
           standalone: bool = True) -> Path:
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise RuntimeError(f"La plantilla no tiene el marcador {PLACEHOLDER}")
    # </script> dentro del JSON cerraria la etiqueta antes de tiempo
    blob = json.dumps(payload, separators=(",", ":"),
                      ensure_ascii=False).replace("</", "<\\/")
    body = html.replace(PLACEHOLDER, blob)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if standalone:
        # <title> y <link> pertenecen al head; la plantilla los lleva arriba
        # porque el publicador de artefactos monta su propia cabecera.
        #
        # Solo se mira lo que hay ANTES del primer <style>. El JavaScript del
        # heatmap contiene un `<title>` (el tooltip nativo de cada cuadro) y
        # una busqueda global se lo llevaba por delante, dejando el fichero
        # con un error de sintaxis.
        cut = body.find("<style")
        if cut < 0:
            raise RuntimeError("La plantilla no tiene <style>: no se donde acaba la cabecera")
        head_part, rest = body[:cut], body[cut:]
        head_tags = re.findall(r"<(?:title|link)\b[^>]*>(?:[^<]*</title>)?",
                               head_part)
        for tag in head_tags:
            head_part = head_part.replace(tag, "", 1)
        out.write_text(
            SHELL.format(head="\n".join(t.strip() for t in head_tags),
                         body=(head_part + rest).lstrip()), encoding="utf-8")
    else:
        out.write_text(body, encoding="utf-8")
    log.info("Dashboard: %s (%d KB, %d ejercicios)",
             out, out.stat().st_size // 1024, len(payload["exercises"]))
    return out
