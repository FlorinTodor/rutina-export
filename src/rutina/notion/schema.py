"""Definicion y creacion de las cinco bases de datos en Notion.

Se crean en dos fases porque una relacion necesita que la base destino ya
exista: primero las bases con sus propiedades simples, despues se parchean
las relaciones entre ellas.
"""

from __future__ import annotations

import logging

from .client import NotionClient

log = logging.getLogger(__name__)

# Propiedad que hace idempotente la sincronizacion: guarda la clave externa
# (id de Hevy, fecha...) para reconocer una fila ya creada.
SYNC = {"Sync ID": {"rich_text": {}}}

DIAS = {
    "Fecha": {"title": {}},
    "Dia": {"date": {}},
    "Pasos": {"number": {"format": "number"}},
    "Distancia (km)": {"number": {"format": "number"}},
    "Kcal activas": {"number": {"format": "number"}},
    "Kcal totales": {"number": {"format": "number"}},
    "Pisos": {"number": {"format": "number"}},
    "FC reposo": {"number": {"format": "number"}},
    "FC media": {"number": {"format": "number"}},
    "HRV (ms)": {"number": {"format": "number"}},
    "SpO2 (%)": {"number": {"format": "percent"}},
    "Sueno (h)": {"number": {"format": "number"}},
    "Sueno profundo (h)": {"number": {"format": "number"}},
    "Sueno REM (h)": {"number": {"format": "number"}},
    "Peso (kg)": {"number": {"format": "number"}},
    "Grasa (%)": {"number": {"format": "number"}},
    "Musculo (kg)": {"number": {"format": "number"}},
    "Entreno": {"checkbox": {}},
    "Volumen (kg)": {"number": {"format": "number"}},
    "Series efectivas": {"number": {"format": "number"}},
    "Min entrenando": {"number": {"format": "number"}},
    "Sesion": {"rich_text": {}},
    "Semana": {"rich_text": {}},
    "Dia semana": {"select": {"options": [
        {"name": n} for n in ["Lunes", "Martes", "Miercoles", "Jueves",
                              "Viernes", "Sabado", "Domingo"]]}},
    **SYNC,
}

ENTRENOS = {
    "Entreno": {"title": {}},
    "Fecha": {"date": {}},
    "Duracion (min)": {"number": {"format": "number"}},
    "Volumen (kg)": {"number": {"format": "number"}},
    "Series efectivas": {"number": {"format": "number"}},
    "Reps totales": {"number": {"format": "number"}},
    "Ejercicios": {"number": {"format": "number"}},
    "RPE medio": {"number": {"format": "number"}},
    "Densidad (kg/min)": {"number": {"format": "number"}},
    "Kg por serie": {"number": {"format": "number"}},
    "Musculatura": {"multi_select": {}},
    "PRs": {"multi_select": {}},
    "Notas": {"rich_text": {}},
    **SYNC,
}

SERIES = {
    "Serie": {"title": {}},
    "Fecha": {"date": {}},
    "Ejercicio (texto)": {"rich_text": {}},
    "Nº serie": {"number": {"format": "number"}},
    "Tipo": {"select": {"options": [
        {"name": "normal", "color": "blue"},
        {"name": "warmup", "color": "gray"},
        {"name": "dropset", "color": "orange"},
        {"name": "failure", "color": "red"}]}},
    "Peso (kg)": {"number": {"format": "number"}},
    "Reps": {"number": {"format": "number"}},
    "RPE": {"number": {"format": "number"}},
    "Volumen (kg)": {"number": {"format": "number"}},
    "e1RM (kg)": {"number": {"format": "number"}},
    **SYNC,
}

EJERCICIOS = {
    "Ejercicio": {"title": {}},
    "Grupo muscular": {"select": {}},
    "Equipo": {"select": {}},
    "Otros nombres": {"rich_text": {}},
    "Sesiones": {"number": {"format": "number"}},
    "Series totales": {"number": {"format": "number"}},
    "Volumen total (kg)": {"number": {"format": "number"}},
    "PR peso (kg)": {"number": {"format": "number"}},
    "PR reps": {"number": {"format": "number"}},
    "PR e1RM (kg)": {"number": {"format": "number"}},
    "Fecha PR": {"date": {}},
    "Ultima vez": {"date": {}},
    "Dias sin hacer": {"number": {"format": "number"}},
    "Tendencia (kg)": {"number": {"format": "number"}},
    "Estado": {"select": {"options": [
        {"name": "progresa", "color": "green"},
        {"name": "estancado", "color": "yellow"},
        {"name": "retrocede", "color": "orange"},
        {"name": "abandonado", "color": "red"},
        {"name": "nuevo", "color": "blue"}]}},
    **SYNC,
}

