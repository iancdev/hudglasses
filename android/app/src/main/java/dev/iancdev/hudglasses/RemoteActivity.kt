package dev.iancdev.hudglasses

import android.Manifest
import android.content.Context
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Surface
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject

class RemoteActivity : ComponentActivity() {
    private var hudPresentation: HudPresentation? = null
    private lateinit var displayManager: DisplayManager
    private lateinit var wsController: WsController
    private lateinit var vitureImuController: VitureImuController
    private lateinit var hapticsController: HapticsController
    private lateinit var phoneAudioStreamer: MicSttStreamer

    private var phoneAudioRequested: Boolean = false

    private val micPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!phoneAudioRequested) return@registerForActivityResult
        phoneAudioRequested = false
        if (granted) {
            enablePhoneAudioFallbackInternal()
        } else {
            HudStore.update { it.copy(phoneAudioFallbackEnabled = false) }
        }
    }

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = runOnUiThread { refreshHudDisplay() }
        override fun onDisplayRemoved(displayId: Int) = runOnUiThread { refreshHudDisplay() }
        override fun onDisplayChanged(displayId: Int) = runOnUiThread { refreshHudDisplay() }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        displayManager = getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        displayManager.registerDisplayListener(displayListener, null)

        refreshHudDisplay()

        hapticsController = HapticsController(this)
        wsController = WsController(
            onEvents = { evt -> hapticsController.onEvent(evt) },
        )
        phoneAudioStreamer = MicSttStreamer(
            onAudioFrame = { frame -> wsController.sendSttAudioFrame(frame) },
        )
        vitureImuController = VitureImuController(
            context = this,
            onPose = { pose ->
                wsController.sendOnEventsChannel(pose.toJson())
            },
            onSdkState = { sdk ->
                runOnUiThread {
                    HudStore.update {
                        it.copy(
                            vitureInitResult = sdk.initResult ?: it.vitureInitResult,
                            vitureImuState = sdk.imuState ?: it.vitureImuState,
                            viture3dState = sdk.stereo3dState ?: it.viture3dState,
                            vitureImuFrequency = sdk.imuFrequency ?: it.vitureImuFrequency,
                        )
                    }
                }
            },
        )

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    RemoteUi(
                        onConnect = { wsController.connect(HudStore.state.value.serverUrl) },
                        onDisconnect = { wsController.disconnect() },
                        onSetPhoneAudioFallbackEnabled = { enabled -> setPhoneAudioFallbackEnabled(enabled) },
                        onSetVitureImu = { enabled -> vitureImuController.setImuEnabled(enabled) },
                        onSetViture3d = { enabled -> vitureImuController.set3dEnabled(enabled) },
                        onSetVitureImuFreq = { mode -> vitureImuController.setImuFrequency(mode) },
                        onApplyVitureHudDefaults = {
                            vitureImuController.set3dEnabled(false)
                            vitureImuController.setImuEnabled(true)
                            vitureImuController.setImuFrequency(0) // 60Hz
                        },
                        onApplyThresholds = { rms, fire, horn ->
                            HudStore.update { it.copy(alarmRmsThreshold = rms, fireRatioThreshold = fire, hornRatioThreshold = horn) }
                            wsController.sendOnEventsChannel(
                                JSONObject()
                                    .put("type", "config.update")
                                    .put("alarmRmsThreshold", rms)
                                    .put("fireRatioThreshold", fire)
                                    .put("hornRatioThreshold", horn)
                            )
                        },
                        onApplyKeywords = { keywordsCsv, cooldownS ->
                            HudStore.update { it.copy(keywordsCsv = keywordsCsv, keywordCooldownS = cooldownS) }
                            val keywords = keywordsCsv
                                .split(",", "\n")
                                .map { it.trim() }
                                .filter { it.isNotEmpty() }
                                .take(50)
                            wsController.sendOnEventsChannel(
                                JSONObject()
                                    .put("type", "config.update")
                                    .put("keywordCooldownS", cooldownS)
                                    .put("keywords", JSONArray(keywords))
                            )
                        },
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        vitureImuController.start()
    }

    override fun onPause() {
        vitureImuController.stop()
        super.onPause()
    }

    override fun onDestroy() {
        super.onDestroy()
        displayManager.unregisterDisplayListener(displayListener)
        vitureImuController.stop()
        vitureImuController.release()
        wsController.close()
        phoneAudioStreamer.close()
        hudPresentation?.dismiss()
        hudPresentation = null
    }

    private fun refreshHudDisplay() {
        val displays = displayManager.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION)
        val external = displays.firstOrNull()
        HudStore.update { it.copy(glassesConnected = external != null) }
        if (external == null) {
            hudPresentation?.dismiss()
            hudPresentation = null
            return
        }
        if (hudPresentation?.display?.displayId == external.displayId) {
            return
        }
        hudPresentation?.dismiss()
        hudPresentation = HudPresentation(this, external, owner = this).also { it.show() }
    }

    private fun setPhoneAudioFallbackEnabled(enabled: Boolean) {
        if (enabled) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                enablePhoneAudioFallbackInternal()
                return
            }
            phoneAudioRequested = true
            micPermLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }

        phoneAudioRequested = false
        phoneAudioStreamer.stop()
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        HudStore.update { it.copy(phoneAudioFallbackEnabled = false) }
        wsController.sendOnEventsChannel(
            JSONObject()
                .put("type", "audio.source")
                .put("source", "auto")
        )
    }

    private fun enablePhoneAudioFallbackInternal() {
        val sampleRateHz = 16000
        val frameMs = 20
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE

        val started = phoneAudioStreamer.start(sampleRateHz = sampleRateHz, frameMs = frameMs, preferStereo = true)
        if (started == null) {
            HudStore.update { it.copy(phoneAudioFallbackEnabled = false, sttError = "Failed to start phone mic") }
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            return
        }
        HudStore.update { it.copy(phoneAudioFallbackEnabled = true, sttError = "") }

        wsController.sendOnEventsChannel(
            JSONObject()
                .put("type", "audio.source")
                .put("source", "auto")
        )

        wsController.sendOnSttChannel(
            JSONObject()
                .put("type", "audio.hello")
                .put("v", 1)
                .put("deviceId", Build.MODEL)
                .put(
                    "audio",
                    JSONObject()
                        .put("format", "pcm_s16le")
                        .put("sampleRateHz", started.sampleRateHz)
                        .put("channels", started.channels)
                        .put("frameMs", started.frameMs),
                )
        )
    }
}

