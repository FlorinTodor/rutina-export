#!/usr/bin/env python3
"""Lee la pantalla del movil por ADB y pulsa botones POR SU TEXTO.

Dar toques en coordenadas fijas se rompe con cualquier cambio de diseno o de
resolucion. Aqui se vuelca la jerarquia de la interfaz con uiautomator, se
busca el nodo por su texto o descripcion y se pulsa en su centro.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def sh(*args: str, timeout: int = 60) -> str:
    r = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout


# El volcado se deja en la carpeta del pipeline, no suelto en la raiz de la
# tarjeta, para que todo lo que generamos en el movil este en un solo sitio.
UI_XML = "/sdcard/Documents/rutina/ui.xml"


def dump() -> list[dict]:
    """Devuelve los nodos de la pantalla actual."""
    sh("shell", "mkdir", "-p", UI_XML.rsplit("/", 1)[0])
    for _ in range(3):
        sh("shell", "uiautomator", "dump", UI_XML)
        xml = sh("shell", "cat", UI_XML)
        if "<hierarchy" in xml:
            break
        time.sleep(0.6)
    else:
        return []

    nodes = []
    for n in ET.fromstring(xml[xml.index("<hierarchy"):]).iter("node"):
        b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        nodes.append({
            "text": (n.get("text") or "").strip(),
            "desc": (n.get("content-desc") or "").strip(),
            "id": (n.get("resource-id") or "").split("/")[-1],
            "cls": (n.get("class") or "").split(".")[-1],
            "clickable": n.get("clickable") == "true",
            "box": (x1, y1, x2, y2),
            "xy": ((x1 + x2) // 2, (y1 + y2) // 2),
        })
    return nodes


def show(nodes: list[dict], only_clickable: bool = False) -> None:
    for n in nodes:
        if only_clickable and not n["clickable"]:
            continue
        label = n["text"] or n["desc"]
        if not label and not n["id"]:
            continue
        mark = "*" if n["clickable"] else " "
        print(f" {mark} {n['cls']:<14} {label[:40]:<40} id={n['id'][:22]:<22} {n['xy']}")


def find(nodes: list[dict], needle: str, exact: bool = False) -> dict | None:
    t = needle.lower()
    for n in nodes:
        for field in (n["text"], n["desc"], n["id"]):
            f = field.lower()
            if (f == t) if exact else (t in f and f):
                return n
    return None


def tap(node_or_xy) -> None:
    x, y = node_or_xy["xy"] if isinstance(node_or_xy, dict) else node_or_xy
    sh("shell", "input", "tap", str(x), str(y))
    time.sleep(1.2)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "dump":
        show(dump(), only_clickable="--clickable" in sys.argv)
    elif cmd == "tap":
        ns = dump()
        n = find(ns, sys.argv[2])
        if not n:
            print(f"No encuentro {sys.argv[2]!r}"); sys.exit(1)
        print(f"pulsando {n['text'] or n['desc']!r} en {n['xy']}")
        tap(n)
