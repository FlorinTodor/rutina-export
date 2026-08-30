#!/usr/bin/env python3
"""Ficheros en el movil: una sola carpeta y un borrado que no se puede pasar.

Regla de oro: TODO lo que el pipeline genera en el movil vive en CARPETA, y
solo se borra un fichero cuyo nombre este en LISTA_BLANCA. Cualquier otra
ruta hace saltar `Rechazado` antes de mandarle nada al movil.

No se usan comodines en el `rm`. El shell del movil nunca ve un `*`: se lista
la carpeta, se filtra aqui en Python y se borra fichero a fichero por su ruta
exacta. Un glob que no case con nada se expande a si mismo y `rm -f *.csv`
acabaria intentando borrar un fichero llamado literalmente "*.csv"; peor aun,
un glob mal escrito casa con lo que no debe. Por eso no hay globs.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("movil")

# /sdcard es un enlace a esto; se canonicaliza para que las dos formas de
# escribir la misma ruta no burlen la comprobacion de prefijo.
RAIZ = "/storage/emulated/0"
CARPETA = f"{RAIZ}/Documents/rutina"

# Solo estos nombres son borrables, y solo dentro de CARPETA.
LISTA_BLANCA = (
    re.compile(r"^health\.json$"),                          # la app de Health Connect
    re.compile(r"^FitdaysData_[\w .+-]+\.(?:csv|xls|xlsx)$", re.I),  # el export de FitDays
    re.compile(r"^ui\.xml$"),                               # volcado de pantalla de adb_ui
    re.compile(r"^rec\.mp4$"),                              # grabacion de pantalla temporal
)


class Rechazado(RuntimeError):
    """La ruta no cumple las condiciones para tocarla. No se ejecuta nada."""


def canonica(ruta: str) -> str:
    r = ruta.strip()
    if r.startswith("/sdcard/"):
        r = RAIZ + r[len("/sdcard"):]
    return r.rstrip("/")


def adb(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)


def shell(orden: str, timeout: int = 60) -> str:
    """Una orden ya montada y citada. adb pega los argumentos en una sola
    cadena y se la pasa a sh, asi que citar aqui es obligatorio."""
    return adb("shell", orden, timeout=timeout).stdout


def comprobar(ruta: str) -> str:
    """Devuelve la ruta canonica si es borrable; si no, revienta.

    Tres condiciones, todas necesarias:
      1. cuelga directamente de CARPETA (ni subcarpetas ni ..)
      2. el nombre esta en la lista blanca
      3. no lleva comodines ni nada que el shell pueda expandir
    """
    r = canonica(ruta)
    prefijo = CARPETA + "/"
    if not r.startswith(prefijo):
        raise Rechazado(f"{r} esta fuera de {CARPETA}")

    nombre = r[len(prefijo):]
    if "/" in nombre or nombre in ("", ".", ".."):
        raise Rechazado(f"{r} no es un fichero directo de {CARPETA}")
    if any(c in nombre for c in "*?[]{}$`\\\n"):
        raise Rechazado(f"{nombre!r} tiene caracteres que el shell expandiria")
    if not any(p.match(nombre) for p in LISTA_BLANCA):
        raise Rechazado(f"{nombre!r} no esta en la lista de ficheros del pipeline")
    return r


def preparar() -> None:
    shell(f"mkdir -p {shlex.quote(CARPETA)}")


def listar() -> list[str]:
    """Ficheros del pipeline que hay ahora mismo en la carpeta.

    Se lista sin comodines y se filtra en Python: lo que no reconozcamos ni
    se nombra. Si dejaste algo tuyo ahi dentro, para este modulo no existe.
    """
    salida = shell(f"ls -1 {shlex.quote(CARPETA)} 2>/dev/null")
    out = []
    for linea in salida.splitlines():
        nombre = linea.strip()
        if not nombre:
            continue
        try:
            out.append(comprobar(f"{CARPETA}/{nombre}"))
        except Rechazado:
            log.debug("Ignoro %r: no es del pipeline", nombre)
    return out


def existe(ruta: str) -> bool:
    r = canonica(ruta)
    return shell(f"test -f {shlex.quote(r)} && echo si").strip() == "si"


def traer(remoto: str, destino: Path) -> Path:
    """adb pull. No borra nada: el borrado siempre se pide aparte."""
    r = canonica(remoto)
    destino.parent.mkdir(parents=True, exist_ok=True)
    p = adb("pull", r, str(destino), timeout=180)
    if p.returncode or not destino.exists():
        raise RuntimeError(f"No pude traerme {r}: {p.stderr.strip()[:200]}")
    return destino


def mover(origen: str, nombre: str | None = None) -> str:
    """Mete un fichero en CARPETA sin sobrescribir nada (`mv -n`).

    Mover no destruye, asi que aqui solo se exige que el DESTINO sea valido.
    Sirve para el export de FitDays, que la app deja donde quiere.
    """
    o = canonica(origen)
    destino = comprobar(f"{CARPETA}/{nombre or Path(o).name}")
    preparar()
    shell(f"mv -n {shlex.quote(o)} {shlex.quote(destino)}")
    if not existe(destino):
        raise RuntimeError(f"No pude mover {o} a {destino}")
    return destino


def borrar(ruta: str) -> bool:
    """Borra UN fichero, por su ruta exacta, si pasa `comprobar`."""
    r = comprobar(ruta)
    if not existe(r):
        log.debug("Ya no estaba: %s", r)
        return False
    shell(f"rm -f {shlex.quote(r)}")
    if existe(r):
        log.warning("No se ha podido borrar %s", r)
        return False
    log.info("Borrado del movil: %s", r)
    return True


# --------------------------------------------------------------- conexion
# Vive aqui y no en cada script porque los dos puentes (FitDays y la app de
# Health Connect) necesitan exactamente lo mismo: encontrar el movil y
# despertarlo.


class Fallo(RuntimeError):
    pass


def conectados() -> list[str]:
    salida = adb("devices").stdout
    return [l.split()[0] for l in salida.splitlines()[1:]
            if l.strip() and l.split()[-1] == "device"]


def conectar(host: str | None) -> str:
    """Devuelve el serial del movil, reconectando por wifi si hace falta."""
    if host:
        if host not in conectados():
            adb("connect", host)
            time.sleep(2)
        if host in conectados():
            return host
        log.warning("No conecto con %s; pruebo por USB", host)

    # la IP del movil puede cambiar: se busca por mDNS antes de rendirse
    for linea in adb("mdns", "services").stdout.splitlines():
        m = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)", linea)
        if m and "adb-tls-connect" not in linea:
            adb("connect", m.group(1))
            time.sleep(2)
            if m.group(1) in conectados():
                log.info("Encontrado por mDNS: %s", m.group(1))
                return m.group(1)

    disponibles = conectados()
    if not disponibles:
        raise Fallo("No hay ningun movil accesible por ADB. "
                    "Conectalo por cable o revisa que este en la wifi.")
    return disponibles[0]


def bloqueado() -> bool:
    m = re.search(r"mDreamingLockscreen=(\w+)", shell("dumpsys window"))
    return bool(m and m.group(1) == "true")


def despertar(pin: str | None, exigir: bool = True) -> None:
    """Enciende la pantalla y desbloquea si se puede.

    `exigir=False` para quien no necesita manejar la interfaz: la app de
    Health Connect se declara `showWhenLocked`, asi que arranca por encima de
    la pantalla de bloqueo y lee igual. Solo FitDays necesita el movil abierto,
    porque le va pulsando botones.
    """
    shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(1)
    if not bloqueado():
        return
    shell("input swipe 540 1800 540 700")
    time.sleep(1.2)
    if bloqueado() and pin:
        shell(f"input text {shlex.quote(pin)}")
        shell("input keyevent KEYCODE_ENTER")
        time.sleep(1.5)
    if bloqueado():
        if not exigir:
            log.info("El movil sigue bloqueado; da igual, la app arranca encima")
            return
        raise Fallo("El movil esta bloqueado. Desbloquealo, o define "
                    "FITDAYS_PIN si aceptas guardar el PIN en el entorno.")


# ------------------------------------------------------------- publicacion
# Los dos puentes suben al mismo repositorio y disparan el mismo workflow.
# Estaba duplicado en cada script; aqui se hace una vez.


def publicar(ficheros: list[str], mensaje: str, root: Path) -> bool:
    """Commit y push de los JSONL que hayan cambiado. Devuelve si subio algo."""
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a],
                              capture_output=True, text=True)

    if not git("diff", "--quiet", "--", *ficheros).returncode:
        log.info("Sin cambios en %s", ", ".join(Path(f).name for f in ficheros))
        return False
    git("add", *ficheros)
    git("-c", "user.name=rutina-local", "-c", "user.email=actions@github.com",
        "commit", "-m", mensaje)
    r = git("push", "origin", "main")
    if r.returncode:
        log.error("No pude subirlo: %s", r.stderr.strip()[:200])
        return False
    log.info("Subido al repositorio")
    return True


def disparar_nube(repo: str | None = None) -> bool:
    """Lanza el workflow desde aqui.

    El cron de GitHub se retrasa y a veces se salta ejecuciones; ya fallo una
    vez. Este temporizador si dispara puntual, asi que sirve de respaldo. Si
    ademas el cron acaba funcionando no pasa nada: la sincronizacion es
    idempotente y la segunda pasada no escribe.
    """
    if not shutil.which("gh"):
        log.info("Sin 'gh' instalado: no disparo el workflow")
        return False
    if repo is None:                      # el del remoto de git, no uno fijo
        r = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner",
                            "-q", ".nameWithOwner"], capture_output=True, text=True)
        repo = r.stdout.strip()
        if not repo:
            log.info("No se de que repositorio se trata: no disparo el workflow")
            return False
    r = subprocess.run(["gh", "workflow", "run", "sync.yml", "--repo", repo],
                       capture_output=True, text=True)
    if r.returncode:
        log.warning("No pude lanzar el workflow: %s", r.stderr.strip()[:160])
        return False
    log.info("Workflow lanzado en GitHub")
    return True


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "borrar":
        for f in listar():
            borrar(f)
    else:
        print(f"Carpeta del pipeline: {CARPETA}")
        for f in listar() or ["(vacia)"]:
            print("  ", f)
        print("\nPara vaciarla:  python scripts/movil.py borrar")
