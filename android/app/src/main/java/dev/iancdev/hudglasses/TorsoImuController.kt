package dev.iancdev.hudglasses

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock
import org.json.JSONObject
import kotlin.math.PI

class TorsoImuController(
    context: Context,
    private val onPose: (JSONObject) -> Unit,
) : SensorEventListener {
    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val sensor: Sensor? =
        sensorManager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)

    private var started = false
    private var lastSentMs: Long = 0

    fun start() {
        if (started) return
        started = true
        val s = sensor ?: return
        // SENSOR_DELAY_GAME is typically ~50Hz+. We also throttle messages below.
        sensorManager.registerListener(this, s, SensorManager.SENSOR_DELAY_GAME)
    }

    fun stop() {
        if (!started) return
        started = false
        runCatching { sensorManager.unregisterListener(this) }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    override fun onSensorChanged(event: SensorEvent) {
        if (!started) return
        if (event.sensor.type != Sensor.TYPE_GAME_ROTATION_VECTOR && event.sensor.type != Sensor.TYPE_ROTATION_VECTOR) return
        val nowMs = SystemClock.elapsedRealtime()
        if (nowMs - lastSentMs < 33) return // ~30Hz
        lastSentMs = nowMs

        // Rotation vector: [x, y, z, w] where w may be omitted.
        if (event.values.size < 3) return
        val x = event.values[0]
        val y = event.values[1]
        val z = event.values[2]
        val w = if (event.values.size >= 4) {
            event.values[3]
        } else run {
            val ww = 1f - (x * x + y * y + z * z)
            if (ww <= 0f) 0f else kotlin.math.sqrt(ww)
        }

        val r = FloatArray(9)
        SensorManager.getRotationMatrixFromVector(r, floatArrayOf(x, y, z, w))
        val o = FloatArray(3)
        SensorManager.getOrientation(r, o)
        // o[0] = azimuth/yaw around gravity (radians), range [-pi, +pi]
        val yawDeg = (o[0] * (180.0 / PI)).toFloat()

        val msg =
            JSONObject()
                .put("type", "torso_pose")
                .put("v", 1)
                .put("yawDeg", yawDeg)
                .put(
                    "q",
                    JSONObject()
                        .put("x", x)
                        .put("y", y)
                        .put("z", z)
                        .put("w", w),
                )
                .put("tMonotonicMs", (event.timestamp / 1_000_000L))
        onPose(msg)
    }
}
