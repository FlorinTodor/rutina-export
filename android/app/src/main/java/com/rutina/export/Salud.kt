package com.rutina.export

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.provider.MediaStore
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.aggregate.AggregateMetric
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.BasalMetabolicRateRecord
import androidx.health.connect.client.records.BodyFatRecord
import androidx.health.connect.client.records.BodyWaterMassRecord
import androidx.health.connect.client.records.BoneMassRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.FloorsClimbedRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.HeightRecord
import androidx.health.connect.client.records.LeanBodyMassRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.Period
import java.time.ZoneId
import kotlin.reflect.KClass

/**
 * Lee Health Connect y escribe el JSON. Sin interfaz.
 *
 * Vive fuera de la Activity porque lo usan dos: la pantalla, cuando lo lanzas
 * a mano o desde el PC, y el trabajo diario en segundo plano, que corre con
 * la app cerrada. Antes estaba dentro de MainActivity y no se podia reutilizar.
 */
object Salud {

    const val CARPETA = "Documents/rutina"
    const val FICHERO = "health.json"

    /** Todo lo que se pide. Si falta alguno, se sigue sin ese dato. */
    val tipos: List<KClass<out Record>> = listOf(
        StepsRecord::class, DistanceRecord::class, TotalCaloriesBurnedRecord::class,
        ActiveCaloriesBurnedRecord::class, FloorsClimbedRecord::class,
        SleepSessionRecord::class,
        HeartRateRecord::class, RestingHeartRateRecord::class,
        HeartRateVariabilityRmssdRecord::class, OxygenSaturationRecord::class,
        Vo2MaxRecord::class,
        WeightRecord::class, BodyFatRecord::class, BoneMassRecord::class,
        LeanBodyMassRecord::class, BodyWaterMassRecord::class,
        BasalMetabolicRateRecord::class, HeightRecord::class,
    )

    val permisos: Set<String>
        get() = tipos.map { HealthPermission.getReadPermission(it) }.toSet() +
                HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND

    suspend fun recoger(ctx: Context, dias: Int): JSONObject {
        val client = HealthConnectClient.getOrCreate(ctx)
        val zona = ZoneId.systemDefault()
        val concedidos = client.permissionController.getGrantedPermissions()
        fun hay(t: KClass<out Record>) = HealthPermission.getReadPermission(t) in concedidos

        // Que app escribe cada dato. Sirve para saber de que aplicaciones
        // depende de verdad el pipeline y cuales se pueden desinstalar.
        val origenes = mutableMapOf<String, MutableSet<String>>()
        fun anota(tipo: String, paquete: String) {
            origenes.getOrPut(tipo) { mutableSetOf() }.add(paquete)
        }

        val hoy = LocalDate.now(zona)
        val desde = hoy.minusDays((dias - 1).toLong())
        val inicio = desde.atStartOfDay()
        val fin = LocalDateTime.now(zona)
        val ventana = TimeRangeFilter.between(inicio, fin)

        // --- totales diarios: Health Connect ya deduplica por lista de
        //     prioridad, asi que esto no necesita el apano de tomar el maximo
        val metricas = mutableSetOf<AggregateMetric<*>>()
        if (hay(StepsRecord::class)) metricas += StepsRecord.COUNT_TOTAL
        if (hay(DistanceRecord::class)) metricas += DistanceRecord.DISTANCE_TOTAL
        if (hay(TotalCaloriesBurnedRecord::class)) metricas += TotalCaloriesBurnedRecord.ENERGY_TOTAL
        if (hay(ActiveCaloriesBurnedRecord::class)) metricas += ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL
        if (hay(FloorsClimbedRecord::class)) metricas += FloorsClimbedRecord.FLOORS_CLIMBED_TOTAL
        if (hay(HeartRateRecord::class)) metricas += setOf(HeartRateRecord.BPM_AVG, HeartRateRecord.BPM_MAX)
        if (hay(RestingHeartRateRecord::class)) metricas += RestingHeartRateRecord.BPM_AVG

        val dias_ = JSONArray()
        if (metricas.isNotEmpty()) {
            val cubos = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(metricas, ventana, Period.ofDays(1)))
            for (c in cubos) {
                c.result.dataOrigins.forEach { anota("totales_diarios", it.packageName) }
                val o = JSONObject().put("day", c.startTime.toLocalDate().toString())
                c.result[StepsRecord.COUNT_TOTAL]?.let { o.put("steps", it) }
                c.result[DistanceRecord.DISTANCE_TOTAL]?.let { o.put("distance_km", it.inKilometers) }
                c.result[TotalCaloriesBurnedRecord.ENERGY_TOTAL]?.let { o.put("total_kcal", it.inKilocalories) }
                c.result[ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL]?.let { o.put("active_kcal", it.inKilocalories) }
                c.result[FloorsClimbedRecord.FLOORS_CLIMBED_TOTAL]?.let { o.put("floors", it) }
                c.result[HeartRateRecord.BPM_AVG]?.let { o.put("avg_hr", it) }
                c.result[HeartRateRecord.BPM_MAX]?.let { o.put("max_hr", it) }
                c.result[RestingHeartRateRecord.BPM_AVG]?.let { o.put("resting_hr", it) }
                if (o.length() > 1) dias_.put(o)
            }
        }