@Composable
private fun RemoteUi(
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onSetPhoneAudioFallbackEnabled: (Boolean) -> Unit,
    onSetVitureImu: (Boolean) -> Unit,
    onSetViture3d: (Boolean) -> Unit,
    onSetVitureImuFreq: (Int) -> Unit,
    onApplyVitureHudDefaults: () -> Unit,
    onApplyThresholds: (Float, Float, Float) -> Unit,
    onApplyKeywords: (String, Float) -> Unit,
) {
    val state by HudStore.state.collectAsState()
    var url by remember(state.serverUrl) { mutableStateOf(state.serverUrl) }
    var rmsStr by remember(state.alarmRmsThreshold) { mutableStateOf(state.alarmRmsThreshold.toString()) }
    var fireStr by remember(state.fireRatioThreshold) { mutableStateOf(state.fireRatioThreshold.toString()) }
    var hornStr by remember(state.hornRatioThreshold) { mutableStateOf(state.hornRatioThreshold.toString()) }
    var keywordsCsv by remember(state.keywordsCsv) { mutableStateOf(state.keywordsCsv) }
    var keywordCooldownStr by remember(state.keywordCooldownS) { mutableStateOf(state.keywordCooldownS.toString()) }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("HUD Glasses Remote", style = MaterialTheme.typography.headlineSmall)

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Phone Audio Fallback Mode")
            Switch(
                checked = state.phoneAudioFallbackEnabled,
                onCheckedChange = onSetPhoneAudioFallbackEnabled,
            )
        }

        Text(
            "Viture: init=${vitureInitLabel(state.vitureInitResult)} " +
                "imu=${vitureStateLabel(state.vitureImuState)} " +
                "3d=${vitureStateLabel(state.viture3dState)} " +
                "freq=${vitureImuFrequencyLabel(state.vitureImuFrequency)}"
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("IMU")
            Switch(
                checked = state.vitureImuState == 1,
                onCheckedChange = onSetVitureImu,
            )
            Text("3D")
            Switch(
                checked = state.viture3dState == 1,
                onCheckedChange = onSetViture3d,
            )
            Button(onClick = onApplyVitureHudDefaults) { Text("HUD Defaults") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("IMU Hz")
            Button(onClick = { onSetVitureImuFreq(0) }) { Text("60") }
            Button(onClick = { onSetVitureImuFreq(1) }) { Text("90") }
            Button(onClick = { onSetVitureImuFreq(2) }) { Text("120") }
            Button(onClick = { onSetVitureImuFreq(3) }) { Text("240") }
        }

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Server URL (ws://host:port)") },
            singleLine = true,
        )

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = { HudStore.update { it.copy(serverUrl = url) } }) {
                Text("Set URL")
            }
            Button(onClick = onConnect) {
                Text("Connect")
            }
            Button(onClick = onDisconnect) {
                Text("Disconnect")
            }
            Text("Events: ${if (state.eventsConnected) "connected" else "disconnected"}")
            Text("STT: ${state.sttStatus.ifBlank { if (state.sttConnected) "connected" else "disconnected" }}")
        }

        if (!state.glassesConnected) {
            Text("Glasses: disconnected (no external display)")
        } else {
            Text("Glasses: connected")
        }
        Text("ESP32 L: ${if (state.esp32ConnectedLeft) "connected" else "missing"}")
        Text("ESP32 R: ${if (state.esp32ConnectedRight) "connected" else "missing"}")
        Text("Wristband (ESP-NOW bridge): ${if (state.wristbandConnected) "connected" else "disconnected"}")
        Text("Phone vibration fallback: enabled")
        if (state.serverStatus.isNotBlank()) {
            Text("Server: ${state.serverStatus}")
        }
        if (state.sttError.isNotBlank()) {
            Text("STT error: ${state.sttError}")
        }

        Text("Alarm thresholds (server tuning)")
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = rmsStr,
                onValueChange = { rmsStr = it },
                label = { Text("RMS") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = fireStr,
                onValueChange = { fireStr = it },
                label = { Text("Fire ratio") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = hornStr,
                onValueChange = { hornStr = it },
                label = { Text("Horn ratio") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        Button(
            onClick = {
                val rms = rmsStr.toFloatOrNull() ?: state.alarmRmsThreshold
                val fire = fireStr.toFloatOrNull() ?: state.fireRatioThreshold
                val horn = hornStr.toFloatOrNull() ?: state.hornRatioThreshold
                onApplyThresholds(rms, fire, horn)
            }
        ) {
            Text("Apply Thresholds")
        }

        Text("Keywords / phrases (Phase 2)")
        OutlinedTextField(
            value = keywordsCsv,
            onValueChange = { keywordsCsv = it },
            label = { Text("Comma-separated phrases") },
            singleLine = false,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = keywordCooldownStr,
                onValueChange = { keywordCooldownStr = it },
                label = { Text("Cooldown (s)") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(
                onClick = {
                    val cd = keywordCooldownStr.toFloatOrNull() ?: state.keywordCooldownS
                    onApplyKeywords(keywordsCsv, cd)
                },
            ) {
                Text("Apply Keywords")
            }
        }

        Text("Fire alarm: ${state.fireAlarm}")
        Text("Car horn: ${state.carHorn}")
        if (state.keywordAlert.isNotBlank()) {
            Text("Keyword alert: ${state.keywordAlert}")
        }

        Text("Partial: ${state.subtitlePartial}")
        Text("Lines: ${state.subtitleLines.takeLast(3).joinToString(" | ")}")

        Text("Direction: ${"%.1f".format(state.directionDeg)}°  intensity=${"%.2f".format(state.intensity)}")
    }
}

private fun vitureInitLabel(code: Int?): String {
    return when (code) {
        null -> "unknown"
        0 -> "success"
        -1 -> "no_device"
        -2 -> "no_permission"
        -3 -> "unknown_error"
        else -> "code_$code"
    }
}

private fun vitureStateLabel(code: Int?): String {
    return when (code) {
        null -> "unknown"
        1 -> "on"
        0 -> "off"
        else -> "err_$code"
    }
}

private fun vitureImuFrequencyLabel(code: Int?): String {
    return when (code) {
        null -> "unknown"
        0 -> "60hz"
        1 -> "90hz"
        2 -> "120hz"
        3 -> "240hz"
        else -> "code_$code"
    }
}
