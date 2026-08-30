package com.rutina.export

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Path
import android.net.Uri
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.documentfile.provider.DocumentFile
import com.rutina.export.Ajustes.carpetaDocs
import com.rutina.export.Ajustes.repo
import com.rutina.export.Ajustes.ruta
import com.rutina.export.Ajustes.token
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.LocalDateTime

/**
 * Hace el recorrido de FitDays desde dentro del movil, sin PC ni ADB.
 *
 * Es el mismo recorrido que hacia `scripts/fitdays_pull.py`, con la misma
 * idea: buscar los botones POR SU TEXTO, no por coordenadas. Dar toques en
 * posiciones fijas se rompe con cualquier cambio de diseno o de resolucion.
 *
 * La diferencia es que aqui se pulsa el NODO (`performAction(ACTION_CLICK)`),
 * que es mas fiable que simular un dedo: funciona aunque el boton este
 * desplazado y no depende de la densidad de pantalla. Solo se recurre al
 * gesto cuando hay que tocar algo que no es un nodo pulsable, como el aviso
 * de ayuda que la app pone encima la primera vez.
 *
 *     Tablas → Datos del usuario → ⋮ → Exportar → Todas → compartir
 *            → Guardar en local
 */
class FitdaysServicio : AccessibilityService() {

    private val alcance = CoroutineScope(Dispatchers.Default)
    private var trabajando = false

