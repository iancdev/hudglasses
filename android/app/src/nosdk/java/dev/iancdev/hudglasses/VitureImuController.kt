package dev.iancdev.hudglasses

import android.content.Context

/**
 * No-op implementation used when the Viture SDK AAR is not present.
 *
 * This keeps the hackathon build runnable on any Android device while still
 * allowing a Viture-enabled flavor (`viture`) when the SDK is available.
 */
class VitureImuController(
    private val context: Context,
    private val onPose: (Pose) -> Unit,
) {
    fun start() {
        // Intentionally no-op.
    }

    fun stop() {
        // Intentionally no-op.
    }
}

