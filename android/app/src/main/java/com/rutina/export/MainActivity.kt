package com.rutina.export

import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import com.rutina.export.Ajustes.carpetaDocs
import com.rutina.export.Ajustes.dias
import com.rutina.export.Ajustes.repo
import com.rutina.export.Ajustes.ruta
import com.rutina.export.Ajustes.token
import com.rutina.export.Ajustes.ultimaSubida
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.LocalDateTime

/**
 * La pantalla: ver que falta por configurar y forzar una ejecucion.
 *
 * El trabajo de verdad lo hace [TrabajoDiario] con la app cerrada. Esto
 * existe solo para lo que no se puede hacer sin interfaz: pegar el token,
 * conceder permisos y comprobar que todo esta en su sitio.
 *
 * Se presenta como una lista de comprobacion, no como una fila de botones:
 * hay cinco cosas que configurar la primera vez y sin verlas juntas no se
 * sabe cual falta. Cada linea en rojo trae su boton para arreglarla, y
 * desaparece al quedar resuelta.
 *
 * Sigue aceptando el arranque por ADB, que es como la usa el PC:
 *     adb shell am start -n com.rutina.export/.MainActivity --ei dias 7
 */
class MainActivity : ComponentActivity() {

    private lateinit var raiz: LinearLayout
    private lateinit var aviso: TextView
    private var concedidos: Set<String> = emptySet()
    private var trabajando = false

