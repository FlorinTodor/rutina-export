package com.rutina.export

import android.content.Context
import android.provider.Settings
import android.util.Log
import com.rutina.export.Ajustes.fitdaysPendiente
import java.time.LocalDate

/**
 * Decide CUANDO se exporta FitDays. El como lo hace [FitdaysServicio].
 *
 * Hay un limite que no se puede saltar: manejar la interfaz de otra app exige
 * la pantalla desbloqueada, porque Android no pinta una actividad ajena sobre
 * el bloqueo. Ni con accesibilidad ni con ADB. Asi que no se puede garantizar
 * una hora fija.
 *
 * No importa tanto como parece: el export de FitDays trae SIEMPRE el historico
 * completo, asi que saltarse dias no pierde nada, solo retrasa. Se marca el
 * dia como pendiente y se aprovecha el primer desbloqueo que haya.
 */
object Fitdays {

    const val PAQUETE = "cn.fitdays.fitdays"
    private const val TAG = "rutina"

    /** Exporta ya si se puede; si no, lo deja apuntado para el proximo desbloqueo. */
    fun intentarOEsperar(ctx: Context) {
        if (!servicioActivo(ctx)) {
            Log.i(TAG, "FitDays: el servicio de accesibilidad esta apagado, no lo intento")
            return
        }
        val hoy = LocalDate.now().toString()
        if (TrabajoDiario.desbloqueado(ctx)) {
            Log.i(TAG, "FitDays: movil desbloqueado, exporto ahora")
            ctx.fitdaysPendiente = hoy
            FitdaysServicio.pedirExport()
        } else {
            Log.i(TAG, "FitDays: movil bloqueado, queda pendiente para el proximo desbloqueo")
            ctx.fitdaysPendiente = hoy
        }
    }

    fun hayPendiente(ctx: Context): Boolean =
        ctx.fitdaysPendiente == LocalDate.now().toString()

    fun marcarHecho(ctx: Context) {
        ctx.fitdaysPendiente = ""
    }

    /** Si el usuario ha activado el servicio en Ajustes > Accesibilidad. */
    fun servicioActivo(ctx: Context): Boolean {
        val activos = Settings.Secure.getString(
            ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return activos.contains("${ctx.packageName}/${FitdaysServicio::class.java.name}")
    }
}
