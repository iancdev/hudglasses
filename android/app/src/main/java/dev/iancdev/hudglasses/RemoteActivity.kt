package dev.iancdev.hudglasses

import android.Manifest
import android.content.Context
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
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

class RemoteActivity : ComponentActivity() {
    private var hudPresentation: HudPresentation? = null
    private lateinit var displayManager: DisplayManager
    private lateinit var wsController: WsController
    private lateinit var vitureImuController: VitureImuController
    private lateinit var hapticsController: HapticsController
    private lateinit var wristbandController: WristbandController

    private val blePermsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        val ok = granted.values.all { it }
        if (!ok) return@registerForActivityResult
        connectWristbandInternal()
    }

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = refreshHudDisplay()
        override fun onDisplayRemoved(displayId: Int) = refreshHudDisplay()
        override fun onDisplayChanged(displayId: Int) = refreshHudDisplay()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        displayManager = getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        displayManager.registerDisplayListener(displayListener, null)

        refreshHudDisplay()

        wristbandController = WristbandController(this)
        hapticsController = HapticsController(this, wristbandController)
        wsController = WsController(
            onEvents = { evt -> hapticsController.onEvent(evt) },
        )
        vitureImuController = VitureImuController(
            context = this,
            onPose = { pose ->
                wsController.sendOnEventsChannel(pose.toJson())
            },
        )
        vitureImuController.start()

        setContent {
            MaterialTheme {
                RemoteUi(
                    onConnect = { wsController.connect(HudStore.state.value.serverUrl) },
                    onDisconnect = { wsController.disconnect() },
                    onConnectWristband = { connectWristband() },
                    onDisconnectWristband = { wristbandController.disconnect() },
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

    override fun onDestroy() {
        super.onDestroy()
        displayManager.unregisterDisplayListener(displayListener)
        vitureImuController.stop()
        wsController.close()
        wristbandController.disconnect()
        hudPresentation?.dismiss()
        hudPresentation = null
    }

    private fun refreshHudDisplay() {
        val displays = displayManager.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION)
        val external = displays.firstOrNull()
        if (external == null) {
            hudPresentation?.dismiss()
            hudPresentation = null
            return
        }
        if (hudPresentation?.display?.displayId == external.displayId) {
            return
        }
        hudPresentation?.dismiss()
        hudPresentation = HudPresentation(this, external).also { it.show() }
    }

    private fun connectWristband() {
        val perms = requiredBlePermissions()
        if (perms.isEmpty()) {
            connectWristbandInternal()
            return
        }
        blePermsLauncher.launch(perms)
    }

    private fun connectWristbandInternal() {
        val state = HudStore.state.value
        val serviceUuid = runCatching { UUID.fromString(state.wristbandServiceUuid) }.getOrNull() ?: return
        val charUuid = runCatching { UUID.fromString(state.wristbandCommandCharUuid) }.getOrNull() ?: return
        wristbandController.connectByScan(state.wristbandNamePrefix, serviceUuid, charUuid)
    }

    private fun requiredBlePermissions(): Array<String> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.BLUETOOTH, Manifest.permission.BLUETOOTH_ADMIN)
        }
    }
}

@Composable
private fun RemoteUi(
    onConnect: () -> Unit,
    onDisconnect: () -> Unit,
    onConnectWristband: () -> Unit,
    onDisconnectWristband: () -> Unit,
    onApplyThresholds: (Float, Float, Float) -> Unit,
    onApplyKeywords: (String, Float) -> Unit,
) {
    val state by HudStore.state.collectAsState()
    var url by remember(state.serverUrl) { mutableStateOf(state.serverUrl) }
    var wbPrefix by remember(state.wristbandNamePrefix) { mutableStateOf(state.wristbandNamePrefix) }
    var rmsStr by remember(state.alarmRmsThreshold) { mutableStateOf(state.alarmRmsThreshold.toString()) }
    var fireStr by remember(state.fireRatioThreshold) { mutableStateOf(state.fireRatioThreshold.toString()) }
    var hornStr by remember(state.hornRatioThreshold) { mutableStateOf(state.hornRatioThreshold.toString()) }
    var keywordsCsv by remember(state.keywordsCsv) { mutableStateOf(state.keywordsCsv) }
    var keywordCooldownStr by remember(state.keywordCooldownS) { mutableStateOf(state.keywordCooldownS.toString()) }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("HUD Glasses Remote", style = MaterialTheme.typography.headlineSmall)

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
            Text("STT: ${if (state.sttConnected) "connected" else "disconnected"}")
        }

        Text("ESP32 L: ${if (state.esp32ConnectedLeft) "connected" else "missing"}")
        Text("ESP32 R: ${if (state.esp32ConnectedRight) "connected" else "missing"}")
        Text("Wristband: ${if (state.wristbandConnected) "connected" else "disconnected"}")

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = wbPrefix,
                onValueChange = { wbPrefix = it },
                label = { Text("Wristband name prefix") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Button(onClick = { HudStore.update { it.copy(wristbandNamePrefix = wbPrefix) } }) {
                Text("Set")
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = onConnectWristband) { Text("Connect Wristband") }
            Button(onClick = onDisconnectWristband) { Text("Disconnect Wristband") }
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
        Text("Lines: ${state.subtitleLines.takeLast(3).joinToString(\" | \")}")

        Text("Direction: ${"%.1f".format(state.directionDeg)}°  intensity=${"%.2f".format(state.intensity)}")
    }
}
