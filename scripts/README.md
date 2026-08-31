# scripts/

## adb_ui.py

Controla el móvil por ADB para sacar el export de FitDays, que es la única vía
para las 15 métricas de la báscula (Health Connect solo deja pasar 5).

Pulsa los botones **buscándolos por su texto**, no por coordenadas fijas: se
vuelca la jerarquía de la interfaz con `uiautomator` y se toca el centro del
nodo que coincide. Así sobrevive a cambios de diseño y de resolución.

### Preparación, una sola vez

En el móvil: Ajustes → Opciones de desarrollador → Depuración USB. Conectar por
cable, aceptar el aviso, y pasar a wifi para no depender del cable:

```bash
adb tcpip 5555
adb connect <ip-del-movil>:5555
```

### Uso

```bash
export ANDROID_SERIAL=<ip>:5555
python scripts/adb_ui.py dump              # ver la pantalla actual
python scripts/adb_ui.py tap "Exportar"    # pulsar por texto
```

### El recorrido del export en FitDays 1.28

```
Tablas → Datos del usuario → menú ⋮ → Exportar → Todas → icono de compartir
      → Guardar en local
```

El fichero aparece en `/storage/emulated/0/Documents/FitdaysData_<epoch>.csv`,
y pese a la extensión es un Excel antiguo OLE2. Se trae con `adb pull` y se
importa con `python -m rutina import-fitdays <fichero>`.

La primera vez la app muestra un aviso de ayuda que se come el primer toque:
hay que descartarlo tocando en cualquier otro sitio.

## Los ficheros que dejamos en el móvil

Todo lo que el pipeline genera en el teléfono vive en **una sola carpeta**:

```
/sdcard/Documents/rutina/
    health.json                  ← lo escribe la app de Health Connect
    FitdaysData_<epoch>.csv      ← el export de FitDays, recogido ahí
    ui.xml                       ← volcado de pantalla de adb_ui
```

`movil.py` es el único sitio del repositorio que borra algo del teléfono, y
para hacerlo exige **tres condiciones a la vez**:

1. la ruta cuelga directamente de esa carpeta (ni subcarpetas, ni `..`),
2. el nombre coincide con la lista blanca de arriba,
3. no lleva ningún carácter que el shell pudiera expandir.

Si algo no cumple las tres, salta `Rechazado` y **no se ejecuta nada** en el
móvil. No hay ni un comodín en ningún `rm`: se lista la carpeta, se filtra en
Python y se borra fichero a fichero por su ruta exacta.

Además, **el borrado ocurre al final**, cuando el import ya ha funcionado y el
dato está guardado en el repositorio. Si el importador falla, el fichero se
queda en el móvil para poder reintentar.

```bash
python scripts/movil.py             # ver qué hay en la carpeta
python scripts/movil.py borrar      # vaciarla a mano
```

Con `--conservar`, cualquiera de los dos puentes deja el fichero en el móvil
para que lo borres tú.

## health_pull.py — Health Connect sin suscripción

Reemplaza a la app *Health Data Export* y a la Google Sheet: pasos, sueño,
constantes vitales y composición corporal salen directos de Health Connect a
través de una app propia (`android/`), instalada por ADB.

```bash
export FITDAYS_ADB_HOST=192.168.1.50:5555
python scripts/health_pull.py --dry-run     # enseña las diferencias, no escribe
python scripts/health_pull.py               # importa, sube y borra del móvil
```

### Por qué esto no necesita pagar nada

Lo que cobra *Health Data Export* es que la exportación se dispare sola, y eso
en Android 15 exige el permiso `READ_HEALTH_DATA_IN_BACKGROUND`. Aquí no hace
falta: **la app no decide cuándo ejecutarse**. La despierta el PC a las 20:50,
lee en primer plano y termina. El permiso caro nunca entra en juego.

Publicarla en Play Store sí obligaría a pasar el formulario de aprobación de
apps de salud de Google. Como se instala con `adb install`, no hay revisión.

