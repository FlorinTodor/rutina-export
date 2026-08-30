"""Empareja los ejercicios de Hevy con los GIFs de ExerciseGymGifsDB.

Los nombres no coinciden literalmente (Hevy dice "Machine" donde el repo dice
"Lever"), asi que se puntua por solapamiento de palabras normalizadas y se
exige coincidencia de grupo muscular para aceptar un emparejamiento flojo.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CDN = "https://cdn.jsdelivr.net/gh/JahelCuadrado/ExerciseGymGifsDB@v1.1.0"
INDEX_URL = f"{CDN}/api/{{lang}}/exercises.json"

# Hevy -> vocabulario del repo
SYNONYMS = {
    "machine": {"lever", "machine"},
    "smith": {"smith"},
    "dumbbell": {"dumbbell"},
    "barbell": {"barbell"},
    "cable": {"cable"},
    "bodyweight": {"body", "weight"},
    "band": {"band"},
    "kettlebell": {"kettlebell"},
    "plate": {"plate", "lever"},
}
# ruido que no aporta a la comparacion
STOP = {"the", "a", "of", "with", "and", "on", "in", "to"}

# grupos musculares: Hevy -> repo. Usan vocabularios distintos y sin este
# mapa la penalizacion por musculo hunde emparejamientos correctos.
MUSCLE_ALIAS = {
    "quadriceps": "quads",
    "chest": "pectorals",
    "shoulders": "delts",
    "upper_back": "upper-back",
    "lower_back": "spine",
    "abdominals": "abs",
    "full_body": "cardio",
}


# Emparejamientos que el algoritmo no acierta: el repo usa otra convencion
# (el hack squat de maquina es un "sled", el pec deck es un "seated fly").
# Verificados a mano contra el catalogo.
OVERRIDES = {
    "Hack Squat (Machine)": "Sled Hack Squat",
    "Pull Over": "Lever Pullover",
    "Pull Over Unilateral": "Lever Pullover",
    "Iso-Lateral Row (Machine)": "Lever One Arm Bent Over Row",
    "Iso-Lateral Low Row": "Lever Narrow Grip Seated Row",
    "Chest Fly (Machine)": "Lever Seated Fly",
    "Butterfly (Pec Deck)": "Lever Seated Fly",
    "Leg Press Horizontal (Machine)": "Lever Horizontal One Leg Press",
    "Leg Press (Machine)": "Lever Alternate Leg Press",
    "Vertical Traction (Machine)": "Lever Front Pulldown",
    "Single Arm Lat Pulldown": "Cable One Arm Pulldown",
    "Jalon Unilateral Maquina": "Lever One Arm Lateral Wide Pulldown",
    "Extensión De Pierna Unilateral": "Lever Leg Extension",
    "Ab Wheel": "Band Assisted Wheel Rollerout",
}
# Sin GIF disponible en el repositorio: mejor ninguno que uno equivocado.
NO_GIF = {"Face Pull", "Running", "Lateral Polea (Por Detrás)", "Chest Fly (Band)"}


def norm(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", text)
    return [w for w in words if w not in STOP]


def expand(words: list[str]) -> set[str]:
    out = set(words)
    for w in words:
        out |= SYNONYMS.get(w, set())
    return out


# El repositorio de GIFs codifica el aparato en la primera palabra del nombre,
# y Hevy nos dice cual es en `equipment`. Sin cruzarlos, "Concentration Curl"
# (mancuerna, segun Hevy) acababa emparejado con "band-concentration-curl",
# que es con goma: mismo ejercicio, aparato equivocado, GIF que no reconoces.
FAMILIAS = {
    # "machine" en Hevy es un cajon de sastre: incluye poleas y multipower,
    # asi que aqui tiene que ser permisiva. Al restringirla a lever/machine
    # empeoraba 10 emparejamientos que estaban bien (el pushdown en polea se
    # iba a una extension en maquina) y solo arreglaba uno.
    "machine":         {"lever", "machine", "sled", "cable", "smith"},
    "cable":           {"cable"},
    "dumbbell":        {"dumbbell"},
    "barbell":         {"barbell", "smith", "ez"},
    "plate":           {"weighted", "lever", "plate"},
    "resistance_band": {"band"},
    "kettlebell":      {"kettlebell"},
    "suspension":      {"suspension", "trx"},
}
# el resto de prefijos que aparecen en el catalogo; sirven para saber que un
# candidato SI declara aparato y por tanto puede contradecir al de Hevy
PREFIJOS = set().union(*FAMILIAS.values())


def familia(cand_name: str) -> str | None:
    """El aparato que declara el nombre del GIF, si declara alguno."""
    primera = norm(cand_name)[:1]
    return primera[0] if primera and primera[0] in PREFIJOS else None


def score(hevy_title: str, hevy_muscle: str | None,
          cand_name: str, cand_muscle: str,
          hevy_equip: str | None = None) -> float:
    a, b = norm(hevy_title), norm(cand_name)
    ea, eb = expand(a), expand(b)
    jaccard = len(ea & eb) / len(ea | eb) if ea | eb else 0.0
    ratio = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    s = 0.55 * jaccard + 0.30 * ratio

    # si el nombre corto cabe entero en el largo, casi seguro es el mismo
    # ejercicio con calificativos de mas ("Leg Extension" en "Lever Leg Extension")
    short, long_ = (ea, eb) if len(ea) <= len(eb) else (eb, ea)
    if short and short <= long_:
        s += 0.15 * (len(short) / max(len(long_), 1)) + 0.10

    if hevy_muscle:
        want = MUSCLE_ALIAS.get(hevy_muscle, hevy_muscle)
        if want == cand_muscle:
            s += 0.15
        else:
            s -= 0.08

    # El aparato pesa mas que un matiz del nombre: un curl con goma y uno con
    # mancuerna se llaman casi igual y el GIF es irreconocible si te lo cambian.
    fam = familia(cand_name)
    if hevy_equip and fam:
        esperados = FAMILIAS.get(hevy_equip)
        if esperados is not None:
            s += 0.18 if fam in esperados else -0.22
    return s


def load_catalog(lang: str = "en", cache_dir: str | Path = "data/raw") -> list[dict]:
    cache = Path(cache_dir) / f"gifs_{lang}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    log.info("Descargando catalogo de GIFs (%s)...", lang)
    data = requests.get(INDEX_URL.format(lang=lang), timeout=60).json()
    items = data if isinstance(data, list) else data.get("exercises", [])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(items))
    return items


def load_both() -> list[dict]:
    """Catalogos en ingles y espanol juntos: los ejercicios personalizados del
    usuario suelen estar nombrados en espanol."""
    return load_catalog("en") + load_catalog("es")


def gif_url(item: dict) -> str:
    return f"{CDN}/{item['file'].lstrip('/')}"


def match(title: str, muscle: str | None, catalog: list[dict],
          threshold: float = 0.55, equip: str | None = None) -> tuple[dict | None, float]:
    if title in NO_GIF:
        return None, 0.0
    if title in OVERRIDES:
        want = OVERRIDES[title]
        for c in catalog:
            if c["name"] == want:
                return c, 1.0
        log.warning("Override %r no encontrado en el catalogo: %r", title, want)

    best, best_s = None, 0.0
    for cand in catalog:
        s = score(title, muscle, cand["name"], cand.get("muscle", ""), equip)
        if s > best_s:
            best, best_s = cand, s
    return (best, best_s) if best_s >= threshold else (None, best_s)


# ---------------------------------------------------------------- Hevy
# Las animaciones que ensena la propia app de Hevy. Se prefieren a las del
# repositorio de GIFs porque son EXACTAMENTE las que el usuario ve al entrenar:
# mismo ejercicio, misma maquina, mismo angulo.
#
# No salen de su API, que no devuelve imagenes, sino de la lista de ficheros
# que va dentro del APK; el CDN que las sirve es publico. La lista se refresca
# con scripts/hevy_assets.py y solo hace falta cuando Hevy anade ejercicios.

HEVY_ASSETS = Path("data/raw/hevy_assets.json")
# El catalogo extraido tiene ~200 de los ~1300 ejercicios de Hevy, asi que para
# muchos el correcto NO esta y el emparejador coge lo menos malo. Ensenar una
# elevacion lateral donde va un remo es peor que ensenar el GIF de siempre, que
# al menos es un remo.
#
# Con los datos reales la frontera es nitida: todo lo correcto puntua 0.56 o
# mas, y los dos errores graves ("Iso-Lateral Row" -> elevacion lateral,
# "Single Arm Cable Row" -> remo al menton) caen los dos en 0.55. Se corta en
# 0.58 con margen: se pierden un par de aciertos, que se van al GIF de
# respaldo, y no se cuela ningun video equivocado.
UMBRAL_HEVY = 0.58


def load_hevy(path: str | Path = HEVY_ASSETS) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("No pude leer %s: %s", p, exc)
        return []


def match_hevy(title: str, catalogo: list[dict],
               equip: str | None = None) -> dict | None:
    """La animacion de Hevy para este ejercicio, si hay uana bastante buena."""
    best, best_s = None, 0.0
    for c in catalogo:
        s = score(title, None, c["name"], "", equip)
        if s > best_s:
            best, best_s = c, s
    return best if best_s >= UMBRAL_HEVY else None
