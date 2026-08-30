package com.rutina.export

import android.util.Base64
import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Sube un fichero al repositorio con la API de contenidos de GitHub.
 *
 * Sin librerias: HttpURLConnection y punto. Meter Retrofit u OkHttp por dos
 * peticiones al dia no compensa el peso ni las actualizaciones.
 *
 * La API exige el `sha` del fichero que se sobrescribe. Si no se manda,
 * responde 422 en vez de crear una segunda version, asi que primero se
 * pregunta si ya existe.
 */
class GitHub(private val token: String, private val repo: String) {

    class Fallo(mensaje: String) : RuntimeException(mensaje)

    fun subir(ruta: String, contenido: ByteArray, mensaje: String): String {
        val sha = shaActual(ruta)
        val cuerpo = JSONObject()
            .put("message", mensaje)
            .put("content", Base64.encodeToString(contenido, Base64.NO_WRAP))
            .put("branch", "main")
        if (sha != null) cuerpo.put("sha", sha)

        val (codigo, respuesta) = peticion("PUT", ruta, cuerpo.toString())
        if (codigo !in 200..299) {
            throw Fallo("GitHub respondio $codigo: ${resumen(respuesta)}")
        }
        val commit = JSONObject(respuesta).optJSONObject("commit")?.optString("sha") ?: "?"
        Log.i(TAG, "Subido $ruta (commit ${commit.take(7)})")
        return commit
    }

    /** null si el fichero todavia no existe: entonces se crea. */
    private fun shaActual(ruta: String): String? {
        val (codigo, respuesta) = peticion("GET", ruta, null)
        return when {
            codigo == 404 -> null
            codigo in 200..299 -> JSONObject(respuesta).optString("sha").ifEmpty { null }
            else -> throw Fallo("No pude consultar $ruta: $codigo ${resumen(respuesta)}")
        }
    }

    private fun peticion(metodo: String, ruta: String, cuerpo: String?): Pair<Int, String> {
        val url = URL("https://api.github.com/repos/$repo/contents/$ruta")
        val c = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = metodo
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
            setRequestProperty("User-Agent", "rutina-health")
            connectTimeout = 20_000
            readTimeout = 60_000
            if (cuerpo != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }
        try {
            cuerpo?.let { c.outputStream.use { s -> s.write(it.toByteArray()) } }
            val codigo = c.responseCode
            val flujo = if (codigo in 200..299) c.inputStream else c.errorStream
            val texto = flujo?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            return codigo to texto
        } finally {
            c.disconnect()
        }
    }

    /** El mensaje de error de GitHub, sin volcar la respuesta entera al log. */
    private fun resumen(respuesta: String): String = try {
        JSONObject(respuesta).optString("message").ifEmpty { respuesta.take(160) }
    } catch (e: Exception) {
        respuesta.take(160)
    }

    companion object {
        private const val TAG = "rutina"
    }
}