MEDIDAS = {
    "Pesaje": {"title": {}},
    "Fecha": {"date": {}},
    "Peso (kg)": {"number": {"format": "number"}},
    "Grasa (%)": {"number": {"format": "number"}},
    "Musculo (kg)": {"number": {"format": "number"}},
    "Masa magra (kg)": {"number": {"format": "number"}},
    "Masa osea (kg)": {"number": {"format": "number"}},
    "Agua (%)": {"number": {"format": "number"}},
    "Grasa visceral": {"number": {"format": "number"}},
    "IMC": {"number": {"format": "number"}},
    "Metabolismo basal (kcal)": {"number": {"format": "number"}},
    "Grasa (kg)": {"number": {"format": "number"}},
    "Grasa subcutanea (%)": {"number": {"format": "number"}},
    "Musculo esqueletico (%)": {"number": {"format": "number"}},
    "Proteina (%)": {"number": {"format": "number"}},
    "Edad corporal": {"number": {"format": "number"}},
    # Cinta metrica, tomada a mano. Comparte base con los pesajes porque la
    # fecha las une y son la misma pregunta ("como va el cuerpo"); separarlas
    # obligaria a mirar dos tablas para responderla.
    "Cuello (cm)": {"number": {"format": "number"}},
    "Pecho (cm)": {"number": {"format": "number"}},
    "Cintura (cm)": {"number": {"format": "number"}},
    "Abdomen (cm)": {"number": {"format": "number"}},
    "Cadera (cm)": {"number": {"format": "number"}},
    "Brazo izq (cm)": {"number": {"format": "number"}},
    "Brazo der (cm)": {"number": {"format": "number"}},
    "Antebrazo izq (cm)": {"number": {"format": "number"}},
    "Antebrazo der (cm)": {"number": {"format": "number"}},
    "Muslo izq (cm)": {"number": {"format": "number"}},
    "Muslo der (cm)": {"number": {"format": "number"}},
    "Gemelo izq (cm)": {"number": {"format": "number"}},
    "Gemelo der (cm)": {"number": {"format": "number"}},
    "Nota": {"rich_text": {}},
    **SYNC,
}

SPECS = [
    ("db_dias",       "Dias",      "📅", DIAS),
    ("db_entrenos",   "Entrenos",  "🏋️", ENTRENOS),
    ("db_series",     "Series",    "🔢", SERIES),
    ("db_ejercicios", "Ejercicios", "💪", EJERCICIOS),
    ("db_medidas",    "Medidas",   "⚖️", MEDIDAS),
]


def _rel(db_id: str) -> dict:
    return {"relation": {"database_id": db_id, "type": "single_property",
                         "single_property": {}}}


def create_all(client: NotionClient, parent_page_id: str) -> dict[str, str]:
    """Crea las cinco bases y las enlaza. Devuelve {clave: database_id}."""
    ids: dict[str, str] = {}

    for key, name, icon, props in SPECS:
        db = client.create_database(parent_page_id, name, props, icon=icon)
        ids[key] = db["id"]
        log.info("Base creada: %-11s %s", name, db["id"])

    # Fase 2: relaciones (ya existen ambos extremos)
    client.update_database(ids["db_entrenos"], {"Dia": _rel(ids["db_dias"])})
    client.update_database(ids["db_series"], {
        "Entreno": _rel(ids["db_entrenos"]),
        "Ejercicio": _rel(ids["db_ejercicios"]),
    })
    client.update_database(ids["db_medidas"], {"Dia": _rel(ids["db_dias"])})
    log.info("Relaciones enlazadas")

    return ids


def asegurar_columnas(client: NotionClient, db_ids: dict[str, str]) -> int:
    """Anade a las bases ya creadas las columnas que les falten.

    Sin esto, ampliar el esquema obliga a borrar las bases y rehacerlas, o a
    anadir las columnas a mano en Notion. Escribir una propiedad que no existe
    no la crea: Notion responde 400 y se pierde la sincronizacion entera.

    Solo anade. Nunca borra ni cambia el tipo de una columna existente, que es
    justo lo que destruiria datos si el esquema y la base discrepan por algo
    que no sea una ampliacion nuestra.
    """
    anadidas = 0
    for clave, _, _, spec in SPECS:
        db_id = db_ids.get(clave)
        if not db_id:
            continue
        actual = client.request("GET", f"/databases/{db_id}").get("properties", {})
        faltan = {k: v for k, v in spec.items() if k not in actual}
        if not faltan:
            continue
        log.info("%s: anadiendo %d columnas nuevas (%s)", clave, len(faltan),
                 ", ".join(list(faltan)[:4]) + ("..." if len(faltan) > 4 else ""))
        client.update_database(db_id, faltan)
        anadidas += len(faltan)
    return anadidas
