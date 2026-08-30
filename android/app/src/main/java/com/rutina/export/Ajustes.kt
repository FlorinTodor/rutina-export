package com.rutina.export

import android.content.Context

/**
 * Lo poco que hay que recordar entre ejecuciones.
 *
 * En SharedPreferences privadas: en un movil sin root ninguna otra app puede
 * leerlas. No se usa EncryptedSharedPreferences porque Google la dejo obsoleta
 * en 2024 y el almacenamiento privado ya cubre el caso.
 *
 * El token debe ser fine-grained, limitado a ESTE repositorio y con permiso
 * de Contents (lectura y escritura) y nada mas. Si se filtra, lo peor que
 * puede hacer quien lo tenga es escribir en el repo de tu rutina.
 */
object Ajustes {
    private const val FICHERO = "rutina"
    private const val TOKEN = "github_token"
    private const val REPO = "github_repo"
    private const val ULTIMA = "ultima_subida"
    private const val DIAS = "dias"
    private const val PENDIENTE = "fitdays_pendiente"
    private const val DOCS = "carpeta_docs"
    private const val RUTA = "ruta"
    private const val MEDIDAS = "medidas"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(FICHERO, Context.MODE_PRIVATE)

    var Context.token: String
        get() = prefs(this).getString(TOKEN, "").orEmpty()
        set(v) { prefs(this).edit().putString(TOKEN, v.trim()).apply() }

    /** "usuario/repositorio" de GitHub. Sin valor por defecto: es de cada uno. */
    var Context.repo: String
        get() = prefs(this).getString(REPO, "").orEmpty()
        set(v) { prefs(this).edit().putString(REPO, v.trim()).apply() }

    /** Donde se deja el JSON dentro del repositorio. */
    var Context.ruta: String
        get() = prefs(this).getString(RUTA, "data/inbox/health.json").orEmpty()
        set(v) { prefs(this).edit().putString(RUTA, v.trim().ifEmpty { "data/inbox/health.json" }).apply() }

    var Context.ultimaSubida: String
        get() = prefs(this).getString(ULTIMA, "nunca").orEmpty()
        set(v) { prefs(this).edit().putString(ULTIMA, v).apply() }

    var Context.dias: Int
        get() = prefs(this).getInt(DIAS, 7)
        set(v) { prefs(this).edit().putInt(DIAS, v).apply() }

    /** Dia (ISO) para el que queda pendiente exportar FitDays, o "" si no. */
    var Context.fitdaysPendiente: String
        get() = prefs(this).getString(PENDIENTE, "").orEmpty()
        set(v) { prefs(this).edit().putString(PENDIENTE, v).apply() }

    /**
     * Permiso persistente sobre la carpeta Documents, en forma de URI.
     *
     * Hace falta porque el almacenamiento por ambitos no deja leer con la API
     * de File el fichero que ha escrito OTRA app. Se pide una vez con el
     * selector del sistema y queda concedido para siempre. Es mucho mas
     * estrecho que MANAGE_EXTERNAL_STORAGE, que daria acceso a todo.
     */
    var Context.carpetaDocs: String
        get() = prefs(this).getString(DOCS, "").orEmpty()
        set(v) { prefs(this).edit().putString(DOCS, v).apply() }

    /**
     * Las tandas de cinta metrica, en JSON.
     *
     * Se guardan TODAS y se suben TODAS cada vez, no solo la ultima. Son
     * cuatro numeros por semana, no pesa nada, y asi da igual que el workflow
     * no haya llegado a importar la anterior: el importador se queda con la
     * ultima tanda de cada dia y repetirlas no rompe nada.
     */
    var Context.medidas: String
        get() = prefs(this).getString(MEDIDAS, "[]").orEmpty()
        set(v) { prefs(this).edit().putString(MEDIDAS, v).apply() }

    fun configurado(ctx: Context): Boolean = with(Ajustes) { ctx.token.isNotEmpty() }
}
