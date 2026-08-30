"""Métricas de comparación: lo que convierte un registro en información.

Un dato suelto no dice nada. 107,7 kg no significa nada; 107,7 kg tras subir
3,9 en treinta días mientras el músculo se mantiene, sí. Aquí se calcula todo
lo que necesita un punto de referencia.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..models import Workout
from .metrics import DayRow, ExerciseStats


def _ventana(rows: list[DayRow], desde: date, hasta: date) -> list[DayRow]:
    return [r for r in rows if desde <= r.day <= hasta]


def periodo(rows: list[DayRow], dias: int, fin: date | None = None) -> dict:
    """Resumen de los últimos N días, para comparar contra otro periodo."""
    fin = fin or (rows[-1].day if rows else date.today())
    ini = fin - timedelta(days=dias - 1)
    v = _ventana(rows, ini, fin)
    entrenados = [r for r in v if r.trained]
    pasos = [r.health.steps for r in v if r.health and r.health.steps]
    sueno = [r.health.sleep_hours for r in v if r.health and r.health.sleep_hours]
    return {
        "desde": ini.isoformat(), "hasta": fin.isoformat(),
        "sesiones": len(entrenados),
        "volumen": round(sum(r.volume_kg for r in entrenados)),
        "series": sum(r.working_sets for r in entrenados),
        "minutos": sum(r.training_min for r in entrenados),
        "pasos": round(sum(pasos) / len(pasos)) if pasos else None,
        "sueno": round(sum(sueno) / len(sueno), 2) if sueno else None,
    }


def rachas(rows: list[DayRow]) -> dict:
    """Racha de semanas seguidas entrenando, y descanso actual.

    Se cuenta por semanas y no por días: nadie entrena siete días seguidos, y
    una racha de días castigaría el descanso, que es parte del entrenamiento.
    """
    semanas = {r.day.isocalendar()[:2] for r in rows if r.trained}
    if not semanas:
        return {"actual": 0, "mejor": 0, "dias_desde": None}

    ordenadas = sorted(semanas)
    mejor = actual = 1
    for prev, sig in zip(ordenadas, ordenadas[1:]):
        lunes_prev = date.fromisocalendar(*prev, 1)
        seguidas = (date.fromisocalendar(*sig, 1) - lunes_prev).days == 7
        actual = actual + 1 if seguidas else 1
        mejor = max(mejor, actual)

    ultimo = max(r.day for r in rows if r.trained)
    hoy = rows[-1].day
    # la racha solo sigue viva si se entrenó esta semana o la pasada
    if (hoy.isocalendar()[:2] not in semanas
            and (hoy - timedelta(days=7)).isocalendar()[:2] not in semanas):
        actual = 0
    return {"actual": actual, "mejor": mejor, "dias_desde": (hoy - ultimo).days}


def reparto_muscular(workouts: list[Workout], templates: dict, dias: int = 30) -> list[dict]:
    """Volumen por músculo en los últimos N días frente a su peso histórico.

    Sirve para ver qué se está descuidando: si los isquios son el 8% de tu
    histórico pero el 2% del último mes, algo se ha quedado por el camino.
    """
    if not workouts:
        return []
    fin = max(w.day for w in workouts)
    ini = fin - timedelta(days=dias - 1)

    recientes: dict[str, float] = defaultdict(float)
    historico: dict[str, float] = defaultdict(float)
    for w in workouts:
        for s in w.sets:
            m = templates.get(s.exercise_template_id or "", {}).get("primary_muscle_group")
            if not m or m == "cardio":
                continue
            historico[m] += s.volume_kg
            if ini <= w.day <= fin:
                recientes[m] += s.volume_kg

    th = sum(historico.values()) or 1
    tr = sum(recientes.values()) or 1
    out = []
    for m in historico:
        pr, ph = recientes[m] / tr * 100, historico[m] / th * 100
        out.append({
            "musculo": m.replace("_", " "),
            "reciente": round(recientes[m]),
            "pct_reciente": round(pr, 1),
            "pct_historico": round(ph, 1),
            "desvio": round(pr - ph, 1),
        })
    return sorted(out, key=lambda x: -x["reciente"])


def estado_ejercicios(stats: list[ExerciseStats], hoy: date,
                      progresion: dict[str, list]) -> list[dict]:
    """Clasifica cada ejercicio: progresa, estancado o abandonado."""
    out = []
    for e in stats:
        if not e.last_performed:
            continue
        dias = (hoy - e.last_performed).days
        pts = sorted(progresion.get(e.title, []))
        ys = [p[1] for p in pts if p[1]]
        n = min(3, len(ys) // 2) or 1
        tend = round(sum(ys[-n:]) / n - sum(ys[:n]) / n, 1) if len(ys) >= 2 else 0.0

        if dias > 60:
            estado = "abandonado"
        elif e.sessions < 3:
            estado = "nuevo"
        elif tend > 1:
            estado = "progresa"
        elif tend < -1:
            estado = "retrocede"
        else:
            estado = "estancado"

        out.append({
            "titulo": e.title, "musculo": (e.muscle_group or "").replace("_", " "),
            "dias": dias, "sesiones": e.sessions, "tendencia": tend,
            "estado": estado, "pr": e.best_e1rm_kg,
        })
    return out


def cuerpo(body_rows: list[DayRow]) -> dict:
    """Peso, grasa y músculo a 7, 30 y 90 días.

    El dato que importa no es el peso: es si lo que sube es músculo o grasa.
    """
    pesajes = [(r.day, r.body) for r in body_rows if r.body and r.body.weight_kg]
    if not pesajes:
        return {}
    hoy, ult = pesajes[-1]

    def hace(dias: int):
        objetivo = hoy - timedelta(days=dias)
        previos = [b for d, b in pesajes if d <= objetivo]
        return previos[-1] if previos else None

    out = {"fecha": hoy.isoformat(), "pesajes": len(pesajes)}
    for campo, clave in (("weight_kg", "peso"), ("fat_mass_kg", "grasa_kg"),
                         ("muscle_mass_kg", "musculo"), ("fat_percent", "grasa_pct"),
                         ("visceral_fat", "visceral")):
        actual = getattr(ult, campo, None)
        out[clave] = {"actual": actual}
        for dias in (7, 30, 90):
            ref = hace(dias)
            v = getattr(ref, campo, None) if ref else None
            out[clave][f"d{dias}"] = round(actual - v, 2) if (actual and v) else None
    return out


def construir(rows: list[DayRow], workouts: list[Workout], templates: dict,
              stats: list[ExerciseStats], progresion: dict[str, list]) -> dict:
    hoy = rows[-1].day if rows else date.today()
    return {
        "semana": periodo(rows, 7),
        "semana_previa": periodo(rows, 7, hoy - timedelta(days=7)),
        "mes": periodo(rows, 30),
        "mes_previo": periodo(rows, 30, hoy - timedelta(days=30)),
        "rachas": rachas(rows),
        "musculos": reparto_muscular(workouts, templates),
        "ejercicios": estado_ejercicios(stats, hoy, progresion),
        "cuerpo": cuerpo(rows),
    }
