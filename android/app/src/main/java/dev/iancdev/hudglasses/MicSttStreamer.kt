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
import kotlin.math.sqrt

class MicSttStreamer(
    private val onAudioFrame: (ByteArray) -> Unit,
    private val onStats: ((MicStats) -> Unit)? = null,
) {
    data class MicStats(
        val audioSource: Int,
        val channels: Int,
        val rmsLeft: Float,
        val rmsRight: Float,
        val stereoDiffRatio: Float,
        val correlation: Float,
    )

    data class StartConfig(
        val sampleRateHz: Int,
        val frameMs: Int,
        val channels: Int,
        val audioSource: Int,
        val channelConfig: Int,
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var record: AudioRecord? = null
    private var job: Job? = null
    private var lastConfig: StartConfig? = null

    fun start(
        sampleRateHz: Int = 16000,
        frameMs: Int = 20,
        preferStereo: Boolean = true,
        preferredAudioSource: Int? = null,
    ): StartConfig? {
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
                    var lastStatsMs = 0L
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

                        val cb = onStats ?: continue
                        val now = System.currentTimeMillis()
                        if ((now - lastStatsMs) < 500) continue
                        lastStatsMs = now
                        val stats = computeStats(buf, n, channels = channels, audioSource = source) ?: continue
                        cb(stats)
                    }
                }

            return StartConfig(
                sampleRateHz = sampleRateHz,
                frameMs = frameMs,
                channels = channels,
                audioSource = source,
                channelConfig = channelConfig,
            )
        }

        val defaultSources =
            listOf(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                MediaRecorder.AudioSource.UNPROCESSED,
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                MediaRecorder.AudioSource.MIC,
                MediaRecorder.AudioSource.CAMCORDER,
                MediaRecorder.AudioSource.DEFAULT,
                MediaRecorder.AudioSource.VOICE_PERFORMANCE,
            )
        val sources =
            buildList {
                if (preferredAudioSource != null) add(preferredAudioSource)
                addAll(defaultSources)
            }.distinct()

        if (preferStereo) {
            for (s in sources) {
                val stereo = tryStart(source = s, channelConfig = AudioFormat.CHANNEL_IN_STEREO, channels = 2)
                if (stereo != null) {
                    Log.i("MicStt", "Started mic streaming stereo @ ${sampleRateHz}Hz src=$s")
                    lastConfig = stereo
                    return stereo
                }
            }
        }

        for (s in sources) {
            val mono = tryStart(source = s, channelConfig = AudioFormat.CHANNEL_IN_MONO, channels = 1)
            if (mono != null) {
                Log.i("MicStt", "Started mic streaming mono @ ${sampleRateHz}Hz src=$s")
                lastConfig = mono
                return mono
            }
        }

        Log.e("MicStt", "AudioRecord init failed (no compatible config)")
        return null
    }

    private fun computeStats(buf: ByteArray, n: Int, channels: Int, audioSource: Int): MicStats? {
        if (n <= 0) return null
        if (channels == 1) {
            val samples = n / 2
            if (samples <= 0) return null
            var sum2 = 0f
            var i = 0
            while (i + 1 < n) {
                val s = s16le(buf, i) / 32768f
                sum2 += s * s
                i += 2
            }
            val rms = sqrt(sum2 / samples)
            return MicStats(
                audioSource = audioSource,
                channels = 1,
                rmsLeft = rms,
                rmsRight = rms,
                stereoDiffRatio = 0f,
                correlation = 1f,
            )
        }
        if (channels != 2) return null
        val frames = n / 4
        if (frames <= 0) return null
        var sumL2 = 0f
        var sumR2 = 0f
        var sumLR = 0f
        var sumDiff2 = 0f
        var i = 0
        while (i + 3 < n) {
            val l = s16le(buf, i) / 32768f
            val r = s16le(buf, i + 2) / 32768f
            sumL2 += l * l
            sumR2 += r * r
            sumLR += l * r
            val d = l - r
            sumDiff2 += d * d
            i += 4
        }
        val rmsL = sqrt(sumL2 / frames)
        val rmsR = sqrt(sumR2 / frames)
        val diffRms = sqrt(sumDiff2 / frames)
        val corr = sumLR / sqrt((sumL2 * sumR2) + 1e-9f)
        val ratio = diffRms / (rmsL + rmsR + 1e-6f)
        return MicStats(
            audioSource = audioSource,
            channels = 2,
            rmsLeft = rmsL,
            rmsRight = rmsR,
            stereoDiffRatio = ratio,
            correlation = corr,
        )
    }

    private fun s16le(buf: ByteArray, idx: Int): Float {
        val lo = buf[idx].toInt() and 0xFF
        val hi = buf[idx + 1].toInt()
        val v = (hi shl 8) or lo
        return v.toShort().toInt().toFloat()
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
