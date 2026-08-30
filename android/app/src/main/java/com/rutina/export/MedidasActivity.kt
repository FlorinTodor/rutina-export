package com.rutina.export

import android.app.DatePickerDialog
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.rutina.export.Ajustes.medidas
import com.rutina.export.Ajustes.repo
import com.rutina.export.Ajustes.ruta
import com.rutina.export.Ajustes.token
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Medidas de cinta metrica, tecleadas a mano.
 *
 * Ni la bascula ni Health Connect miden perimetros: Health Connect no tiene
 * ningun tipo de dato para ellos. Esta es la unica via.
 *
 * Los campos se dejan VACIOS aunque haya medidas anteriores, y el valor de la
 * ultima vez se ensena como pista. Rellenarlos automaticamente guardaria como
 * medido de hoy algo que no se ha medido hoy, que es peor que no tener el
 * dato: una linea plana en la grafica pareceria "no ha cambiado" cuando en
 * realidad es "no se midio".
 */
class MedidasActivity : ComponentActivity() {

    private val campos = LinkedHashMap<String, TextInputEditText>()
    private lateinit var nota: TextInputEditText
    private lateinit var aviso: TextView
    private var dia: LocalDate = LocalDate.now()
    private lateinit var botonFecha: MaterialButton
    private val ESPANOL: Locale = Locale.forLanguageTag("es-ES")

