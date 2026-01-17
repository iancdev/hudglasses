package dev.iancdev.hudglasses

/**
 * Mirror of key Viture SDK state.
 *
 * Values follow the Viture SDK `Constants`:
 * - initResult: `ERROR_INIT_*`
 * - imuState / stereo3dState: `STATE_*`
 * - imuFrequency: `IMU_FREQUENCE_*`
 */
data class VitureSdkState(
    val initResult: Int? = null,
    val imuState: Int? = null,
    val stereo3dState: Int? = null,
    val imuFrequency: Int? = null,
)

