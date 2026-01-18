package dev.iancdev.hudglasses

import android.content.Context
import android.util.Log
import com.viture.sdk.ArCallback
import com.viture.sdk.ArManager
import com.viture.sdk.Constants
import java.nio.ByteBuffer
import java.nio.ByteOrder

class VitureImuController(
    private val context: Context,
    private val onPose: (Pose) -> Unit,
    private val onSdkState: (VitureSdkState) -> Unit,
) {
    private var arManager: ArManager? = null
    private var started = false
    private var initialized = false
    private var defaultsApplied = false

    private val callback = object : ArCallback() {
        override fun onEvent(eventId: Int, event: ByteArray, ts: Long) {
            if (eventId == Constants.EVENT_ID_INIT) {
                val initResult = byteArrayToIntLittleEndian(event)
                Log.i("VitureImu", "init result=$initResult")
                onSdkState(VitureSdkState(initResult = initResult))
                if (initResult == Constants.ERROR_INIT_SUCCESS) {
                    initialized = true
                    refreshDeviceState()
                    applyDefaultsOnce()
                }
                return
            }
            Log.d("VitureImu", "eventId=$eventId bytes=${event.size}")
            // Best-effort refresh; the SDK reports changes for 3D/brightness/voice via events.
            refreshDeviceState()
        }

        override fun onImu(ts: Long, imu: ByteArray) {
            if (imu.size < 12) return
            val bb = ByteBuffer.wrap(imu).order(ByteOrder.BIG_ENDIAN)
            val roll = bb.getFloat(0)
            val pitch = bb.getFloat(4)
            val yaw = bb.getFloat(8)
            val now = System.currentTimeMillis()
            if (now - lastPoseSentMs < 50) return // ~20Hz
            lastPoseSentMs = now
            onPose(Pose(yaw = yaw, pitch = pitch, roll = roll))
        }
    }

    private var lastPoseSentMs: Long = 0

    /**
     * Register callback + initialize the SDK (requests USB permission if needed).
     * Safe to call multiple times (e.g., onResume).
     */
    fun start() {
        if (started) return
        started = true

        val mgr = try {
            arManager ?: ArManager.getInstance(context).also { arManager = it }
        } catch (t: Throwable) {
            Log.e("VitureImu", "failed to get ArManager", t)
            started = false
            onSdkState(VitureSdkState(initResult = -999))
            return
        }

        try {
            mgr.setLogOn(true)
            mgr.registerCallback(callback)

            val initResult = mgr.init()
            onSdkState(VitureSdkState(initResult = initResult))
            if (initResult == Constants.ERROR_INIT_SUCCESS) {
                initialized = true
                refreshDeviceState()
                applyDefaultsOnce()
            }
        } catch (t: Throwable) {
            Log.e("VitureImu", "start failed", t)
            runCatching { mgr.unregisterCallback(callback) }
            started = false
            onSdkState(VitureSdkState(initResult = -999))
        }
    }

    /**
     * Unregister callback; does not release native resources (call [release] on destroy).
     */
    fun stop() {
        if (!started) return
        started = false
        runCatching { arManager?.unregisterCallback(callback) }
    }

    fun release() {
        // Mirrors the SDK demo: release on destroy.
        runCatching { arManager?.release() }
    }

    fun retryInit() {
        stop()
        release()
        arManager = null
        started = false
        initialized = false
        defaultsApplied = false
        lastPoseSentMs = 0
        start()
    }

    fun setImuEnabled(enabled: Boolean) {
        if (!initialized) return
        val rc = arManager?.setImuOn(enabled) ?: return
        Log.i("VitureImu", "setImuOn($enabled) rc=$rc")
        refreshDeviceState()
    }

    fun set3dEnabled(enabled: Boolean) {
        if (!initialized) return
        val rc = arManager?.set3D(enabled) ?: return
        Log.i("VitureImu", "set3D($enabled) rc=$rc")
        refreshDeviceState()
    }

    fun setImuFrequency(mode: Int) {
        if (!initialized) return
        val rc = arManager?.setImuFrequency(mode) ?: return
        Log.i("VitureImu", "setImuFrequency($mode) rc=$rc")
        refreshDeviceState()
    }

    private fun byteArrayToIntLittleEndian(bytes: ByteArray): Int {
        var result = 0
        for (i in bytes.indices) {
            result = result or ((bytes[i].toInt() and 0xFF) shl (8 * i))
        }
        return result
    }

    private fun applyDefaultsOnce() {
        if (defaultsApplied) return
        defaultsApplied = true
        val mgr = arManager ?: return

        // For HUD mode we prefer 2D output (1920x1080) so the UI isn't stretched across 3D SBS.
        val set3dRc = mgr.set3D(false)
        Log.i("VitureImu", "default set3D(false) rc=$set3dRc")

        // Enable IMU reporting by default.
        val imuRc = mgr.setImuOn(true)
        Log.i("VitureImu", "default setImuOn(true) rc=$imuRc")

        // Choose a conservative IMU frequency by default.
        val freqRc = mgr.setImuFrequency(Constants.IMU_FREQUENCE_60)
        Log.i("VitureImu", "default setImuFrequency(60Hz) rc=$freqRc")

        refreshDeviceState()
    }

    private fun refreshDeviceState() {
        if (!initialized) return
        val mgr = arManager ?: return
        val state = VitureSdkState(
            imuState = mgr.getImuState(),
            stereo3dState = mgr.get3DState(),
            imuFrequency = mgr.getCurImuFrequency(),
        )
        onSdkState(state)
    }
}
