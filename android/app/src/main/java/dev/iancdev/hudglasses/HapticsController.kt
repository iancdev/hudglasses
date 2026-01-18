package dev.iancdev.hudglasses

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import org.json.JSONObject
import kotlin.math.abs

class HapticsController(
    context: Context,
) {
    private val vibrator: Vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        vm.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }

    private var enabled: Boolean = true
    private var directionEnabled: Boolean = false
    private var lastBuzzMs: Long = 0
    private var lastDirectionDeg: Float = 0f

    fun setEnabled(enabled: Boolean) {
        this.enabled = enabled
        if (!enabled) {
            stop()
        }
    }

    fun setDirectionEnabled(enabled: Boolean) {
        this.directionEnabled = enabled
        if (!enabled) {
            lastDirectionDeg = 0f
        }
    }

    fun stop() {
        runCatching { vibrator.cancel() }
        lastBuzzMs = 0
        lastDirectionDeg = 0f
    }

    fun onEvent(evt: HudEvent) {
        if (!enabled) return
        when (evt) {
            is HudEvent.EventsMessage -> handleEvents(evt.json)
            is HudEvent.SttMessage -> Unit
        }
    }

    private fun handleEvents(obj: JSONObject) {
        when (obj.optString("type")) {
            "alarm.fire" -> if (obj.optString("state") == "started") {
                buzz(pattern = longArrayOf(0, 800, 200, 800))
            }
            "alarm.car_horn" -> if (obj.optString("state") == "started") {
                buzz(pattern = longArrayOf(0, 200, 100, 200, 100, 200))
            }
            "alarm.siren" -> if (obj.optString("state") == "started") {
                // Match the fire alarm pattern (same urgency bucket).
                buzz(pattern = longArrayOf(0, 800, 200, 800))
            }
            "alert.keyword" -> {
                buzz(pattern = longArrayOf(0, 120, 80, 120, 80, 120))
            }
            "direction.ui" -> {
                if (!directionEnabled) return
                val intensity = obj.optDouble("intensity", 0.0).toFloat()
                val direction = obj.optDouble("directionDeg", 0.0).toFloat()
                if (intensity < 0.25f) return
                if (abs(direction - lastDirectionDeg) < 25f) return
                lastDirectionDeg = direction
                // Simple directional buzz: one pulse for left, two for right.
                if (direction < 0) {
                    buzz(pattern = longArrayOf(0, 120))
                } else {
                    buzz(pattern = longArrayOf(0, 80, 80, 80))
                }
            }
        }
    }

    private fun buzz(pattern: LongArray) {
        val now = System.currentTimeMillis()
        if (now - lastBuzzMs < 250) return
        lastBuzzMs = now

        val effect = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            VibrationEffect.createWaveform(pattern, -1)
        } else {
            @Suppress("DEPRECATION")
            null
        }
        if (effect != null) {
            vibrator.vibrate(effect)
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(pattern.sum())
        }
    }
}
