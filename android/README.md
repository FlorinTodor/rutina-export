# Rutina Export

App Android que lee Health Connect y exporta FitDays, y lo sube a GitHub ella
sola. Sustituye a *Health Data Export* (0,99 € por que la exportación sea
automática) y a la Google Sheet que hacía de intermediaria.

```bash
./build.sh                                    # compila el APK
python ../scripts/health_pull.py --instalar   # lo instala por ADB
```

## Por qué es gratis

Lo que cobran las apps del ramo es que la exportación se dispare sola, y en
Android 15 eso es el permiso `READ_HEALTH_DATA_IN_BACKGROUND`. **Ese permiso
no cuesta dinero**: lo que lo bloquea es la revisión de Google Play para apps
de salud. Como esta app se instala con `adb install` y no se publica, no hay
revisión que pasar. Está declarado en el manifiesto y concedido.

## Las dos mitades, y por qué una es más fácil

| | cómo | ¿con el móvil bloqueado? |
|---|---|---|
| **Health Connect** | lectura en segundo plano desde `TrabajoDiario` | **sí** |
| **FitDays** | `FitdaysServicio` pulsa los botones de su interfaz | **no** |

Manejar la interfaz de otra app exige la pantalla desbloqueada: Android no
pinta una actividad ajena sobre el bloqueo, ni por accesibilidad ni por ADB.
No hay forma de saltárselo.

Importa menos de lo que parece, porque **el export de FitDays trae siempre el
histórico completo**. Saltarse días no pierde nada, solo retrasa. Por eso el
día se marca como pendiente y se aprovecha el primer desbloqueo que haya
(`ACTION_USER_PRESENT`). Lo que sí necesita puntualidad diaria es Health
Connect, que es justo la mitad que funciona bloqueada.

## Preparación, una sola vez

1. **Instalar**: `./build.sh && python ../scripts/health_pull.py --instalar`
2. **Permisos**: `python ../scripts/health_pull.py --permisos`, o abrir la app
   y concederlos en la pantalla de Health Connect.
3. **Token**: crear en GitHub un *fine-grained personal access token*
   limitado a **este repositorio** y con **Contents: Read and write** y nada
   más. Pegarlo en la app y pulsar *Guardar token*.
4. **FitDays** (opcional): pulsar *Activar el servicio para FitDays* y
   activarlo en la lista de Accesibilidad.

El token vive en `SharedPreferences` privadas: en un móvil sin root ninguna
otra app puede leerlas. Con ese alcance, lo peor que puede hacer quien lo
robe es escribir en el repositorio de tu rutina.

## Cómo llega el dato al dashboard

```
20:45  la app          → Health Connect → data/inbox/health.json  (API de GitHub)
       al desbloquear  → FitDays        → data/inbox/fitdays.csv
21:00  el workflow     → importa el inbox, lo borra, y regenera Notion y Pages
```

El móvil **no decide nada** sobre los datos: entrega ficheros crudos y el
import y la fusión con el histórico siguen en Python, en el workflow, que es
donde ya vivían.

El PC deja de ser necesario, pero sigue sirviendo: `scripts/pull_movil.py`
hace lo mismo por ADB cuando lo enciendas.

## Es deliberadamente tonta

No decide a qué día pertenece una noche de sueño, no convierte el agua
corporal a porcentaje y no deriva el IMC. Todo eso vive en
`src/rutina/sources/health_app.py`, donde se cambia sin recompilar ni volver a
instalar nada en el móvil.

Lo único que sí hace la app y no podría hacerse fuera es pedir los totales
diarios con `aggregateGroupByPeriod`: ahí Health Connect deduplica por su
lista de prioridad antes de sumar, que es justo lo que la Google Sheet no
hacía y obligaba a tomar el máximo por fecha.

## El servicio de accesibilidad

Está limitado a FitDays en `res/xml/accesibilidad.xml`
(`android:packageNames="cn.fitdays.fitdays"`). Sin esa línea vería todo lo que
apareciera en tu pantalla. Con ella, solo esa app.

Busca los botones **por su texto**, no por coordenadas, igual que hacía el
script de ADB, y pulsa el nodo (`ACTION_CLICK`) en lugar de simular un dedo:
funciona aunque el botón esté desplazado y no depende de la resolución.

## Requisitos de compilación

Un JDK **con `javac`** (el `java` de Ubuntu suele traer solo el runtime).
`build.sh` busca uno en `$JAVA_HOME`, `~/Android/jdk` y `/usr/lib/jvm`. Si no
hay ninguno: `sudo apt install openjdk-21-jdk`.

AGP 8.13.2 · Kotlin 2.3.21 · Gradle 8.14.3 · WorkManager 2.11.2 ·
compileSdk 36 · minSdk 29.