    /** clave en el JSON -> etiqueta, agrupadas como se miden */
    private val grupos = listOf(
        "Tronco" to listOf(
            "cuello_cm" to "Cuello",
            "pecho_cm" to "Pecho",
            "cintura_cm" to "Cintura",
            "abdomen_cm" to "Abdomen",
            "cadera_cm" to "Cadera",
        ),
        "Brazos" to listOf(
            "brazo_izq_cm" to "Brazo izquierdo",
            "brazo_der_cm" to "Brazo derecho",
            "antebrazo_izq_cm" to "Antebrazo izquierdo",
            "antebrazo_der_cm" to "Antebrazo derecho",
        ),
        "Piernas" to listOf(
            "muslo_izq_cm" to "Muslo izquierdo",
            "muslo_der_cm" to "Muslo derecho",
            "gemelo_izq_cm" to "Gemelo izquierdo",
            "gemelo_der_cm" to "Gemelo derecho",
        ),
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val caja = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(40))
        }
        val scroll = ScrollView(this).apply { addView(caja) }
        setContentView(scroll)
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, b.top, 0, b.bottom)
            insets
        }

        caja.addView(titulo("Medidas"))
        caja.addView(parrafo("Lo que dejes en blanco no se guarda. La pista de cada " +
                "campo es lo que medía la última vez."))

        botonFecha = MaterialButton(this, null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
            setOnClickListener { elegirFecha() }
        }
        caja.addView(botonFecha)
        pintarFecha()

        val ultimas = ultimaTanda()
        for ((grupo, lista) in grupos) {
            caja.addView(rotulo(grupo))
            for ((clave, etiqueta) in lista) {
                val campo = TextInputEditText(this).apply {
                    inputType = InputType.TYPE_CLASS_NUMBER or
                                InputType.TYPE_NUMBER_FLAG_DECIMAL
                    // encadenados: el teclado ensena "siguiente" y salta al
                    // campo de abajo. Sin esto habia que cerrar el teclado a
                    // mano entre medida y medida, y cerrarlo con el boton de
                    // atras se lleva por delante la pantalla entera.
                    isSingleLine = true
                    imeOptions = EditorInfo.IME_ACTION_NEXT
                }
                campos[clave] = campo
                val previa = ultimas?.optDouble(clave)?.takeIf { !it.isNaN() }
                caja.addView(TextInputLayout(this).apply {
                    hint = if (previa != null) "$etiqueta · última: $previa cm"
                           else "$etiqueta (cm)"
                    addView(campo)
                })
            }
        }

        caja.addView(rotulo("Nota"))
        nota = TextInputEditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT
            isSingleLine = true
            imeOptions = EditorInfo.IME_ACTION_DONE      // el ultimo cierra el teclado
            setOnEditorActionListener { v, accion, _ ->
                if (accion == EditorInfo.IME_ACTION_DONE) {
                    (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager)
                        .hideSoftInputFromWindow(v.windowToken, 0)
                    v.clearFocus()
                    true
                } else false
            }
        }
        caja.addView(TextInputLayout(this).apply {
            hint = "opcional: quién midió, cómo te veías…"
            addView(nota)
        })

        aviso = parrafo("").apply { visibility = View.GONE }
        caja.addView(aviso)

        caja.addView(MaterialButton(this).apply {
            text = "Guardar y subir"
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
                .apply { topMargin = dp(16) }
            setOnClickListener { guardar() }
        })

        val n = JSONArray(medidas).length()
        if (n > 0) caja.addView(parrafo("$n mediciones guardadas en este móvil."))
    }

    // ---------------------------------------------------------------- fecha

    private fun elegirFecha() {
        DatePickerDialog(this, { _, a, m, d ->
            dia = LocalDate.of(a, m + 1, d)
            pintarFecha()
        }, dia.year, dia.monthValue - 1, dia.dayOfMonth).show()
    }

    private fun pintarFecha() {
        // el idioma se fija a proposito: la app esta en castellano y con el
        // locale del sistema salia "30 de August de 2026"
        val f = dia.format(DateTimeFormatter.ofPattern("d 'de' MMMM 'de' yyyy", ESPANOL))
        botonFecha.text = if (dia == LocalDate.now()) "Hoy · $f" else f
    }

    // -------------------------------------------------------------- guardar

    private fun ultimaTanda(): JSONObject? {
        val a = JSONArray(medidas)
        return if (a.length() == 0) null else a.optJSONObject(a.length() - 1)
    }

    private fun guardar() {
        val tanda = JSONObject().put("day", dia.toString())
        var n = 0
        for ((clave, campo) in campos) {
            val t = campo.text?.toString()?.trim().orEmpty().replace(',', '.')
            if (t.isEmpty()) continue
            val v = t.toDoubleOrNull()
            if (v == null || v < 5 || v > 300) {
                decir("«$t» no es una medida en cm válida")
                return
            }
            tanda.put(clave, Math.round(v * 10) / 10.0)
            n++
        }
        if (n == 0) {
            // Sin campos rellenos pero con tandas guardadas, lo que se quiere
            // es reintentar la subida: pasa si la subida fallo (token
            // caducado, sin red) y las medidas se quedaron solo en el movil.
            // Sin esto no habia forma de reintentarlo sin volver a teclearlas.
            val pendientes = JSONArray(medidas).length()
            if (pendientes > 0) {
                decir("Reintentando subir las $pendientes ya guardadas…")
                subir(0)
            } else {
                decir("No has puesto ninguna medida")
            }
            return
        }
        nota.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { tanda.put("nota", it) }

        // fuera la tanda del mismo dia, si la hubiera: esta la corrige
        val previas = JSONArray(medidas)
        val nuevas = JSONArray()
        for (i in 0 until previas.length()) {
            val o = previas.optJSONObject(i) ?: continue
            if (o.optString("day") != dia.toString()) nuevas.put(o)
        }
        nuevas.put(tanda)
        medidas = nuevas.toString()

        subir(n)
    }

    private fun subir(n: Int) = lifecycleScope.launch {
        if (token.isEmpty() || repo.isEmpty()) {
            decir("Guardadas $n medidas en el móvil, pero falta configurar GitHub.")
            return@launch
        }
        if (n > 0) decir("Guardadas $n medidas. Subiendo…")
        try {
            val cuerpo = JSONObject()
                .put("generado", java.time.LocalDateTime.now().withNano(0).toString())
                .put("medidas", JSONArray(medidas))
                .put("fin", true)
            val destino = ruta.substringBeforeLast('/', "data/inbox") + "/medidas.json"
            withContext(Dispatchers.IO) {
                GitHub(token, repo).subir(destino, cuerpo.toString(1).toByteArray(),
                    "movil: medidas del $dia")
            }
            val cuantas = JSONArray(medidas).length()
            decir("Subidas $cuantas mediciones. Apareceran en el dashboard en " +
                  "cuanto acabe el workflow.")
            campos.values.forEach { it.setText("") }
            nota.setText("")
        } catch (e: Exception) {
            // quedan guardadas en el movil: la proxima subida las lleva
            val pista = if (e.message?.contains("401") == true ||
                            e.message?.contains("Bad credentials") == true)
                "\n\nEl token de GitHub ya no vale (caducado o revocado). Pon " +
                "uno nuevo en la pantalla anterior y vuelve a pulsar «Guardar y " +
                "subir»: las medidas siguen guardadas aquí." else ""
            decir("Guardadas en el móvil, pero no pude subirlas: ${e.message}$pista")
        }
    }

    // ----------------------------------------------------------------- util

    private fun decir(t: String) {
        aviso.text = t
        aviso.visibility = if (t.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun titulo(t: String) = TextView(this).apply {
        text = t
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
        setTypeface(null, android.graphics.Typeface.BOLD)
    }

    private fun rotulo(t: String) = TextView(this).apply {
        text = t.uppercase()
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        setTypeface(null, android.graphics.Typeface.BOLD)
        alpha = 0.55f
        letterSpacing = 0.08f
        setPadding(dp(4), dp(20), 0, dp(8))
    }

    private fun parrafo(t: String) = TextView(this).apply {
        text = t
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
        alpha = 0.75f
        setPadding(dp(4), dp(4), dp(4), dp(12))
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
