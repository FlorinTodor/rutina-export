# rutina-export

> 🇬🇧 [Read this in English](README.md)
>
> **[Demo en vivo](https://florintodor.dev/demo/rutina-export/)** (datos inventados) · **[Ficha del proyecto](https://florintodor.dev/proyectos/rutina-export/)**

**Saca tus datos de Health Connect, Samsung Health, FitDays y Hevy a Notion y a
un dashboard web, solo, cada día, sin pagar suscripciones.**

Las apps que exportan Health Connect automáticamente cobran por ello. Esta no,
y no por generosidad: el automatismo que cobran es el permiso
`READ_HEALTH_DATA_IN_BACKGROUND`, que **es gratis**. Lo que lo bloquea es la
revisión de Google Play para apps de salud — y como esta app se instala por
ADB y no se publica, no hay revisión que pasar.

```
                 ┌─ Samsung Health ─┐
   reloj / móvil ─┤                  ├─► Health Connect ─┐
                 └─ Hevy ───────────┘                   │
                                                        ├─► la app de android/
   FitDays (báscula) ─► Health Connect ─────────────────┘         │
                     └─ su export propio (15 métricas) ───────────┤
                                                                  ▼
   cinta métrica ────► se teclea en la app ──────────► data/inbox/ en tu repo
                                                                  │
   Hevy (API) ──────────────────────────────────────► workflow ───┤
                                                                  ▼
                                            Notion + dashboard + GitHub Pages
```

## Qué recoge, y de dónde

| fuente | qué saca | cómo |
|---|---|---|
| **Health Connect** | pasos, distancia, calorías, sueño con fases, pulso, HRV, SpO2 | app propia, en segundo plano |
| **Samsung Health** | es quien alimenta lo anterior desde el reloj | vía Health Connect |
| **FitDays** | las **15** métricas de la báscula: grasa visceral y subcutánea, músculo esquelético, proteína, edad corporal… | su export, manejando su interfaz |
| **Hevy** | entrenos, series, repeticiones, pesos, RPE, récords, 1RM estimado | su API oficial |
| **cinta métrica** | pecho, abdomen, cintura, cadera, brazos, muslos, gemelos | a mano, en la app |
| **Notion** | cinco bases relacionadas: Días, Entrenos, Series, Ejercicios, Medidas | API oficial |

## Por qué hace falta una app propia

**Samsung Health no tiene API en la nube.** Nada fuera del móvil puede leer tus
pasos ni tu sueño. Y **Health Connect es una base de datos local**: solo la
puede leer una app instalada en el teléfono. No hay atajo por servidor.

**FitDays solo escribe 4 de sus 15 métricas en Health Connect.** Las otras
—visceral, subcutánea, esquelético, proteína, edad corporal— no salen de su
app, así que hay que sacarlas de su propio export.

**Hevy sí tiene API**, y por eso los entrenos los trae el workflow sin tocar el
móvil.

## Qué necesitas

- Un móvil Android 10 o superior con Health Connect.
- Un ordenador con `adb` para instalar la app **una vez**. Después el móvil va
  solo, con datos móviles y la pantalla bloqueada.
- Cuentas de Hevy (con API key, requiere Hevy Pro) y Notion. Las dos opcionales:
  sin Hevy tendrás solo salud, sin Notion tendrás solo el dashboard.
- Un repositorio de GitHub, que hace de almacén y de servidor.

## Instalación

### 1. La app del móvil

```bash
android/build.sh                                  # compila el APK
python scripts/health_pull.py --instalar          # lo instala por ADB
python scripts/health_pull.py --permisos          # concede los permisos
```

Hace falta un JDK **con `javac`** (el `java` de muchas distribuciones trae solo
el runtime). `build.sh` busca uno solo.

### 2. El token

En GitHub: Settings → Developer settings → **Fine-grained tokens**.

- Repository access: **Only select repositories** → el tuyo
- Permissions → Repository permissions → **Contents: Read and write**. Nada más.

Créalo **desde el navegador del móvil** y pégalo en la app. Así no tiene que
viajar de un aparato a otro, que es donde se filtran.

En la app, pon también tu repositorio (`usuario/repositorio`) y dale a
*Guardar*. La pantalla es una lista de comprobación: lo que falte sale arriba
en rojo con su botón.

### 3. FitDays (opcional)

Si usas su báscula y quieres las 15 métricas, activa el servicio de
accesibilidad desde la app y dale acceso a la carpeta `Documents`. Hace el
recorrido por su interfaz él solo.

> El servicio está limitado a FitDays en `res/xml/accesibilidad.xml`. No ve
> ninguna otra app.

### 4. El resto

```bash
cp config.example.toml config.toml     # pon aquí tus claves de Hevy y Notion
pip install -r requirements.txt
python scripts/configurar.py           # el resto lo hace él
```

`configurar.py` crea las cinco bases de Notion, sube los ocho secretos al
repositorio, pone las variables y enciende Pages. Es idempotente: se puede
repetir sin miedo, y con `--dry-run` dice qué haría sin tocar nada.

Sin él habría que copiar ocho identificadores a mano de `config.toml` a la
pantalla de Secrets de GitHub, que es la parte más tediosa de montar esto y
donde más se equivoca uno.

## Cómo funciona una vez montado

| cuándo | qué pasa |
|---|---|
| 20:45 | la app lee Health Connect y sube a `data/inbox/` |
| al desbloquear el móvil | si toca, exporta FitDays y lo sube |
| ese push | dispara el workflow al instante |
| 21:00 | el cron, como red de seguridad |

El workflow importa lo que haya en el inbox, trae los entrenos de Hevy, escribe
en Notion y republica el dashboard en GitHub Pages.

**No hace falta el ordenador.** Los scripts de `scripts/` siguen ahí por si lo
prefieres, pero el móvil se basta.

## Detalles que costaron encontrar

Están documentados donde ocurren, pero estos merecen aviso:

- **El export de Health Connect no acumula.** La ventana son unos días, así que
  el histórico vive en `data/raw/*.jsonl` y lo que llega pisa lo de esa fecha.
  Un día medido a medias se corrige solo en la siguiente pasada.
- **Manejar la interfaz de otra app exige la pantalla desbloqueada.** No se
  puede saltar. Da igual: el export de FitDays trae el histórico completo.
- **Los ejercicios se agrupan por su id, nunca por nombre**: Hevy congela el
  nombre traducido de cada entreno, y cambiar el idioma parte el historial.
- **Las calorías de Hevy ya están en Health Connect**, porque Hevy escribe ahí.
  Sumarlas aparte sería contarlas dos veces.
- **`uiautomator dump` congela las animaciones de la app que estés mirando**, y
  no se recupera sola. Si automatizas interfaces, tenlo en cuenta.

## Privacidad

Tus datos van de tu móvil a **tu** repositorio. No hay servidor intermedio ni
cuenta de nadie más. Si lo pones privado, GitHub Pages sobre repositorio
privado requiere Pro (gratis con el Student Pack).

El token vive en `SharedPreferences` privadas del móvil; en un teléfono sin
root ninguna otra app puede leerlas.

## Pruebas

```bash
python tests/test_history.py        # el histórico sobrevive a una ventana corta
python tests/test_sync_offline.py   # la sincronización no duplica en Notion
python tests/test_movil.py          # el borrado en el móvil no se pasa
npm install jsdom && node tests/test_dashboard.mjs
```

Se ejecutan en cada push junto con la compilación del APK.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Las animaciones de ejercicios que muestra el dashboard son de Hevy y de
[ExerciseGymGifsDB](https://github.com/JahelCuadrado/ExerciseGymGifsDB); ni unas
ni otras se distribuyen aquí.