### Preparación, una sola vez

```bash
android/build.sh                                  # compila el APK
python scripts/health_pull.py --instalar          # lo instala
python scripts/health_pull.py --permisos          # concede los 18 permisos
```

Si `--permisos` no puede con alguno, se conceden a mano en
Ajustes → Seguridad y privacidad → Health Connect → Rutina Export.

### Qué mejora respecto a la Google Sheet

- Los totales diarios los pide con `aggregateGroupByPeriod`, así que Health
  Connect ya **deduplica por su lista de prioridad**. Desaparece el apaño de
  `health_sheets.py` de tomar el máximo por fecha porque la hoja repetía el
  total en cada fila de sesión.
- Desaparecen la `service_account.json`, la hoja de cálculo y el bug de la app
  que escribía `2026-08-29 14:24:49=3.90` en lugar de `3.90`.
- La app entrega el sueño **crudo**, con inicio y fin de cada sesión. A qué día
  pertenece una noche se decide en `sources/health_app.py` (`--sueno-por`), sin
  recompilar ni reinstalar nada.

## pull_movil.py — el único comando

Los dos puentes de una vez. Es lo que ejecuta el temporizador, y lo único que
hace falta lanzar a mano si algún día quieres forzarlo:

```bash
python scripts/pull_movil.py                 # todo: traer, comprobar, subir
python scripts/pull_movil.py --solo health   # solo uno de los dos
python scripts/pull_movil.py --conservar     # sin borrar nada del móvil
```

Antes eran dos unidades de systemd separadas, cinco minutos aparte. Se
peleaban por el ADB, hacían dos commits por el mismo dato y disparaban el
workflow dos veces. Ahora se conecta una vez, se ejecutan los dos puentes y se
publica **un commit** y **un disparo**.

**Los dos son independientes**: si uno falla, el otro se ejecuta igual. No es
un caso raro, es lo normal. Health Connect va primero porque funciona con el
móvil bloqueado; FitDays necesita la pantalla abierta porque le pulsa botones.

### La comprobación antes de subir

Que un fichero haya cambiado no significa que el dato sea bueno. Antes de
publicar se verifica, y solo hay **un motivo que aborta la subida**: que el
histórico haya **menguado**. Eso sería pérdida de datos, y subirlo la haría
permanente.

```
── Comprobacion antes de subir ──
   ok     health_daily.jsonl: 32 filas (+1), hasta 2026-08-30
   ok     body.jsonl: 192 filas (+1), hasta 2026-08-30
   ok     hoy 2026-08-30: 742 pasos, 6.65 h de sueno
```

Lo demás son avisos y no bloquean nada, porque tienen explicaciones normales:
un día sin pesarte, o el móvil apagado a las 20:45. Si algo aborta, los datos
se quedan en `data/raw/` para que los mires; no se pierde nada.

## hevy_grabar.py — las animaciones, grabadas de la propia app

El dashboard enseña una animación por ejercicio. Conseguirlas costó tres
intentos, y los dos primeros eran adivinanzas:

1. **Repositorio de GIFs de GitHub, emparejado por nombre.** Aproximado: salían
   ejercicios parecidos pero con otro aparato (un curl con goma donde iba uno
   con mancuerna).
2. **El CDN de Hevy.** Sus animaciones están en un CloudFront público y la
   lista de ficheros va dentro del APK (`hevy_assets.py`). Mejor, pero solo se
   puede extraer la mitad del catálogo, así que para muchos ejercicios el
   correcto no estaba y el emparejador cogía el menos malo: "Iso-Lateral Row"
   acababa en una elevación lateral.
3. **Grabar la pantalla.** Sin adivinar nada: es literalmente lo que la app
   pinta para ese ejercicio.

