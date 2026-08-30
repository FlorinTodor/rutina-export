#!/usr/bin/env python3
"""Graba de la app de Hevy la animacion de cada ejercicio.

Por que grabando y no descargando: la API de Hevy no devuelve imagenes, y de
su CDN solo se puede deducir la mitad del catalogo, asi que emparejar por
nombre acertaba en unos y en otros ensenaba otro ejercicio. Grabando lo que la
app pinta no hay que adivinar nada: es EXACTAMENTE lo que el usuario ve.

Recorrido por cada ejercicio, todo por ADB:

    Perfil -> Exercises -> buscar por nombre -> abrir -> grabar 5 s -> atras

Despues se recorta la zona de la animacion, se escala y se guarda en
docs/media/hevy/, que es lo que sirve GitHub Pages.

    python scripts/hevy_grabar.py                 # todos los de la web
    python scripts/hevy_grabar.py --solo "Pull Over" "Face Pull"
    python scripts/hevy_grabar.py --rehacer       # tambien los ya grabados

Tarda: unos 25 segundos por ejercicio. Solo hay que rehacerlo cuando aparezca
un ejercicio nuevo.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movil  # noqa: E402
from adb_ui import dump, find, tap  # noqa: E402
from movil import Fallo, conectar, despertar  # noqa: E402

log = logging.getLogger("grabar")

PKG = "com.hevy"
ROOT = Path(__file__).resolve().parents[1]
DESTINO = ROOT / "docs" / "media" / "hevy"
REMOTO = f"{movil.CARPETA}/rec.mp4"

# La animacion ocupa la banda de arriba, sobre fondo blanco, justo debajo de
# las pestanas Summary/History/How to. Medido sobre una captura de 1080x2340.
# se empieza 70 px mas abajo para dejar fuera el boton de pausa que la app
# pinta en la esquina de la animacion
RECORTE = "crop=1080:520:0:450"
ANCHO = 420          # suficiente para el hueco del dashboard, y pesa poco
SEGUNDOS = 5         # el ciclo de la animacion dura menos


def slug(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")


def ejercicios_de_la_web() -> list[str]:
    """Los que salen en el dashboard, que son los que importan."""
    sys.path.insert(0, str(ROOT / "src"))
    from rutina.config import Config
    from rutina.cli import load_workouts
    from rutina.transform.metrics import build_exercise_stats

    cfg = Config.load(str(ROOT / "config.toml"))
    w, t = load_workouts(cfg)
    return [e.title for e in build_exercise_stats(w, t)]


# ------------------------------------------------------------------ navegar

def abrir_biblioteca() -> None:
    movil.shell(f"am force-stop {PKG}")
    time.sleep(1)
    movil.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(6)
    pulsar("Profile")
    time.sleep(3)
    pulsar("Exercises")
    time.sleep(3)


def en_primer_plano() -> bool:
    salida = movil.shell("dumpsys window | grep mCurrentFocus")
    return PKG in salida


def asegurar_biblioteca() -> None:
    """Vuelve a la lista de ejercicios si Hevy se ha cerrado.

    Paso en una tirada larga: la app se cerro a la mitad y el resto de
    busquedas se hicieron contra el lanzador, fallando una tras otra sin que
    nada lo detectara.
    """
    # No se busca "Search exercise": ese texto es la PISTA del campo y
    # desaparece en cuanto hay algo escrito, asi que la comprobacion fallaba
    # siempre despues del primer ejercicio y reiniciaba la app entera cada vez,
    # a 60 segundos por reinicio. Se mira que haya un campo de texto y el
    # titulo de la pantalla, que si se quedan.
    if en_primer_plano():
        nodos = dump()
        hay_campo = any(n["cls"] == "EditText" for n in nodos)
        hay_titulo = any(n["text"].strip().lower() == "exercises" for n in nodos)
        if hay_campo and hay_titulo:
            return
    log.info("   Hevy no esta donde deberia; vuelvo a la biblioteca")
    abrir_biblioteca()


def pulsar(texto: str, intentos: int = 6) -> None:
    for _ in range(intentos):
        n = find(dump(), texto)
        if n:
            tap(n)
            return
        time.sleep(1.2)
    raise Fallo(f"No encuentro {texto!r}")


def buscar(nombre: str) -> bool:
    """Escribe el nombre y abre la fila que coincide EXACTAMENTE.

    Lo de exacto no es quisquilloso: buscando "Lat Pulldown" salen tambien
    "(Cable)" y "- Close Grip", y grabar la que no es seria peor que no grabar.
    """
    campo = find(dump(), "Search exercise") or find(dump(), "Search")
    if not campo:
        raise Fallo("No veo el buscador de ejercicios")
    tap(campo)
    time.sleep(1)
    movil.shell("input keyevent KEYCODE_MOVE_END")
    for _ in range(60):
        movil.shell("input keyevent KEYCODE_DEL")
    # Se teclea solo hasta el primer parentesis: "input text" pasa por el
    # shell del movil y "(Machine)" lo rompia con "syntax error: unexpected
    # '('". Con "Lat Pulldown" sale la lista y luego se elige la fila exacta,
    # que es lo que de verdad importa.
    consulta = nombre.split("(")[0].strip() or nombre
    movil.shell(f"input text {shlex.quote(consulta.replace(' ', '%s'))}")

    # esperar a que la lista se repinte: buscar antes de tiempo daba
    # "no aparece" en ejercicios que si estaban
    objetivo = nombre.strip().lower()
    for _ in range(8):
        time.sleep(1.5)
        nodos = dump()
        fila = next((n for n in nodos
                     if n["text"].strip().lower() == objetivo and n["clickable"]), None)
        fila = fila or next((n for n in nodos
                             if n["text"].strip().lower() == objetivo), None)
        if fila:
            movil.shell(f"input tap {fila['xy'][0]} {fila['xy'][1]}")
            # La ficha tarda en pintar y la animacion en cargar. Grabar antes
            # daba 5 segundos de pantalla negra que parecian un fichero valido.
            for _ in range(10):
                time.sleep(1)
                if es_el_detalle(nombre):
                    time.sleep(3)      # que arranque la animacion
                    return True
            return False
    return False


def es_el_detalle(nombre: str) -> bool:
    """Estamos en la FICHA del ejercicio, no en la lista de resultados.

    Buscar solo el nombre no basta: tambien aparece en la fila de la lista, y
    con los ejercicios personalizados -- que Hevy no ilustra, solo pone las
    iniciales -- la ficha nunca se abria y se acababa grabando la propia lista
    de busqueda. Salia un video de un listado, y colaba: no esta en negro y
    cambia lo suficiente para no parecer congelado.

    Las pestanas Summary/History/How to solo existen en la ficha.
    """
    nodos = dump()
    if not any(n["text"].strip().lower() in ("summary", "history", "how to")
               for n in nodos):
        return False
    return any(n["text"].strip().lower() == nombre.strip().lower() for n in nodos)


# ------------------------------------------------------------------ grabar

def grabar(destino: Path) -> bool:
    """Graba la banda de la animacion.

    El toque previo no es un capricho: `uiautomator dump` CONGELA la animacion
    de la app, y no se recupera sola ni esperando diez segundos. Como el
    recorrido usa dumps para encontrar los botones, todo lo que se grababa
    despues salia estatico. Un toque en zona muerta -- el texto bajo la
    animacion, que no es pulsable -- la vuelve a arrancar.

    Fue la causa de 22 grabaciones tiradas a la basura antes de encontrarla.
    """
    movil.shell("input tap 540 1050")
    time.sleep(2.5)
    movil.preparar()
    movil.adb("shell", "screenrecord", "--time-limit", str(SEGUNDOS),
              "--size", "1080x2340", REMOTO, timeout=SEGUNDOS + 25)
    time.sleep(2)
    if not movil.existe(REMOTO):
        return False
    crudo = destino.with_suffix(".raw.mp4")
    movil.traer(REMOTO, crudo)
    movil.borrar(REMOTO)

    destino.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(crudo), "-vf", f"{RECORTE},scale={ANCHO}:-2",
         "-an", "-movflags", "+faststart", "-crf", "30", "-preset", "veryfast",
         str(destino)], capture_output=True, text=True)
    crudo.unlink(missing_ok=True)
    if r.returncode or not destino.exists():
        log.error("ffmpeg fallo: %s", r.stderr.strip()[-200:])
        return False
    return True


def es_buena(v: Path) -> bool:
    """Descarta las grabaciones en negro o congeladas.

    Pasa: si la animacion aun no habia cargado cuando empieza screenrecord,
    salen 5 segundos de pantalla negra, y el fichero se ve igual de valido que
    uno bueno. Tambien hay ejercicios que Hevy ilustra con una imagen fija.

    freezedetect caza los dos casos de una: una animacion de verdad nunca esta
    dos segundos sin cambiar. Un pelin mas fiable que mirar el tamano, aunque
    ahi tambien se nota (2 KB en negro contra 18 KB animada).
    """
    def detecta(filtro: str, marca: str) -> bool:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(v), "-vf", filtro,
                            "-f", "null", "-"], capture_output=True, text=True)
        return marca in r.stderr

    # Lo primero, contar fotogramas: una grabacion de una imagen fija se
    # comprime a UN solo fotograma, y ahi freezedetect no dispara -- necesita
    # dos segundos de contenido congelado y no hay con que compararlo. Cinco
    # se colaron por este hueco. Una buena trae ~150 en cinco segundos.
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames",
                        "-select_streams", "v", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", str(v)],
                       capture_output=True, text=True)
    n = int((r.stdout.strip() or "0").rstrip(","))
    if n < 30:
        log.warning("   solo %d fotogramas: es una imagen fija", n)
        return False

    # Lo primero, contar fotogramas: la grabacion de una imagen fija se
    # comprime a UN solo fotograma, y ahi freezedetect no dispara -- necesita
    # dos segundos de contenido congelado y no hay con que compararlo. Cinco
    # se colaron por este hueco. Una buena trae ~150 en cinco segundos.
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames",
                        "-select_streams", "v", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", str(v)],
                       capture_output=True, text=True)
    n = int((r.stdout.strip() or "0").rstrip(","))
    if n < 30:
        log.warning("   solo %d fotogramas: es una imagen fija", n)
        return False

    if detecta("blackdetect=d=0.3:pix_th=0.15", "black_start"):
        log.warning("   salio en negro")
        return False
    if detecta("freezedetect=n=0.003:d=2", "freeze_start"):
        log.warning("   sale congelada (imagen fija o no cargo)")
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("FITDAYS_ADB_HOST"))
    p.add_argument("--solo", nargs="*", help="solo estos ejercicios")
    p.add_argument("--rehacer", action="store_true", help="regrabar los que ya estan")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s",
                        datefmt="%H:%M:%S")

    nombres = args.solo or ejercicios_de_la_web()
    serial = conectar(args.host)
    os.environ["ANDROID_SERIAL"] = serial
    despertar(os.environ.get("FITDAYS_PIN"), exigir=False)

    abrir_biblioteca()
    hechos, fallidos = [], []
    for i, nombre in enumerate(nombres, 1):
        destino = DESTINO / f"{slug(nombre)}.mp4"
        if destino.exists() and not args.rehacer:
            log.info("[%d/%d] %s ya estaba", i, len(nombres), nombre)
            hechos.append(nombre)
            continue
        log.info("[%d/%d] %s", i, len(nombres), nombre)
        try:
            asegurar_biblioteca()
            if not buscar(nombre):
                log.warning("   no aparece en la biblioteca de Hevy")
                fallidos.append(nombre)
                continue
            if not es_el_detalle(nombre):
                log.warning("   no se abrio su ficha")
                fallidos.append(nombre)
                movil.shell("input keyevent KEYCODE_BACK")
                time.sleep(2)
                continue
            bien = False
            for intento in (1, 2):
                if grabar(destino) and es_buena(destino):
                    log.info("   %d KB", destino.stat().st_size // 1024)
                    bien = True
                    break
                destino.unlink(missing_ok=True)
                if intento == 1:
                    log.info("   reintento")
                    time.sleep(3)
            (hechos if bien else fallidos).append(nombre)
        except Exception as e:
            log.warning("   %s", e)
            fallidos.append(nombre)
        finally:
            movil.shell("input keyevent KEYCODE_BACK")
            time.sleep(2)

    movil.shell("input keyevent KEYCODE_HOME")
    log.info("Grabados %d · sin grabar %d", len(hechos), len(fallidos))
    if fallidos:
        log.info("Sin grabar: %s", ", ".join(fallidos))
    return 0 if hechos else 1


if __name__ == "__main__":
    sys.exit(main())