    private val pedirPermisos = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) { dados ->
        concedidos = dados
        if (dados.isEmpty()) decir("No has concedido ningún permiso en Health Connect")
        pintar()
    }

    /**
     * Acceso a Documents, que hace falta para leer el CSV de FitDays.
     *
     * El almacenamiento por ambitos no deja leer con File el fichero que ha
     * escrito otra app. Esto lo pide una vez con el selector del sistema y
     * queda para siempre.
     */
    private val pedirCarpeta = registerForActivityResult(
        ActivityResultContracts.OpenDocumentTree()
    ) { uri ->
        if (uri == null) return@registerForActivityResult
        contentResolver.takePersistableUriPermission(
            uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        carpetaDocs = uri.toString()
        pintar()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        dias = intent.getIntExtra("dias", dias).coerceIn(1, 400)
        val desdePC = intent.hasExtra("dias")

        raiz = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(40))
        }
        val scroll = ScrollView(this).apply { addView(raiz) }
        setContentView(scroll)

        // Android 15 dibuja de borde a borde con targetSdk 35+, asi que el
        // contenido se metia debajo de la barra de estado. Se aparta lo justo,
        // preguntando al sistema en vez de poniendo un margen a ojo.
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, b.top, 0, b.bottom)
            insets
        }

        val estado = HealthConnectClient.getSdkStatus(this)
        if (estado != HealthConnectClient.SDK_AVAILABLE) {
            raiz.addView(titulo("Rutina Export"))
            raiz.addView(parrafo(
                if (estado == HealthConnectClient.SDK_UNAVAILABLE)
                    "Este móvil no tiene Health Connect."
                else "Health Connect necesita actualizarse desde Play Store."))
            return
        }

        lifecycleScope.launch {
            concedidos = HealthConnectClient.getOrCreate(this@MainActivity)
                .permissionController.getGrantedPermissions()
            if (token.isNotEmpty()) TrabajoDiario.programar(this@MainActivity)
            pintar()
            when {
                // el PC solo quiere el fichero; ya lo sube el mismo
                desdePC -> exportar(subir = false)
                faltanPermisos().isNotEmpty() -> pedirPermisos.launch(Salud.permisos)
            }
        }
    }

    /** Relanzar la app desde el PC sin matarla antes.
     *
     * El puente del PC necesitaba que `onCreate` se volviera a ejecutar para
     * regenerar el JSON, y lo conseguia con `am force-stop`. Eso tiene un
     * efecto que no se ve: Android saca a una app parada a la fuerza de la
     * lista de servicios de accesibilidad habilitados, y no la vuelve a meter.
     * Es decir, cada ejecucion del temporizador apagaba la exportacion de
     * FitDays. Con `singleTop` + `onNewIntent` la actividad se reutiliza, se
     * lee el `dias` nuevo y se exporta igual, sin parar nada.
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (!intent.hasExtra("dias")) return
        dias = intent.getIntExtra("dias", dias).coerceIn(1, 400)
        if (::raiz.isInitialized) pintar()
        exportar(subir = false)
    }

    override fun onResume() {
        super.onResume()
        // la accesibilidad se activa en Ajustes, fuera de la app
        if (::raiz.isInitialized && raiz.childCount > 0) pintar()
    }

    // ------------------------------------------------------------- pantalla

    private fun faltanPermisos() = Salud.permisos - concedidos

    private fun pintar() {
        raiz.removeAllViews()
        raiz.addView(titulo("Rutina Export"))
        raiz.addView(parrafo("Tus datos de salud van del móvil a GitHub sin pasar " +
                "por ningún intermediario de pago."))

        aviso = parrafo("").apply { visibility = TextView.GONE }

        // --- que falta por configurar ---
        val pendientes = mutableListOf<Punto>()
        val hechos = mutableListOf<Punto>()

        fun punto(ok: Boolean, etiqueta: String, detalle: String,
                  accion: Pair<String, () -> Unit>? = null) {
            (if (ok) hechos else pendientes) += Punto(ok, etiqueta, detalle, accion)
        }

        val nPerms = Salud.permisos.size - faltanPermisos().size
        punto(faltanPermisos().isEmpty(), "Health Connect",
              "$nPerms de ${Salud.permisos.size} permisos",
              "Conceder" to { pedirPermisos.launch(Salud.permisos) })
        punto(token.isNotEmpty() && repo.isNotEmpty(), "GitHub",
              when {
                  repo.isEmpty() -> "falta el repositorio, más abajo"
                  token.isEmpty() -> "falta el token, más abajo"
                  else -> "$repo · $ruta"
              })
        punto(Fitdays.servicioActivo(this), "Accesibilidad",
              if (Fitdays.servicioActivo(this)) "activa, para FitDays" else "apagada: FitDays no se exportará",
              "Activar" to { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) })
        punto(carpetaDocs.isNotEmpty(), "Carpeta Documents",
              if (carpetaDocs.isEmpty()) "sin dar: no puedo leer el CSV de FitDays" else "concedida",
              "Dar acceso" to {
                  pedirCarpeta.launch(Uri.parse(
                      "content://com.android.externalstorage.documents/document/primary%3ADocuments"))
              })

        if (pendientes.isNotEmpty()) {
            raiz.addView(rotulo("Falta por configurar"))
            raiz.addView(tarjeta(pendientes))
        }
        if (hechos.isNotEmpty()) {
            raiz.addView(rotulo(if (pendientes.isEmpty()) "Todo listo" else "Ya configurado"))
            raiz.addView(tarjeta(hechos))
        }

        // --- ventana de dias ---
        raiz.addView(rotulo("Cuántos días leer"))
        raiz.addView(parrafo("Cada lectura reescribe esos días en el histórico, así " +
                "que una ventana más ancha corrige días viejos que se midieron a " +
                "medias. Cuesta unos segundos más y nada de batería."))
        raiz.addView(selectorDias())

        // --- estado ---
        raiz.addView(rotulo("Estado"))
        raiz.addView(tarjeta(listOf(
            Punto(true, "Última subida", ultimaSubida.replace('T', ' ')),
            Punto(true, "Próxima automática", proxima()),
            Punto(true, "Ventana", "$dias días hacia atrás"),
        ), conIconos = false))

        raiz.addView(aviso)

        // --- acciones ---
        raiz.addView(rotulo("Ahora mismo"))
        raiz.addView(boton("Exportar y subir ahora", principal = true) {
            if (repo.isEmpty() || token.isEmpty()) decir("Configura antes GitHub, más abajo")
            else exportar(subir = true)
        })
        if (!TrabajoDiario.programado(this) && token.isNotEmpty()) {
            raiz.addView(parrafo("Si esto se repite, quita la app del ahorro de " +
                    "batería: Ajustes → Batería → Límites de uso en segundo plano → " +
                    "Apps que no se pondrán en reposo. Samsung detiene los trabajos " +
                    "programados de las apps que no están en esa lista."))
        }
        raiz.addView(boton("Probar la ejecución automática") {
            if (repo.isEmpty() || token.isEmpty()) decir("Configura antes GitHub, más abajo")
            else {
                TrabajoDiario.probarAhora(this)
                decir("Encolado el MISMO trabajo que corre a las 20:45, en segundo " +
                      "plano. Cierra la app y vuelve a abrirla en un minuto: si " +
                      "«Última subida» ha cambiado, la automatización funciona.")
            }
        })
        raiz.addView(boton("Apuntar medidas de cinta") {
            startActivity(Intent(this, MedidasActivity::class.java))
        })
        raiz.addView(boton("Exportar FitDays ahora") {
            if (!Fitdays.servicioActivo(this)) decir("Activa antes la accesibilidad")
            else if (carpetaDocs.isEmpty()) decir("Da antes acceso a la carpeta Documents")
            else {
                decir("Abriendo FitDays, tarda unos 45 segundos.\nNo toques la pantalla mientras.")
                FitdaysServicio.pedirExport()
            }
        })

        // --- token ---
        raiz.addView(rotulo("GitHub"))
        raiz.addView(parrafo("El repositorio donde se sube, y un token fine-grained " +
                "limitado a él con permiso de Contents (lectura y escritura). Nada más."))
        val campoRepo = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            setText(repo)
        }
        raiz.addView(TextInputLayout(this).apply {
            hint = "usuario/repositorio"
            addView(campoRepo)
        })
        // El campo NUNCA se rellena con el token guardado. El volcado de
        // accesibilidad expone el texto de un EditText aunque sea de tipo
        // contrasena, asi que cualquier app con permiso de accesibilidad
        // podria leerlo de la pantalla. Se deja vacio: si ya hay uno, se
        // escribe otro para cambiarlo, y si no se escribe nada no se toca.
        val campo = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }
        raiz.addView(TextInputLayout(this).apply {
            hint = if (token.isEmpty()) "github_pat_..."
                   else "hay uno guardado · escribe otro para cambiarlo"
            addView(campo)
        })
        raiz.addView(boton("Guardar") {
            repo = campoRepo.text.toString()
            // vacio significa "no lo cambies", no "borralo"
            campo.text?.toString()?.takeIf { it.isNotBlank() }?.let { token = it }
            TrabajoDiario.programar(this)
            campo.setText("")
            decir(when {
                repo.isEmpty() -> "Falta el repositorio"
                token.isEmpty() -> "Falta el token"
                else -> "Guardado. Ya puedes pulsar «Exportar y subir ahora»."
            })
            pintar()
        })
    }

    /**
     * Ventana de lectura, en dias.
     *
     * Estaba fija en 7 y solo se podia cambiar lanzando la app desde el PC con
     * un extra, que no es forma. Los valores no son arbitrarios: 7 cubre la
     * semana en curso, 30 y 90 sirven para recuperar tras un tiempo sin
     * sincronizar, y 365 para una carga inicial.
     */
    private fun selectorDias(): LinearLayout {
        val fila = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        }
        for (n in listOf(7, 30, 90, 365)) {
            val elegido = dias == n
            fila.addView(MaterialButton(this, null,
                if (elegido) com.google.android.material.R.attr.materialButtonStyle
                else com.google.android.material.R.attr.materialButtonOutlinedStyle
            ).apply {
                text = if (n == 365) "1 año" else "$n d"
                layoutParams = LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)
                    .apply { marginEnd = dp(6) }
                setOnClickListener {
                    dias = n
                    decir("Ventana de $n días. Se aplica en la próxima lectura.")
                    pintar()
                }
            })
        }
        return fila
    }

    private data class Punto(val ok: Boolean, val etiqueta: String, val detalle: String,
                             val accion: Pair<String, () -> Unit>? = null)

    private fun tarjeta(puntos: List<Punto>, conIconos: Boolean = true): MaterialCardView {
        val caja = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(8), dp(16), dp(8))
        }
        for (p in puntos) {
            val fila = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, dp(10), 0, dp(10))
            }
            if (conIconos) {
                fila.addView(TextView(this).apply {
                    text = if (p.ok) "✓" else "•"
                    setTextColor(getColor(if (p.ok) R.color.ok else R.color.falta))
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
                    setTypeface(null, Typeface.BOLD)
                    width = dp(28)
                })
            }
            fila.addView(LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)
                addView(TextView(this@MainActivity).apply {
                    text = p.etiqueta
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
                })
                addView(TextView(this@MainActivity).apply {
                    text = p.detalle
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                    alpha = 0.65f
                })
            })
            p.accion?.takeIf { !p.ok }?.let { (texto, accion) ->
                fila.addView(MaterialButton(this,
                    null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                    text = texto
                    setOnClickListener { accion() }
                })
            }
            caja.addView(fila)
        }
        return MaterialCardView(this).apply {
            radius = dp(14).toFloat()
            cardElevation = 0f
            strokeWidth = dp(1)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
                .apply { bottomMargin = dp(8) }
            addView(caja)
        }
    }

    private fun boton(texto: String, principal: Boolean = false,
                      accion: () -> Unit): MaterialButton =
        MaterialButton(this, null,
            if (principal) com.google.android.material.R.attr.materialButtonStyle
            else com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
            text = texto
            isEnabled = !trabajando
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
                .apply { bottomMargin = dp(8) }
            setOnClickListener { accion() }
        }

    private fun titulo(t: String) = TextView(this).apply {
        text = t
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
        setTypeface(null, Typeface.BOLD)
        setPadding(0, 0, 0, dp(4))
    }

    private fun rotulo(t: String) = TextView(this).apply {
        text = t.uppercase()
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        setTypeface(null, Typeface.BOLD)
        alpha = 0.55f
        letterSpacing = 0.08f
        setPadding(dp(4), dp(24), 0, dp(8))
    }

    private fun parrafo(t: String) = TextView(this).apply {
        text = t
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        alpha = 0.75f
        setPadding(dp(4), 0, dp(4), dp(8))
    }

    private fun proxima(): String {
        if (token.isEmpty()) return "sin token, no está programada"
        // preguntar a Android, no al reloj: un force-stop cancela el trabajo y
        // la pantalla seguia prometiendo una ejecucion que no iba a ocurrir
        if (!TrabajoDiario.programado(this)) return "NO PROGRAMADA · abre la app para reactivarla"
        val ahora = LocalDateTime.now()
        var p = ahora.toLocalDate().atTime(TrabajoDiario.HORA)
        if (!p.isAfter(ahora)) p = p.plusDays(1)
        val d = Duration.between(ahora, p)
        return "${TrabajoDiario.HORA} · dentro de ${d.toHours()}h ${d.toMinutes() % 60}m"
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun decir(t: String) {
        if (!::aviso.isInitialized) return
        aviso.text = t
        aviso.visibility = if (t.isEmpty()) TextView.GONE else TextView.VISIBLE
    }

    // -------------------------------------------------------------- acciones

    private fun exportar(subir: Boolean) = lifecycleScope.launch {
        // Sin esto la pantalla no daba ninguna senal y era facil pulsar tres
        // veces seguidas creyendo que no hacia nada. Paso de verdad.
        trabajando = true
        pintar()
        decir("Leyendo Health Connect…")
        try {
            val json = withContext(Dispatchers.IO) { Salud.recoger(this@MainActivity, dias) }
            val texto = json.toString(1)
            Salud.escribir(this@MainActivity, Salud.FICHERO, texto)
            val n = json.getJSONArray("dias").length()
            Log.i(TAG, "OK $n dias -> ${Salud.CARPETA}/${Salud.FICHERO}")

            if (subir && token.isNotEmpty()) {
                decir("Leídos $n días. Subiendo a GitHub…")
                withContext(Dispatchers.IO) {
                    GitHub(token, repo).subir(ruta, texto.toByteArray(),
                        "movil: salud al ${LocalDateTime.now().withNano(0)}".replace('T', ' '))
                }
                Salud.borrar(this@MainActivity, Salud.FICHERO)   // ya esta a salvo
                ultimaSubida = LocalDateTime.now().withNano(0).toString()
                decir("Subidos $n días a GitHub y borrados del móvil.")
                Fitdays.intentarOEsperar(this@MainActivity)
            } else if (subir) {
                decir("Exportados $n días, pero no hay token: no los he subido.")
            } else {
                decir("Exportados $n días a ${Salud.CARPETA}/${Salud.FICHERO}")
            }
        } catch (e: Exception) {
            Log.e(TAG, "FALLO ${e::class.simpleName}: ${e.message}")
            decir("Fallo: ${e.message}")
            runCatching {
                Salud.escribir(this@MainActivity, Salud.FICHERO,
                    org.json.JSONObject().put("error", e.message.orEmpty()).toString())
            }
        } finally {
            trabajando = false
            val texto = aviso.text.toString()
            pintar()
            decir(texto)
        }
    }

    companion object {
        private const val TAG = "rutina"
    }
}
