package dev.iancdev.hudglasses

import android.os.Build
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import kotlin.math.min

class WsController(
    private val onEvents: (HudEvent) -> Unit,
) {
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .build()

    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private var reconnectFuture: ScheduledFuture<*>? = null
    private var keywordClearFuture: ScheduledFuture<*>? = null
    private var reconnectAttempts: Int = 0
    @Volatile private var shouldConnect: Boolean = false
    @Volatile private var targetBaseUrl: String? = null

    private var eventsSocket: WebSocket? = null
    private var sttSocket: WebSocket? = null
    @Volatile private var eventsOpen: Boolean = false
    @Volatile private var sttOpen: Boolean = false

    fun connect(serverBaseUrl: String) {
        targetBaseUrl = serverBaseUrl.removeSuffix("/")
        shouldConnect = true
        reconnectAttempts = 0
        reconnectFuture?.cancel(false)
        closeSockets()
        openSockets()
    }

    fun disconnect() {
        shouldConnect = false
        targetBaseUrl = null
        reconnectAttempts = 0
        reconnectFuture?.cancel(false)
        keywordClearFuture?.cancel(false)
        closeSockets()
        HudStore.update { it.copy(eventsConnected = false, sttConnected = false) }
    }

    fun close() {
        disconnect()
        scheduler.shutdownNow()
    }

    fun sendOnEventsChannel(json: JSONObject) {
        eventsSocket?.send(json.toString())
    }

    private fun openSockets() {
        val base = targetBaseUrl ?: return
        connectEvents("$base/events")
        connectStt("$base/stt")
    }

    private fun closeSockets() {
        eventsSocket?.close(1000, "bye")
        sttSocket?.close(1000, "bye")
        eventsSocket = null
        sttSocket = null
        eventsOpen = false
        sttOpen = false
    }

    private fun scheduleReconnect() {
        val base = targetBaseUrl ?: return
        if (!shouldConnect) return
        if (reconnectFuture?.isDone == false) return

        val delayMs = min(5000L, 500L * (1L shl min(reconnectAttempts, 4)))
        reconnectAttempts += 1
        reconnectFuture = scheduler.schedule(
            {
                if (!shouldConnect) return@schedule
                closeSockets()
                openSockets()
            },
            delayMs,
            TimeUnit.MILLISECONDS,
        )
        HudStore.update { it.copy(eventsConnected = false, sttConnected = false) }
    }

    private fun onMaybeFullyConnected() {
        if (eventsOpen && sttOpen) {
            reconnectAttempts = 0
            reconnectFuture?.cancel(false)
        }
    }

    private fun connectEvents(url: String) {
        val req = Request.Builder().url(url).build()
        eventsSocket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                eventsOpen = true
                HudStore.update { it.copy(eventsConnected = true) }
                val hello =
                    JSONObject()
                        .put("type", "hello")
                        .put("v", 1)
                        .put("client", "android")
                        .put("model", Build.MODEL)
                        .put("sdkInt", Build.VERSION.SDK_INT)
                webSocket.send(hello.toString())
                onMaybeFullyConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleEventsMessage(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                eventsOpen = false
                HudStore.update { it.copy(eventsConnected = false) }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                eventsOpen = false
                HudStore.update { it.copy(eventsConnected = false) }
                scheduleReconnect()
            }
        })
    }

    private fun connectStt(url: String) {
        val req = Request.Builder().url(url).build()
        sttSocket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                sttOpen = true
                HudStore.update { it.copy(sttConnected = true) }
                onMaybeFullyConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleSttMessage(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                sttOpen = false
                HudStore.update { it.copy(sttConnected = false) }
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                sttOpen = false
                HudStore.update { it.copy(sttConnected = false) }
                scheduleReconnect()
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
            "alert.keyword" -> handleKeyword(obj)
        }
        onEvents(HudEvent.EventsMessage(obj))
    }

    private fun handleSttMessage(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "status" -> {
                val stt = obj.optString("stt")
                if (stt.isNotBlank()) {
                    HudStore.update { it.copy(sttStatus = stt, sttError = "") }
                }
            }
            "error" -> {
                val msg = obj.optString("message", obj.optString("error"))
                HudStore.update { it.copy(sttStatus = "error", sttError = msg) }
            }
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
        HudStore.update {
            it.copy(
                serverStatus = obj.optString("server", it.serverStatus),
                esp32ConnectedLeft = hasLeft,
                esp32ConnectedRight = hasRight,
            )
        }
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
        val stateRaw = obj.optString("state", "ongoing")
        val state = when (stateRaw) {
            "started", "ongoing" -> "active"
            "ended" -> "idle"
            else -> stateRaw
        }
        HudStore.update {
            if (isFire) it.copy(fireAlarm = state) else it.copy(carHorn = state)
        }
    }

    private fun handleKeyword(obj: JSONObject) {
        val kw = obj.optString("keyword", "")
        if (kw.isBlank()) return
        HudStore.update { it.copy(keywordAlert = kw) }
        keywordClearFuture?.cancel(false)
        keywordClearFuture = scheduler.schedule(
            { HudStore.update { it.copy(keywordAlert = "") } },
            3,
            TimeUnit.SECONDS,
        )
    }
}
