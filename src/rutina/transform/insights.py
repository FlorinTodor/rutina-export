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
            # días desde el récord: "estancado" no dice lo mismo si el PR es
            # de anteayer que si lleva tres meses sin moverse
            "dias_pr": (hoy - e.best_e1rm_date).days if e.best_e1rm_date else None,
        })
    return out


# Cuánto puede alejarse el pesaje de referencia de la fecha que se pide.
# Sin esto, "hace 7 días" cogía el último pesaje anterior a esa fecha aunque
# fuera de hace un mes, y la casilla "7 d" enseñaba el cambio de 29 días: el
# número era real, la etiqueta mentía.
TOLERANCIA = {7: 3, 30: 10, 90: 25}


def _mediana(xs: list[float]) -> float:
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def tendencia_peso(pesajes: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Mediana de los pesajes de los últimos 7 días, para cada día pesado.

    Un pesaje suelto no es tu peso: la mediana del salto de un día al
    siguiente son 0,6 kg, que es agua, sal y a qué hora te subes. La mediana
    (y no la media) porque un día de 3 kg de más no debe arrastrar la línea.
    """
    out = []
    for i, (d, _) in enumerate(pesajes):
        ventana = [w for dd, w in pesajes[max(0, i - 20):i + 1]
                   if 0 <= (d - dd).days <= 6]
        if ventana:
            out.append((d, round(_mediana(ventana), 2)))
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
        """El pesaje más cercano a esa fecha, si cae dentro de la tolerancia."""
        objetivo = hoy - timedelta(days=dias)
        tol = TOLERANCIA.get(dias, 3)
        candidatos = [(abs((d - objetivo).days), d, b) for d, b in pesajes
                      if abs((d - objetivo).days) <= tol and d < hoy]
        if not candidatos:
            return None, None
        _, d, b = min(candidatos)
        return b, (hoy - d).days

    out = {"fecha": hoy.isoformat(), "pesajes": len(pesajes)}
    for campo, clave in (("weight_kg", "peso"), ("fat_mass_kg", "grasa_kg"),
                         ("muscle_mass_kg", "musculo"), ("fat_percent", "grasa_pct"),
                         ("visceral_fat", "visceral")):
        actual = getattr(ult, campo, None)
        out[clave] = {"actual": actual}
        for dias in (7, 30, 90):
            ref, reales = hace(dias)
            v = getattr(ref, campo, None) if ref else None
            out[clave][f"d{dias}"] = round(actual - v, 2) if (actual and v) else None
            # los días que de verdad separan los dos pesajes, para poder
            # decirlo en vez de fingir que son exactamente 7, 30 o 90
            out[clave][f"n{dias}"] = reales

    # el peso de verdad: la mediana móvil, no el último número de la báscula
    serie = tendencia_peso([(d, b.weight_kg) for d, b in pesajes])
    if serie:
        out["tendencia"] = {"actual": serie[-1][1], "fecha": serie[-1][0].isoformat()}
        for dias in (7, 30, 90):
            objetivo = hoy - timedelta(days=dias)
            tol = TOLERANCIA.get(dias, 3)
            cand = [(abs((d - objetivo).days), d, w) for d, w in serie
                    if abs((d - objetivo).days) <= tol and d < hoy]
            if cand:
                _, d, w = min(cand)
                out["tendencia"][f"d{dias}"] = round(serie[-1][1] - w, 2)
                out["tendencia"][f"n{dias}"] = (hoy - d).days
            else:
                out["tendencia"][f"d{dias}"] = None
                out["tendencia"][f"n{dias}"] = None
    return out


def perfil(body_rows: list[DayRow]) -> dict:
    """Altura, FFMI y cintura/altura: lo que el peso solo no dice.

    La altura no la da ninguna fuente (FitDays no la exporta y Health Connect
    tampoco), pero sale del propio dato: IMC = peso / altura², así que
    altura = raíz(peso / IMC). Sobre 193 pesajes da 1,850 m con 4 mm de
    dispersión, que es mejor que teclearla a mano y olvidarse de mantenerla.
    """
    filas = [(r.day, r.body) for r in body_rows if r.body and r.body.weight_kg]
    alturas = [(b.weight_kg / b.bmi) ** .5 for _, b in filas if b.bmi]
    if not alturas:
        return {}
    h = round(_mediana(alturas), 3)

    out = {"altura_m": h}
    serie = []
    for d, b in filas:
        if b.fat_percent is None:
            continue
        magra = b.weight_kg * (1 - b.fat_percent / 100)
        serie.append((d, round(magra / h ** 2, 2), round(magra, 1)))
    if serie:
        out["ffmi"] = serie[-1][1]
        out["ffmi_max"] = max(x[1] for x in serie)
        out["magra_kg"] = serie[-1][2]

    # cintura/altura: el marcador de riesgo metabólico que no depende de la
    # báscula. Por debajo de 0,5 es el objetivo de salud habitual.
    cintura = None
    for r in body_rows:
        if r.tape and (r.tape.cintura_cm or r.tape.abdomen_cm):
            cintura = (r.tape.cintura_cm, r.tape.abdomen_cm, r.day)
    if cintura:
        cm, ab, dia = cintura
        valor = cm or ab
        out["cintura_cm"] = cm
        out["abdomen_cm"] = ab
        out["cintura_altura"] = round(valor / (h * 100), 3)
        out["cintura_es_abdomen"] = cm is None
        out["cintura_fecha"] = dia.isoformat()
    return out


def energia(rows: list[DayRow], dias: int = 28) -> dict:
    """Déficit o superávit estimado, sin pesar un solo gramo de comida.

    No hace falta apuntar lo que comes: si el peso cae 0,5 kg por semana,
    estás comiendo ~550 kcal/día menos de lo que gastas (7.700 kcal por kg de
    tejido graso). Con el gasto medido de Health Connect encima, sale la
    ingesta aproximada, que es el número accionable.

    Se pide una ventana larga y varios pesajes porque la pendiente de tres
    días es agua, no grasa.
    """
    pesajes = [(r.day, r.body.weight_kg) for r in rows if r.body and r.body.weight_kg]
    fin = pesajes[-1][0] if pesajes else (rows[-1].day if rows else date.today())
    ini = fin - timedelta(days=dias - 1)
    v = [(d, w) for d, w in pesajes if d >= ini]
    # con menos de esto la "tendencia" es el agua de un día concreto; se dice
    # lo que falta en vez de dar un número que suena a medición
    #
    # `abarcan` se cuenta INCLUSIVO, como en la rama de abajo. Antes esta
    # devolvía la resta pelada y la otra la resta más uno, con el mismo nombre
    # (`dias`), así que seis pesajes en seis días seguidos se anunciaban como
    # "6 en 5" y no había forma de entender ese 5. Y el 5 tampoco era lo que
    # parecía: no es cuántos días tienen pesaje, es cuánto separa al primero
    # del último, que es lo que de verdad se exige.
    MIN_PESAJES, MIN_DIAS = 4, 14
    abarcan = (v[-1][0] - v[0][0]).days + 1 if v else 0
    if len(v) < MIN_PESAJES or abarcan < MIN_DIAS:
        out = {"faltan": True, "pesajes": len(v), "dias": abarcan,
               "min_pesajes": MIN_PESAJES, "min_dias": MIN_DIAS,
               "ventana": dias}
        # Si los pesajes ya sobran y lo unico que falta es recorrido, la fecha
        # en que se cumple esta decidida: el primero de la ventana mas los dias
        # que hay que abarcar. Es mejor dato que "sigue pesandote": dice cuando.
        if len(v) >= MIN_PESAJES:
            out["listo_el"] = (v[0][0] + timedelta(days=MIN_DIAS - 1)).isoformat()
        return out

    # regresión lineal del peso contra el día: la pendiente es la tendencia
    xs = [(d - v[0][0]).days for d, _ in v]
    ys = [w for _, w in v]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if not var:
        return {}
    kg_dia = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var

    out = {
        "desde": v[0][0].isoformat(), "hasta": v[-1][0].isoformat(),
        "pesajes": len(v), "dias": (v[-1][0] - v[0][0]).days + 1,
        "kg_semana": round(kg_dia * 7, 2),
        "balance_kcal": round(kg_dia * 7700),
    }

    gastos = [r.health.total_kcal for r in kcal_reales(rows)
              if ini <= r.day <= fin]
    if len(gastos) >= 5:
        gasto = sum(gastos) / len(gastos)
        out["gasto_medio"] = round(gasto)
        out["dias_gasto"] = len(gastos)
        out["ingesta_estimada"] = round(gasto + kg_dia * 7700)
    return out


def kcal_reales(rows: list[DayRow]) -> list[DayRow]:
    """Días con gasto medido, sin los que solo traen la basal apuntada.

    Samsung escribe su estimación de metabolismo basal tal cual los días que
    no registra actividad, y ese número sale idéntico en decenas de días
    (y ese número cambia con el tiempo). Un gasto real nunca cae dos
    veces en el mismo decimal, así que un valor repetido 3 veces o más es
    "sin dato", no un día de quemar poco. Mismo criterio que el dashboard.
    """
    valores: dict[float, int] = defaultdict(int)
    for r in rows:
        if r.health and r.health.total_kcal:
            valores[r.health.total_kcal] += 1
    return [r for r in rows
            if r.health and r.health.total_kcal and valores[r.health.total_kcal] < 3]


def series_semanales(workouts: list[Workout], templates: dict,
                     semanas: int = 6) -> dict:
    """Series efectivas por músculo y semana, contra el rango que sirve.

    Sustituye a comparar tu reparto contra tu propio histórico: con pocas
    semanas de datos el histórico ES el mes, todos los desvíos salen ~0 y el
    aviso nunca puede saltar. La referencia útil es externa: en torno a
    10-20 series semanales por grupo, y al menos dos estímulos por semana.
    """
    if not workouts:
        return {}
    fin = max(w.day for w in workouts)
    # se cuenta por semanas ISO completas hacia atrás desde la última entrenada
    ini = fin - timedelta(days=7 * semanas - 1)

    por_semana: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    dias_musculo: dict[str, set] = defaultdict(set)
    for w in workouts:
        if not (ini <= w.day <= fin):
            continue
        sem = w.day.isocalendar()[:2]
        vistos = set()
        for s in w.sets:
            if s.set_type == "warmup":
                continue
            m = templates.get(s.exercise_template_id or "", {}).get("primary_muscle_group")
            if not m or m == "cardio":
                continue
            por_semana[sem][m] += 1
            vistos.add(m)
        for m in vistos:
            dias_musculo[m].add(w.day)

    if not por_semana:
        return {}
    orden = sorted(por_semana)
    n = len(orden)
    musculos = sorted({m for s in por_semana.values() for m in s})

    filas = []
    for m in musculos:
        serie = [por_semana[s].get(m, 0) for s in orden]
        media = sum(serie) / n
        # frecuencia: días distintos con ese músculo por semana
        frec = len(dias_musculo[m]) / n
        filas.append({
            "musculo": m.replace("_", " "),
            "media": round(media, 1),
            "ultima": serie[-1],
            "frecuencia": round(frec, 1),
            "semanas_cero": sum(1 for x in serie if x == 0),
            "serie": serie,
            "estado": "bajo" if media < 10 else "alto" if media > 22 else "ok",
        })
    filas.sort(key=lambda x: -x["media"])
    return {
        "semanas": [f"{a}-W{b:02d}" for a, b in orden],
        "n_semanas": n,
        "musculos": filas,
        "total_medio": round(sum(f["media"] for f in filas), 1),
    }


def cinta(rows: list[DayRow], cada: int = 14) -> dict:
    """Cuándo tocó la última tanda de cinta y si ya toca otra.

    Es la única medida del cuerpo que no sale de la báscula, así que dejar de
    tomarla deja el panel sin ninguna señal real de forma física.
    """
    tandas = [r.day for r in rows if r.tape and r.tape.algo_medido]
    if not tandas:
        return {"tandas": 0, "toca": True, "dias": None}
    hoy = rows[-1].day
    dias = (hoy - tandas[-1]).days
    return {"tandas": len(tandas), "ultima": tandas[-1].isoformat(),
            "dias": dias, "toca": dias >= cada, "cada": cada}


def construir(rows: list[DayRow], workouts: list[Workout], templates: dict,
              stats: list[ExerciseStats], progresion: dict[str, list]) -> dict:
    hoy = rows[-1].day if rows else date.today()
    return {
        "semana": periodo(rows, 7),
        "semana_previa": periodo(rows, 7, hoy - timedelta(days=7)),
        "mes": periodo(rows, 30),
        "mes_previo": periodo(rows, 30, hoy - timedelta(days=30)),
        "rachas": rachas(rows),
        "series_semanales": series_semanales(workouts, templates),
        "ejercicios": estado_ejercicios(stats, hoy, progresion),
        "cuerpo": cuerpo(rows),
        "perfil": perfil(rows),
        "energia": energia(rows),
        "cinta": cinta(rows),
    }
