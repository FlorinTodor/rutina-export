"""Lo que nunca debe pasar: que el pipeline borre algo del movil que no sea suyo.

`movil.comprobar()` es la unica puerta por la que pasa un borrado. Aqui se le
lanza todo lo que se me ocurre que podria colarse: rutas fuera de la carpeta,
escapes con "..", comodines que el shell expandiria, ficheros personales con
nombre parecido y nombres con espacios.

No toca ningun movil: solo valida rutas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import movil

DEBE_RECHAZAR = [
    # fuera de la carpeta del pipeline
    "/sdcard/DCIM/Camera/IMG_20260830.jpg",
    "/sdcard/Documents/ringtone",
    "/sdcard/Documents/tesis.pdf",
    "/sdcard/Download/factura.pdf",
    "/storage/emulated/0/Documents/FitdaysData_1.csv",   # suelto, sin recoger
    "/sdcard/",
    "/",
    # escapandose de la carpeta
    "/sdcard/Documents/rutina/../../DCIM/foto.jpg",
    "/sdcard/Documents/rutina/..",
    "/sdcard/Documents/rutina/sub/health.json",
    # comodines: el shell los expandiria a otros ficheros
    "/sdcard/Documents/rutina/*",
    "/sdcard/Documents/rutina/*.csv",
    "/sdcard/Documents/rutina/health.jso?",
    "/sdcard/Documents/rutina/$(rm -rf algo)",
    "/sdcard/Documents/rutina/`ls`",
    # dentro de la carpeta, pero no es nuestro
    "/sdcard/Documents/rutina/apuntes.txt",
    "/sdcard/Documents/rutina/health.json.bak",
    "/sdcard/Documents/rutina/salud.json",
    # una carpeta con prefijo parecido no es la nuestra
    "/sdcard/Documents/rutina2/health.json",
    "/sdcard/Documents/rutinaX/health.json",
]

DEBE_ACEPTAR = [
    "/sdcard/Documents/rutina/health.json",
    "/storage/emulated/0/Documents/rutina/health.json",   # la misma, sin enlace
    "/sdcard/Documents/rutina/FitdaysData_1756500000.csv",
    "/sdcard/Documents/rutina/FitdaysData_2026-08-30.xlsx",
    "/sdcard/Documents/rutina/ui.xml",
]


def main():
    fallos = 0

    for ruta in DEBE_RECHAZAR:
        try:
            movil.comprobar(ruta)
            print(f"  FALLO · deberia rechazar: {ruta}")
            fallos += 1
        except movil.Rechazado:
            pass
    print(f"{len(DEBE_RECHAZAR)} rutas peligrosas, todas rechazadas"
          if not fallos else f"{fallos} rutas peligrosas se colaron")

    for ruta in DEBE_ACEPTAR:
        try:
            r = movil.comprobar(ruta)
            assert r.startswith(movil.CARPETA + "/"), r
        except (movil.Rechazado, AssertionError) as e:
            print(f"  FALLO · deberia aceptar {ruta}: {e}")
            fallos += 1
    print(f"{len(DEBE_ACEPTAR)} rutas legitimas, todas aceptadas")

    # las dos formas de escribir la misma ruta llevan al mismo sitio: si no,
    # bastaria con escribir /sdcard/ en vez de /storage/emulated/0/ para
    # saltarse la comprobacion de prefijo
    a = movil.comprobar("/sdcard/Documents/rutina/health.json")
    b = movil.comprobar("/storage/emulated/0/Documents/rutina/health.json")
    if a != b:
        print(f"  FALLO · /sdcard y /storage/emulated/0 no se unifican: {a} != {b}")
        fallos += 1
    else:
        print("/sdcard y /storage/emulated/0 se canonicalizan igual")

    # borrar() nunca debe llegar a mandar una orden si la ruta no pasa
    ordenes = []
    movil.shell = lambda orden, timeout=60: ordenes.append(orden) or ""
    for ruta in DEBE_RECHAZAR:
        try:
            movil.borrar(ruta)
            print(f"  FALLO · borrar() no reviento con {ruta}")
            fallos += 1
        except movil.Rechazado:
            pass
    if ordenes:
        print(f"  FALLO · se mandaron {len(ordenes)} ordenes al movil: {ordenes[:3]}")
        fallos += 1
    else:
        print("borrar() no manda NADA al movil cuando la ruta no pasa")

    print("\n" + ("OK · el guardia aguanta" if not fallos else f"{fallos} FALLOS"))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
