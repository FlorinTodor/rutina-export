package com.rutina.export

import android.app.KeyguardManager
import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.flow.first
import com.rutina.export.Ajustes.dias
import com.rutina.export.Ajustes.horaMin
import com.rutina.export.Ajustes.repo
import com.rutina.export.Ajustes.ruta
import com.rutina.export.Ajustes.token
import com.rutina.export.Ajustes.ultimaSubida
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId
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
        /** La hora a la que toca, segun los ajustes. Por defecto las 20:45. */
        fun hora(ctx: Context): LocalTime = LocalTime.of(ctx.horaMin / 60, ctx.horaMin % 60)

        /**
         * Programa la ejecucion diaria a la hora de los ajustes.
         *
         * Se re-encola siempre (CANCEL_AND_REENQUEUE), no se "actualiza".
         * Con ExistingPeriodicWorkPolicy.UPDATE, WorkManager conserva el
         * calendario del trabajo que ya estaba encolado y SE SALTA el nuevo
         * initialDelay: el trabajo seguia disparandose a la hora en que se
         * encolo la primera vez, y cambiar la hora no hacia absolutamente
         * nada. Se vio en el dumpsys del movil, con la pantalla anunciando
         * las 20:45 y Android con el trabajo puesto a las 18:28.
         *
         * Re-encolar de mas es barato: llamarlo al abrir la app solo mueve la
         * proxima ejecucion al siguiente hueco de la hora elegida, que es
         * justo donde ya deberia estar.
         */
        fun programar(ctx: Context) {
            val ahora = LocalDateTime.now()
            var proxima = ahora.toLocalDate().atTime(hora(ctx))
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
                TRABAJO, ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE, trabajo)
            Log.i(TAG, "Programado para dentro de ${espera.toHours()}h ${espera.toMinutes() % 60}m")
        }

        /**
         * Si el trabajo esta REALMENTE en la cola de Android.
         *
         * La pantalla calculaba la proxima ejecucion mirando el reloj, asi que
         * anunciaba "20:45, dentro de 23h" aunque no hubiera nada programado.
         * Un force-stop cancela los trabajos y deja la app en estado detenido:
         * paso de verdad, y el usuario no tenia forma de saberlo hasta que no
         * subieron los datos.
         */
        /**
         * Encola AHORA el mismo trabajo que corre a las 20:45.
         *
         * El boton "Exportar y subir ahora" ejecuta el codigo de la pantalla,
         * que no es el mismo: el de verdad corre en segundo plano, sin
         * actividad viva, y es el unico que puede fallar por permisos de
         * segundo plano o por el ahorro de bateria. Sin esto no habia forma de
         * probarlo salvo esperar a las 20:45 y ver si aparecian los datos.
         */
        fun probarAhora(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniqueWork(
                "$TRABAJO-prueba", ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<TrabajoDiario>()
                    .setConstraints(Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .build())
            Log.i(TAG, "Prueba del trabajo diario encolada")
        }

        /**
         * Cuando va a saltar DE VERDAD, segun WorkManager, o null si no hay nada.
         *
         * La pantalla lo calculaba con el reloj a partir de la hora elegida,
         * asi que enseñaba la hora que queriamos y no la que Android tenia
         * puesta. Cuando las dos se separaron (ver `programar`) no habia forma
         * de notarlo mirando la app.
         */
        suspend fun proximaReal(ctx: Context): LocalDateTime? = try {
            WorkManager.getInstance(ctx)
                .getWorkInfosForUniqueWorkFlow(TRABAJO).first()
                .firstOrNull { !it.state.isFinished }
                ?.nextScheduleTimeMillis
                ?.takeIf { it > 0 && it != Long.MAX_VALUE }
                ?.let {
                    LocalDateTime.ofInstant(Instant.ofEpochMilli(it), ZoneId.systemDefault())
                }
        } catch (e: Exception) {
            null
        }

        suspend fun programado(ctx: Context): Boolean = try {
            // Se le pregunta a WorkManager, no al JobScheduler.
            //
            // Antes se miraba `JobScheduler.getAllPendingJobs()`, con el
            // razonamiento de que WorkManager programa por debajo con el
            // JobScheduler y asi la comprobacion salia sincrona. Desde Android
            // 14 eso da SIEMPRE que no. WorkManager 2.9+ mete sus trabajos en
            // un namespace propio del JobScheduler ("androidx.work..."), y
            // getAllPendingJobs() solo devuelve los del namespace por defecto:
            // el trabajo estaba encolado y la pantalla anunciaba "NO
            // PROGRAMADA".
            //
            // Se podria pedir ese namespace por su nombre, pero es un detalle
            // interno de WorkManager que puede cambiar de version. Preguntarle
            // a WorkManager es la respuesta autoritativa.
            //
            // Se usa la variante ...Flow y no la normal a proposito: la normal
            // devuelve un ListenableFuture de Guava, que no esta en el
            // classpath (y meter Guava por una comprobacion no compensa). La
            // de Flow la da work-runtime-ktx, que ya es dependencia.
            WorkManager.getInstance(ctx)
                .getWorkInfosForUniqueWorkFlow(TRABAJO).first()
                .any { !it.state.isFinished }
        } catch (e: Exception) {
            false
        }

        fun desbloqueado(ctx: Context): Boolean {
            val km = ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            return !km.isKeyguardLocked
        }
    }
}
