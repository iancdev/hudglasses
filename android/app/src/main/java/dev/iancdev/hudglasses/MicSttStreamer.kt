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
    data class StartConfig(
        val sampleRateHz: Int,
        val frameMs: Int,
        val channels: Int,
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var record: AudioRecord? = null
    private var job: Job? = null
    private var lastConfig: StartConfig? = null

    fun start(sampleRateHz: Int = 16000, frameMs: Int = 20, preferStereo: Boolean = true): StartConfig? {
        if (job != null) return lastConfig

        fun tryStart(source: Int, channelConfig: Int, channels: Int): StartConfig? {
            val bytesPerFrame = (sampleRateHz * frameMs / 1000) * 2 * channels
            val minBuf = AudioRecord.getMinBufferSize(
                sampleRateHz,
                channelConfig,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            val bufferSize = max(minBuf, bytesPerFrame * 4)

            val audioRecord =
                AudioRecord(
                    source,
                    sampleRateHz,
                    channelConfig,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize,
                )
            if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
                runCatching { audioRecord.release() }
                return null
            }

            record = audioRecord
            audioRecord.startRecording()

            job =
                scope.launch {
                    val buf = ByteArray(bytesPerFrame)
                    while (isActive) {
                        val n =
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
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

            return StartConfig(sampleRateHz = sampleRateHz, frameMs = frameMs, channels = channels)
        }

        val stereoConfig =
            if (preferStereo) {
                tryStart(
                    source = MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    channelConfig = AudioFormat.CHANNEL_IN_STEREO,
                    channels = 2,
                )
                    ?: tryStart(
                        source = MediaRecorder.AudioSource.MIC,
                        channelConfig = AudioFormat.CHANNEL_IN_STEREO,
                        channels = 2,
                    )
            } else {
                null
            }
        if (stereoConfig != null) {
            Log.i("MicStt", "Started mic streaming stereo @ ${sampleRateHz}Hz")
            lastConfig = stereoConfig
            return stereoConfig
        }

        val monoConfig =
            tryStart(
                source = MediaRecorder.AudioSource.VOICE_RECOGNITION,
                channelConfig = AudioFormat.CHANNEL_IN_MONO,
                channels = 1,
            )
        if (monoConfig != null) {
            Log.i("MicStt", "Started mic streaming mono @ ${sampleRateHz}Hz")
            lastConfig = monoConfig
            return monoConfig
        }

        Log.e("MicStt", "AudioRecord init failed (no compatible config)")
        return null
    }

    fun stop() {
        job?.cancel()
        job = null
        record?.let {
            runCatching { it.stop() }
            runCatching { it.release() }
        }
        record = null
        lastConfig = null
    }

    fun close() {
        stop()
        scope.cancel()
    }
}
