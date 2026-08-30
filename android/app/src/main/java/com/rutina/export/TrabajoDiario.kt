package com.rutina.export

import android.app.KeyguardManager
import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.rutina.export.Ajustes.dias
import com.rutina.export.Ajustes.repo
import com.rutina.export.Ajustes.ruta
import com.rutina.export.Ajustes.token
import com.rutina.export.Ajustes.ultimaSubida
import java.time.Duration
import java.time.LocalDateTime
import java.time.LocalTime
import java.util.concurrent.TimeUnit

/**
 * Lee Health Connect y lo sube a GitHub, con la app cerrada y sin PC.
 *
 * Esto es lo que hace innecesaria la suscripcion: el permiso
 * READ_HEALTH_DATA_IN_BACKGROUND, que es gratis, solo esta gateado por la
 * revision de Play Store para apps de salud. Como esta app se instala por
 * ADB y no se publica, no hay revision que pasar.
 *
 * Sube el JSON crudo a `data/inbox/`. El import y la fusion con el historico
 * los hace el workflow, en Python, donde ya estaban: el movil no decide nada
 * sobre los datos, solo los entrega.
 *
 * WorkManager no garantiza la hora exacta; con Doze puede irse un rato. Da
 * igual: la ventana es de 7 dias, asi que llegar tarde no pierde nada.
 */
class TrabajoDiario(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (ctx.token.isEmpty() || ctx.repo.isEmpty()) {
            Log.w(TAG, "Falta el token o el repositorio de GitHub: no subo nada")
            return Result.failure()
        }

        return try {
            val json = Salud.recoger(ctx, ctx.dias)
            val texto = json.toString(1)
            Salud.escribir(ctx, Salud.FICHERO, texto)      // copia local, por si acaso

            val gh = GitHub(ctx.token, ctx.repo)
            val ahora = LocalDateTime.now().withNano(0).toString()
            gh.subir(ctx.ruta, texto.toByteArray(),
                     "movil: salud al ${ahora.replace('T', ' ')}")

            // Ya esta en GitHub: fuera del movil. Si la subida hubiera
            // fallado, la excepcion nos habria sacado de aqui y el fichero
            // seguiria ahi para que lo recogiera el PC.
            Salud.borrar(ctx, Salud.FICHERO)

            ctx.ultimaSubida = ahora
            val n = json.getJSONArray("dias").length()
            Log.i(TAG, "OK $n dias subidos desde el movil")

            // FitDays solo se puede hacer con el movil desbloqueado, porque
            // hay que manejar su interfaz. Se intenta y, si no se puede, se
            // deja pendiente para el proximo desbloqueo.
            Fitdays.intentarOEsperar(ctx)
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "FALLO ${e::class.simpleName}: ${e.message}")
            // reintento con retroceso: puede ser un corte de red y no un bug
            if (runAttemptCount < 3) Result.retry() else Result.failure()
        }
    }

    companion object {
        private const val TAG = "rutina"
        private const val TRABAJO = "rutina-diario"
        val HORA: LocalTime = LocalTime.of(20, 45)

        /** Programa la ejecucion diaria. Idempotente: llamarlo de mas no duplica. */
        fun programar(ctx: Context) {
            val ahora = LocalDateTime.now()
            var proxima = ahora.toLocalDate().atTime(HORA)
            if (!proxima.isAfter(ahora)) proxima = proxima.plusDays(1)
            val espera = Duration.between(ahora, proxima)

            val trabajo = PeriodicWorkRequestBuilder<TrabajoDiario>(1, TimeUnit.DAYS)
                .setInitialDelay(espera.toMinutes(), TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build())
                .addTag(TRABAJO)
                .build()

            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                TRABAJO, ExistingPeriodicWorkPolicy.UPDATE, trabajo)
            Log.i(TAG, "Programado para dentro de ${espera.toHours()}h ${espera.toMinutes() % 60}m")
        }

        fun desbloqueado(ctx: Context): Boolean {
            val km = ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            return !km.isKeyguardLocked
        }
    }
}
