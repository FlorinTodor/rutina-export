"""Las metricas derivadas, que son las que pueden mentir sin que se note.

Un numero mal calculado en el dashboard no rompe nada: se lee, se cree y se
decide con el. Estas cuatro cosas ya habian salido mal alguna vez.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rutina.models import BodyMeasurement, DailyHealth, SetRecord, Workout
from rutina.transform import insights
from rutina.transform.metrics import DayRow, build_day_rows
from datetime import datetime

HOY = date(2026, 8, 31)


def dia(n: int) -> date:
    return HOY - timedelta(days=n)


def pesaje(n: int, kg: float) -> BodyMeasurement:
    return BodyMeasurement(day=dia(n), weight_kg=kg, fat_percent=25.0,
                           bmi=round(kg / 1.85 ** 2, 1),
                           muscle_mass_kg=round(kg * .7, 1),
                           fat_mass_kg=round(kg * .25, 2))


def filas(body=(), health=(), workouts=()) -> list[DayRow]:
    return build_day_rows(list(workouts), list(health), list(body))


# --- 1. la ventana de "hace 7 dias" tiene que ser de verdad de 7 dias ---

def test_ventanas_del_cuerpo():
    # pesajes solo hoy y hace un mes: NO hay referencia a 7 dias
    c = insights.cuerpo(filas(body=[pesaje(29, 100.0), pesaje(0, 96.8)]))
    assert c["peso"]["d7"] is None, "sin pesaje cerca, la casilla de 7 d va vacia"
    assert c["peso"]["d30"] == -3.2
    assert c["peso"]["n30"] == 29, "y dice los dias que de verdad separan los dos"
    print(f"7 d sin pesaje cerca: {c['peso']['d7']} · "
          f"30 d: {c['peso']['d30']} kg en {c['peso']['n30']} dias reales")

    # con un pesaje a 6 dias (dentro de la tolerancia de 3) si hay referencia
    c2 = insights.cuerpo(filas(body=[pesaje(29, 100.0), pesaje(6, 98.3),
                                     pesaje(0, 96.8)]))
    assert c2["peso"]["d7"] == -1.5 and c2["peso"]["n7"] == 6
    print(f"con pesaje a 6 dias: {c2['peso']['d7']} kg ({c2['peso']['n7']} dias)")


def test_tendencia_es_la_mediana():
    # un dia de +3 kg (comida, sal, agua) no debe arrastrar la tendencia
    ps = [(dia(4), 100.0), (dia(3), 100.4), (dia(2), 103.0),
          (dia(1), 100.2), (dia(0), 100.3)]
    serie = insights.tendencia_peso(ps)
    assert serie[-1][1] == 100.3, serie[-1]
    print(f"pesajes {[p[1] for p in ps]} -> tendencia {serie[-1][1]} kg")


# --- 2. el balance energetico solo sale con datos que lo aguanten ---

def test_energia_pide_datos():
    pocos = insights.energia(filas(body=[pesaje(2, 99.0), pesaje(1, 97.5),
                                         pesaje(0, 96.8)]))
    assert pocos.get("faltan"), "con 3 pesajes en 2 dias eso es agua, no grasa"
    print(f"con 3 pesajes en 2 dias: faltan datos ({pocos['pesajes']}/"
          f"{pocos['min_pesajes']})")

    # El caso que mandaba a pesarse mas a quien ya se pesaba a diario: seis
    # pesajes en seis dias seguidos. Pesajes sobran, lo que falta es recorrido,
    # y la tarjeta anunciaba "6 en 5" porque esta rama contaba los dias sin el
    # ultimo mientras la de abajo si lo contaba.
    seguidos = insights.energia(filas(body=[pesaje(n, 99.0 - n * .5)
                                            for n in range(5, -1, -1)]))
    assert seguidos.get("faltan")
    assert seguidos["pesajes"] == 6, seguidos
    assert seguidos["dias"] == 6, "seis dias seguidos abarcan seis dias, no cinco"
    assert seguidos["listo_el"] == (dia(5) + timedelta(days=13)).isoformat(), \
        seguidos["listo_el"]
    print(f"6 pesajes en 6 dias seguidos: faltan {seguidos['min_dias']} de "
          f"recorrido y solo hay {seguidos['dias']}; listo el {seguidos['listo_el']}")

    # medio kilo por semana a la baja durante tres semanas
    body = [pesaje(n, 100.0 - (20 - n) * 0.5 / 7) for n in range(20, -1, -2)]
    salud = [DailyHealth(day=dia(n), total_kcal=2500.0 + n) for n in range(20, -1, -1)]
    e = insights.energia(filas(body=body, health=salud))
    assert -0.6 < e["kg_semana"] < -0.4, e
    assert -700 < e["balance_kcal"] < -400, e
    assert e["ingesta_estimada"] < e["gasto_medio"]
    print(f"bajando {e['kg_semana']} kg/semana -> deficit de "
          f"{abs(e['balance_kcal'])} kcal/dia, comiendo ~{e['ingesta_estimada']}")


def test_kcal_reales_descarta_la_basal_apuntada():
    # Samsung apunta su basal tal cual los dias sin actividad: el mismo numero
    # clavado. Un gasto medido nunca cae dos veces en el mismo decimal.
    salud = ([DailyHealth(day=dia(n), total_kcal=2048.0) for n in range(20, 4, -1)]
             + [DailyHealth(day=dia(n), total_kcal=2500.0 + n) for n in range(4, -1, -1)])
    reales = insights.kcal_reales(filas(health=salud))
    assert len(reales) == 5, [r.health.total_kcal for r in reales]
    print(f"{len(salud)} dias con kcal -> {len(reales)} con gasto de verdad")


# --- 3. series por musculo: la referencia es externa, no tu historico ---

def entreno(n: int, tid: str, series: int) -> Workout:
    inicio = datetime(2026, 8, 31, 18, 0) - timedelta(days=n)
    sets = [SetRecord(workout_id=f"w{n}{tid}", exercise_index=0,
                      exercise_title=tid, exercise_template_id=tid,
                      set_index=i, set_type="normal", weight_kg=50.0, reps=10,
                      distance_meters=None, duration_seconds=None, rpe=None)
            for i in range(series)]
    return Workout(id=f"w{n}{tid}", title="s", start_time=inicio,
                   end_time=inicio + timedelta(hours=1), sets=sets)


def test_series_semanales():
    tpl = {"press": {"primary_muscle_group": "chest"},
           "sentadilla": {"primary_muscle_group": "quadriceps"}}
    ws = [entreno(n, "press", 6) for n in (1, 4, 8, 11, 15, 18, 22, 25)]
    ws += [entreno(3, "sentadilla", 4)]          # piernas: una sola vez
    ss = insights.series_semanales(ws, tpl, semanas=4)
    por = {m["musculo"]: m for m in ss["musculos"]}
    assert por["chest"]["estado"] == "ok", por["chest"]
    assert por["quadriceps"]["estado"] == "bajo", por["quadriceps"]
    assert por["quadriceps"]["semanas_cero"] == 3
    print(f"pecho {por['chest']['media']} series/sem ({por['chest']['estado']}), "
          f"cuadriceps {por['quadriceps']['media']} ({por['quadriceps']['estado']}, "
          f"{por['quadriceps']['semanas_cero']} semanas a cero)")


if __name__ == "__main__":
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_"):
            fn()
    print("\nOK · las metricas derivadas dicen lo que dicen que dicen")