    /** Cuando el usuario desbloquea, es el momento de aprovechar. */
    private val alDesbloquear = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            if (Fitdays.hayPendiente(applicationContext)) {
                Log.i(TAG, "Desbloqueo detectado y FitDays pendiente: lo hago ahora")
                exportar()
            }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instancia = this
        registerReceiver(alDesbloquear, IntentFilter(Intent.ACTION_USER_PRESENT),
                         Context.RECEIVER_NOT_EXPORTED)
        Log.i(TAG, "Servicio de accesibilidad conectado")
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(alDesbloquear) }
        instancia = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    // --------------------------------------------------------------- recorrido

    fun exportar() {
        if (trabajando) return
        trabajando = true
        alcance.launch {
            try {
                recorrido()
                Fitdays.marcarHecho(applicationContext)
            } catch (e: Exception) {
                Log.e(TAG, "FitDays FALLO: ${e.message}")
            } finally {
                trabajando = false
            }
        }
    }

    private suspend fun recorrido() {
        Log.i(TAG, "FitDays: abriendo la app")
        val lanzar = packageManager.getLaunchIntentForPackage(Fitdays.PAQUETE)
            ?: throw IllegalStateException("FitDays no esta instalada")
        lanzar.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        startActivity(lanzar)

        // Esperar a que FitDays sea de verdad la ventana activa. Un retardo
        // fijo no vale: la primera vez tarda mas y el recorrido empezaba a
        // buscar botones en una pantalla que todavia era la nuestra.
        esperarApp(Fitdays.PAQUETE)
        irAlInicio()

        pulsarTexto("Tablas")
        pulsarTexto("Datos del usuario")
        delay(2000)

        // el aviso de ayuda de la app se come el primer toque del menu
        val p = pantalla()
        gesto(p.width() / 2f, p.height() * 0.75f)
        delay(1000)

        pulsarId("choose_iv")            // menu de tres puntos
        pulsarTexto("Exportar")
        pulsarTexto("Todas")
        pulsarId("comparison_data_tips") // el icono de compartir, que no tiene texto
        pulsarTexto("Guardar en local")
        delay(4000)

        subir()
        // vuelta a la pantalla de inicio para no dejar FitDays abierta encima
        performGlobalAction(GLOBAL_ACTION_HOME)
    }

    private fun subir() {
        val ctx = applicationContext
        if (ctx.token.isEmpty()) {
            Log.w(TAG, "FitDays exportado pero sin token: no subo")
            return
        }
        if (ctx.carpetaDocs.isEmpty()) {
            throw IllegalStateException(
                "Falta el acceso a la carpeta Documents. Abre Rutina Export y pulsa " +
                "'Dar acceso a Documents': sin eso Android no me deja leer el fichero " +
                "que escribe FitDays, porque es de otra app.")
        }

        val carpeta = DocumentFile.fromTreeUri(ctx, Uri.parse(ctx.carpetaDocs))
            ?: throw IllegalStateException("No puedo abrir la carpeta Documents")
        val fichero = carpeta.listFiles()
            .filter { it.isFile && it.name?.startsWith("FitdaysData_") == true }
            .maxByOrNull { it.lastModified() }
            ?: throw IllegalStateException("La app no dejo ningun export en Documents")

        val nombre = fichero.name.orEmpty()
        val bytes = ctx.contentResolver.openInputStream(fichero.uri)?.use { it.readBytes() }
            ?: throw IllegalStateException("No puedo leer $nombre")
        Log.i(TAG, "FitDays: subiendo $nombre (${bytes.size / 1024} KB)")

        // al lado del JSON de salud, sea cual sea la carpeta configurada
        val destino = ctx.ruta.substringBeforeLast('/', "data/inbox") + "/fitdays.csv"
        GitHub(ctx.token, ctx.repo).subir(
            destino, bytes,
            "movil: fitdays al ${LocalDateTime.now().withNano(0).toString().replace('T', ' ')}")

        // Ya esta a salvo en el repositorio: se puede borrar del movil. Solo
        // este fichero, y solo porque su nombre es de los que genera FitDays.
        // el nombre se lee ANTES de borrar: despues el DocumentFile ya no existe
        if (nombre.startsWith("FitdaysData_") && fichero.delete()) {
            Log.i(TAG, "Borrado del movil: $nombre")
        }
    }

    // ------------------------------------------------------------------ toques

    /**
     * Deja FitDays en su pantalla principal, venga de donde venga.
     *
     * El script del PC resolvia esto con `am force-stop`, que una app no
     * puede hacer sobre otra. Si FitDays se quedo abierta en una pantalla
     * interna (paso: reanudo en HistoryComparisonActivity), hay que salir de
     * ella o no se encuentra "Tablas". Se retrocede como lo haria una
     * persona, comprobando despues de cada paso.
     */
    private suspend fun irAlInicio(pasos: Int = 5) {
        repeat(pasos) {
            if (buscar { it.text?.toString()?.contains("Tablas", true) == true } != null) return
            Log.i(TAG, "  no estoy en el inicio de FitDays, retrocedo")
            performGlobalAction(GLOBAL_ACTION_BACK)
            delay(1500)
            // si el retroceso nos saco de FitDays, volver a entrar
            if (rootInActiveWindow?.packageName != Fitdays.PAQUETE) {
                packageManager.getLaunchIntentForPackage(Fitdays.PAQUETE)?.let { i ->
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(i)
                }
                esperarApp(Fitdays.PAQUETE)
            }
        }
    }

    /** Bloquea hasta que la ventana activa pertenezca a [paquete]. */
    private suspend fun esperarApp(paquete: String, segundos: Int = 25) {
        var visto: CharSequence? = null
        repeat(segundos * 2) {
            val actual = rootInActiveWindow?.packageName
            if (actual == paquete) {
                Log.i(TAG, "  $paquete ya esta en pantalla")
                delay(1500)          // que termine de pintar
                return
            }
            if (actual != visto) {
                visto = actual
                Log.i(TAG, "  esperando a $paquete, ahora veo: ${actual ?: "nada"}")
            }
            delay(500)
        }
        throw IllegalStateException(
            "$paquete no llego a abrirse en ${segundos}s (lo ultimo que vi: ${visto ?: "nada"})")
    }

    private suspend fun pulsarTexto(texto: String, intentos: Int = 8) {
        repeat(intentos) {
            buscar { n ->
                (n.text?.toString()?.contains(texto, true) == true) ||
                (n.contentDescription?.toString()?.contains(texto, true) == true)
            }?.let {
                Log.i(TAG, "  pulso '$texto'")
                clic(it)
                delay(1500)
                return
            }
            delay(1500)
        }
        throw IllegalStateException("No encuentro '$texto' en pantalla")
    }

    private suspend fun pulsarId(id: String, intentos: Int = 8) {
        repeat(intentos) {
            val nodos = rootInActiveWindow
                ?.findAccessibilityNodeInfosByViewId("${Fitdays.PAQUETE}:id/$id")
            if (!nodos.isNullOrEmpty()) {
                Log.i(TAG, "  pulso id/$id")
                clic(nodos[0])
                delay(1500)
                return
            }
            delay(1500)
        }
        throw IllegalStateException("No encuentro el boton id/$id")
    }

    /**
     * Pulsa el nodo, o el primer padre que si sea pulsable.
     *
     * En FitDays el texto suele estar en un TextView dentro de un contenedor
     * clicable: pulsar el TextView no hace nada, hay que subir.
     */
    private fun clic(nodo: AccessibilityNodeInfo) {
        var n: AccessibilityNodeInfo? = nodo
        repeat(5) {
            if (n == null) return@repeat
            if (n!!.isClickable) {
                n!!.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                return
            }
            n = n!!.parent
        }
        // ningun padre es pulsable: se toca en su centro
        val r = Rect().also { nodo.getBoundsInScreen(it) }
        gesto(r.exactCenterX(), r.exactCenterY())
    }

    private fun gesto(x: Float, y: Float) {
        val camino = Path().apply { moveTo(x, y) }
        dispatchGesture(
            GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(camino, 0, 60))
                .build(), null, null)
    }

    private fun buscar(coincide: (AccessibilityNodeInfo) -> Boolean): AccessibilityNodeInfo? {
        val raiz = rootInActiveWindow ?: return null
        val pila = ArrayDeque(listOf(raiz))
        while (pila.isNotEmpty()) {
            val n = pila.removeFirst()
            if (coincide(n)) return n
            for (i in 0 until n.childCount) n.getChild(i)?.let { pila.addLast(it) }
        }
        return null
    }

    private fun pantalla(): Rect {
        val r = Rect()
        rootInActiveWindow?.getBoundsInScreen(r)
        if (r.isEmpty) {
            val m = resources.displayMetrics
            r.set(0, 0, m.widthPixels, m.heightPixels)
        }
        return r
    }

    companion object {
        private const val TAG = "rutina"
        @Volatile private var instancia: FitdaysServicio? = null

        /** Lo llama el trabajo diario. Si el servicio esta apagado, no hace nada. */
        fun pedirExport() {
            instancia?.exportar() ?: Log.w(TAG, "El servicio de accesibilidad no esta activo")
        }
    }
}
