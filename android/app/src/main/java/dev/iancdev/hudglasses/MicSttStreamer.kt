package dev.iancdev.hudglasses

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.max

class MicSttStreamer(
    private val onAudioFrame: (ByteArray) -> Unit,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var record: AudioRecord? = null
    private var job: Job? = null

    fun start(sampleRateHz: Int = 16000, frameMs: Int = 20) {
        if (job != null) return

        val bytesPerFrame = (sampleRateHz * frameMs / 1000) * 2
        val minBuf = AudioRecord.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = max(minBuf, bytesPerFrame * 4)

        val audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRateHz,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
            Log.e("MicStt", "AudioRecord init failed (state=${audioRecord.state})")
            runCatching { audioRecord.release() }
            return
        }
        record = audioRecord
        audioRecord.startRecording()

        job = scope.launch {
            val buf = ByteArray(bytesPerFrame)
            while (isActive) {
                val n = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    audioRecord.read(buf, 0, buf.size, AudioRecord.READ_BLOCKING)
                } else {
                    @Suppress("DEPRECATION")
                    audioRecord.read(buf, 0, buf.size)
                }
                if (n <= 0) continue
                if (n == buf.size) {
                    onAudioFrame(buf.copyOf())
                } else {
                    onAudioFrame(buf.copyOf(n))
                }
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        record?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        record = null
    }

    fun close() {
        stop()
        scope.cancel()
    }
}