        // --- sueno: se entrega crudo, con inicio y fin. Asignar la noche a un
        //     dia u otro es una convencion, y se decide en Python.
        val suenos = JSONArray()
        if (hay(SleepSessionRecord::class)) {
            val filtro = TimeRangeFilter.between(inicio.minusDays(1), fin)
            for (s in leer(client, SleepSessionRecord::class, filtro)) {
                anota("sueno", s.metadata.dataOrigin.packageName)
                val o = JSONObject()
                    .put("inicio", s.startTime.atZone(zona).toLocalDateTime().toString())
                    .put("fin", s.endTime.atZone(zona).toLocalDateTime().toString())
                val mins = mutableMapOf<String, Double>()
                for (t in s.stages) {
                    val campo = when (t.stage) {
                        SleepSessionRecord.STAGE_TYPE_DEEP -> "deep_min"
                        SleepSessionRecord.STAGE_TYPE_REM -> "rem_min"
                        SleepSessionRecord.STAGE_TYPE_LIGHT -> "light_min"
                        SleepSessionRecord.STAGE_TYPE_SLEEPING -> "light_min"
                        SleepSessionRecord.STAGE_TYPE_AWAKE,
                        SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED -> "awake_min"
                        else -> null
                    } ?: continue
                    val m = (t.endTime.toEpochMilli() - t.startTime.toEpochMilli()) / 60000.0
                    mins[campo] = (mins[campo] ?: 0.0) + m
                }
                mins.forEach { (k, v) -> o.put(k, redondear(v, 2)) }
                o.put("total_min", redondear(
                    (s.endTime.toEpochMilli() - s.startTime.toEpochMilli()) / 60000.0, 2))
                suenos.put(o)
            }
        }

        // --- lo que no tiene agregado: se promedia por dia en Python
        val puntos = JSONArray()
        suspend fun <T : Record> puntual(t: KClass<T>, campo: String, valor: (T) -> Double?,
                                         hora: (T) -> Instant) {
            if (!hay(t)) return
            for (r in leer(client, t, ventana)) {
                anota(campo, r.metadata.dataOrigin.packageName)
                val v = valor(r) ?: continue
                puntos.put(JSONObject().put("campo", campo)
                    .put("cuando", hora(r).atZone(zona).toLocalDateTime().toString())
                    .put("valor", redondear(v, 3)))
            }
        }
        puntual(HeartRateVariabilityRmssdRecord::class, "hrv_ms",
            { it.heartRateVariabilityMillis }, { it.time })
        puntual(OxygenSaturationRecord::class, "spo2_pct", { it.percentage.value }, { it.time })
        puntual(Vo2MaxRecord::class, "vo2max", { it.vo2MillilitersPerMinuteKilogram }, { it.time })

