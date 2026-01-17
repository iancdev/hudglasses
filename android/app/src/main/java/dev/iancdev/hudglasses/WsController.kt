package dev.iancdev.hudglasses

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class WsController(
    private val onEvents: (HudEvent) -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .build()

    private var eventsSocket: WebSocket? = null
    private var sttSocket: WebSocket? = null

    fun connect(serverBaseUrl: String) {
        disconnect()
        val base = serverBaseUrl.removeSuffix("/")
        connectEvents("$base/events")
        connectStt("$base/stt")
    }

    fun disconnect() {
        eventsSocket?.close(1000, "bye")
        sttSocket?.close(1000, "bye")
        eventsSocket = null
        sttSocket = null
        HudStore.update { it.copy(eventsConnected = false, sttConnected = false) }
    }

    fun sendOnEventsChannel(json: JSONObject) {
        eventsSocket?.send(json.toString())
    }

    private fun connectEvents(url: String) {
        val req = Request.Builder().url(url).build()
        eventsSocket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                HudStore.update { it.copy(eventsConnected = true) }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleEventsMessage(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                HudStore.update { it.copy(eventsConnected = false) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                HudStore.update { it.copy(eventsConnected = false) }
            }
        })
    }

    private fun connectStt(url: String) {
        val req = Request.Builder().url(url).build()
        sttSocket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                HudStore.update { it.copy(sttConnected = true) }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleSttMessage(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                HudStore.update { it.copy(sttConnected = false) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                HudStore.update { it.copy(sttConnected = false) }
            }
        })
    }

    private fun handleEventsMessage(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "status" -> handleStatus(obj)
            "direction.ui" -> handleDirection(obj)
            "alarm.fire" -> handleAlarm(obj, isFire = true)
            "alarm.car_horn" -> handleAlarm(obj, isFire = false)
        }
        onEvents(HudEvent.EventsMessage(obj))
    }

    private fun handleSttMessage(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "partial" -> {
                HudStore.update { it.copy(subtitlePartial = obj.optString("text")) }
            }
            "final" -> {
                val line = obj.optString("text")
                HudStore.update {
                    it.copy(
                        subtitlePartial = "",
                        subtitleLines = (it.subtitleLines + line).takeLast(30),
                    )
                }
            }
        }
        onEvents(HudEvent.SttMessage(obj))
    }

    private fun handleStatus(obj: JSONObject) {
        val esp32 = obj.optJSONObject("esp32")
        val hasLeft = esp32?.has("left") == true
        val hasRight = esp32?.has("right") == true
        HudStore.update { it.copy(esp32ConnectedLeft = hasLeft, esp32ConnectedRight = hasRight) }
    }

    private fun handleDirection(obj: JSONObject) {
        HudStore.update {
            it.copy(
                directionDeg = obj.optDouble("directionDeg", 0.0).toFloat(),
                intensity = obj.optDouble("intensity", 0.0).toFloat(),
                radarX = obj.optDouble("radarX", 0.0).toFloat(),
                radarY = obj.optDouble("radarY", 0.0).toFloat(),
                glowEdge = obj.optString("glowEdge", "top"),
                glowStrength = obj.optDouble("glowStrength", 0.0).toFloat(),
            )
        }
    }

    private fun handleAlarm(obj: JSONObject, isFire: Boolean) {
        val state = obj.optString("state", "ongoing")
        HudStore.update {
            if (isFire) it.copy(fireAlarm = state) else it.copy(carHorn = state)
        }
    }
}

