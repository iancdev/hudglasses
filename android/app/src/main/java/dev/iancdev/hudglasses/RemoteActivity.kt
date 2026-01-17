package dev.iancdev.hudglasses

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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

class RemoteActivity : ComponentActivity() {
    private var hudPresentation: HudPresentation? = null
    private lateinit var displayManager: DisplayManager

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

        setContent {
            MaterialTheme {
                RemoteUi()
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        displayManager.unregisterDisplayListener(displayListener)
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
}

@Composable
private fun RemoteUi() {
    val state by HudStore.state.collectAsState()
    var url by remember(state.serverUrl) { mutableStateOf(state.serverUrl) }

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
            Text("Events: ${if (state.eventsConnected) "connected" else "disconnected"}")
            Text("STT: ${if (state.sttConnected) "connected" else "disconnected"}")
        }

        Text("ESP32 L: ${if (state.esp32ConnectedLeft) "connected" else "missing"}")
        Text("ESP32 R: ${if (state.esp32ConnectedRight) "connected" else "missing"}")

        Text("Fire alarm: ${state.fireAlarm}")
        Text("Car horn: ${state.carHorn}")

        Text("Partial: ${state.subtitlePartial}")
        Text("Lines: ${state.subtitleLines.takeLast(3).joinToString(\" | \")}")

        Text(
            "Note: networking + Viture IMU integration is wired up in the next iteration.",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

