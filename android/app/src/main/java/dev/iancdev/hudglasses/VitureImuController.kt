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
) {
    private val arManager: ArManager = ArManager.getInstance(context)
    private var started = false

    private val callback = object : ArCallback() {
        override fun onEvent(eventId: Int, event: ByteArray, ts: Long) {
            if (eventId != Constants.EVENT_ID_INIT) return
            val initResult = byteArrayToIntLittleEndian(event)
            Log.i("VitureImu", "init result=$initResult")
            if (initResult == Constants.ERROR_INIT_SUCCESS) {
                arManager.setImuOn(true)
            }
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

    fun start() {
        if (started) return
        started = true
        arManager.registerCallback(callback)
        arManager.init()
    }

    fun stop() {
        if (!started) return
        started = false
        arManager.unregisterCallback(callback)
        arManager.release()
    }

    private fun byteArrayToIntLittleEndian(bytes: ByteArray): Int {
        var result = 0
        for (i in bytes.indices) {
            result = result or ((bytes[i].toInt() and 0xFF) shl (8 * i))
        }
        return result
    }
}