        // --- composicion corporal: cada medida por separado, con su hora
        val cuerpo = JSONArray()
        suspend fun <T : Record> medida(t: KClass<T>, campo: String, valor: (T) -> Double?,
                                        hora: (T) -> Instant) {
            if (!hay(t)) return
            for (r in leer(client, t, ventana)) {
                anota(campo, r.metadata.dataOrigin.packageName)
                val v = valor(r) ?: continue
                cuerpo.put(JSONObject().put("campo", campo)
                    .put("cuando", hora(r).atZone(zona).toLocalDateTime().toString())
                    .put("valor", redondear(v, 3)))
            }
        }
        medida(WeightRecord::class, "weight_kg", { it.weight.inKilograms }, { it.time })
        medida(BodyFatRecord::class, "fat_percent", { it.percentage.value }, { it.time })
        medida(BoneMassRecord::class, "bone_mass_kg", { it.mass.inKilograms }, { it.time })
        medida(LeanBodyMassRecord::class, "lean_mass_kg", { it.mass.inKilograms }, { it.time })
        medida(BodyWaterMassRecord::class, "water_kg", { it.mass.inKilograms }, { it.time })
        medida(BasalMetabolicRateRecord::class, "bmr_kcal",
            { it.basalMetabolicRate.inKilocaloriesPerDay }, { it.time })
        medida(HeightRecord::class, "height_m", { it.height.inMeters }, { it.time })

        return JSONObject()
            .put("generado", LocalDateTime.now(zona).toString())
            .put("zona", zona.id)
            .put("desde", desde.toString())
            .put("hasta", hoy.toString())
            .put("dias", dias_)
            .put("suenos", suenos)
            .put("puntos", puntos)
            .put("cuerpo", cuerpo)
            .put("faltan", JSONArray((permisos - concedidos).toList()))
            .put("origenes", JSONObject(origenes.mapValues { JSONArray(it.value.toList()) }))
            // ultima clave a proposito: si el PC la ve, el fichero esta entero
            .put("fin", true)
    }

    private suspend fun <T : Record> leer(
        client: HealthConnectClient, tipo: KClass<T>, filtro: TimeRangeFilter
    ): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        do {
            val r = client.readRecords(
                ReadRecordsRequest(recordType = tipo, timeRangeFilter = filtro, pageToken = token))
            out += r.records
            token = r.pageToken
        } while (token != null)
        return out
    }

    private fun redondear(v: Double, d: Int): Double {
        val f = Math.pow(10.0, d.toDouble())
        return Math.round(v * f) / f
    }

    /**
     * Escribe siempre en la MISMA ruta y con el MISMO nombre.
     *
     * Si el fichero ya existe se sobrescribe en vez de crear "health (1).json":
     * asi en el movil nunca hay mas de un fichero nuestro y el PC sabe
     * exactamente cual borrar despues.
     */
    fun escribir(ctx: Context, nombre: String, contenido: String): String {
        val col = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val previo = ctx.contentResolver.query(
            col, arrayOf(MediaStore.MediaColumns._ID),
            "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME}=?",
            arrayOf("$CARPETA/", nombre), null
        )?.use { if (it.moveToFirst()) ContentUris.withAppendedId(col, it.getLong(0)) else null }

        val uri = previo ?: ctx.contentResolver.insert(col, ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, nombre)
            put(MediaStore.MediaColumns.MIME_TYPE, "application/json")
            put(MediaStore.MediaColumns.RELATIVE_PATH, CARPETA)
        }) ?: throw IOException("MediaStore no me deja crear $CARPETA/$nombre")

        ctx.contentResolver.openOutputStream(uri, "wt").use {
            (it ?: throw IOException("No puedo escribir en $uri")).write(contenido.toByteArray())
        }
        return "$CARPETA/$nombre"
    }

    /**
     * Borra el fichero de la carpeta del pipeline. Solo el nuestro.
     *
     * Se llama despues de subirlo, nunca antes: si la subida falla, el
     * fichero se queda en el movil y el PC todavia puede recogerlo. Se
     * consulta por nombre Y carpeta, asi que no puede alcanzar nada mas.
     */
    fun borrar(ctx: Context, nombre: String): Boolean {
        val col = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        val n = ctx.contentResolver.delete(
            col,
            "${MediaStore.MediaColumns.RELATIVE_PATH}=? AND ${MediaStore.MediaColumns.DISPLAY_NAME}=?",
            arrayOf("$CARPETA/", nombre))
        return n > 0
    }
}