```bash
python scripts/hevy_grabar.py                      # todos los del dashboard
python scripts/hevy_grabar.py --solo "Pull Over"   # uno suelto
python scripts/hevy_grabar.py --rehacer            # regrabar los que ya están
```

El recorrido, todo por ADB: `Perfil → Exercises → buscar → abrir → grabar 5 s
→ atrás`. Después se recorta la banda de la animación, se escala a 420 px y se
guarda en `docs/media/hevy/`, que es lo que sirve GitHub Pages. Unos 18 KB por
ejercicio.

Tarda ~30 s por ejercicio y solo hay que rehacerlo cuando aparezca uno nuevo.

### Detalles que costaron

- **Los paréntesis rompen la búsqueda.** `input text` pasa por el shell del
  móvil y `(Machine)` da `syntax error: unexpected '('`. Se teclea solo hasta
  el paréntesis y se elige después la fila con el nombre exacto, que además
  evita confundir "Lat Pulldown (Machine)" con "(Cable)".
- **Hay que esperar a que la lista se repinte**, o da "no aparece" en
  ejercicios que sí están.
- **El recorte empieza 70 px más abajo** para dejar fuera el botón de pausa
  que la app dibuja sobre la animación.
- **No se abre un entreno vacío para llegar a la biblioteca**: eso crearía
  datos falsos en la cuenta. Se entra por Perfil → Exercises.

### Orden de preferencia en el dashboard

1. lo grabado de la app (`docs/media/hevy/`)
2. la animación del CDN emparejada por nombre
3. el GIF aproximado del repositorio de GitHub

## fitdays_pull.py — automatizado

Hace el recorrido entero solo: abre la app, exporta, se trae el fichero, lo
importa y sube el histórico al repositorio.

```bash
export FITDAYS_ADB_HOST=192.168.1.50:5555
python scripts/fitdays_pull.py
```

### Por qué no corre en GitHub Actions

Porque la nube no puede hablar con tu móvil. El reparto es:

```
20:45  tu PC    → ADB → Health Connect + FitDays → comprobar → un push
21:00  la nube  → Hevy + lo que el PC subió → Notion + dashboard + Pages
```

El PC va primero para que el workflow encuentre los datos ya puestos.

### Instalación del temporizador

```bash
cp systemd/rutina-movil.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rutina-movil.timer
systemctl --user list-timers rutina-movil.timer
journalctl --user -u rutina-movil -f        # ver qué hace
```

`Persistent=true`: si el PC estaba apagado a las 20:45, se ejecuta al encenderlo.

La unidad da por hecho que el repositorio esta en `~/rutina` y que el venv
es `~/rutina/.venv`; si lo tienes en otro sitio, cambia `WorkingDirectory` y
`ExecStart`. `FITDAYS_ADB_HOST` se puede borrar de la unidad: sin ella el
movil se busca por mDNS y, si no aparece, se prueba por USB.

### Cuándo no funciona, y por qué da igual

- **PC apagado** o **móvil fuera de casa**: se salta ese día. No importa: el
  export incluye siempre *todo* el histórico, así que la siguiente ejecución
  recupera lo que falte.
- **Móvil bloqueado**: el script intenta despertarlo y deslizar, pero no puede
  pasar un PIN. Se puede definir `FITDAYS_PIN`, aunque eso significa guardar el
  PIN del teléfono en el entorno: valóralo tú.
- **La IP del móvil cambia**: se busca por mDNS y, si no, se prueba por USB.
- **ADB por wifi se pierde al reiniciar el teléfono**: hay que reconectar una
  vez con el cable y `adb tcpip 5555`.

### Respaldo del cron

Al terminar, el script lanza también el workflow de GitHub con
`gh workflow run`. El cron de GitHub se retrasa y a veces se salta
ejecuciones —ya falló una vez—, mientras que este temporizador dispara
puntual. Si el cron acaba funcionando no molesta: la sincronización es
idempotente y la segunda pasada no escribe nada.

Se desactiva con `--no-trigger`.

