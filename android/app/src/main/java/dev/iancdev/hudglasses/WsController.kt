package dev.iancdev.hudglasses

import android.os.Build
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
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
    private var subtitleClearFuture: ScheduledFuture<*>? = null
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
        subtitleClearFuture?.cancel(false)
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

    fun sendOnSttChannel(json: JSONObject) {
        sttSocket?.send(json.toString())
    }

    fun sendSttAudioFrame(frame: ByteArray) {
        sttSocket?.send(frame.toByteString())
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
        if (targetBaseUrl == null) return
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
                webSocket.send(JSONObject().put("type", "status.request").toString())
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
            "alarm.fire" -> handleAlarm(obj, AlarmType.FIRE)
            "alarm.car_horn" -> handleAlarm(obj, AlarmType.CAR_HORN)
            "alarm.siren" -> handleAlarm(obj, AlarmType.SIREN)
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
                updateSubtitle(obj.optString("text"))
            }
            "final" -> {
                // Keep subtitles realtime and lightweight: final just updates the current subtitle text.
                updateSubtitle(obj.optString("text"))
            }
        }
        onEvents(HudEvent.SttMessage(obj))
    }

    private fun updateSubtitle(raw: String) {
        val text = trimSubtitle(raw)
        HudStore.update { it.copy(subtitlePartial = text, subtitleLines = emptyList()) }
        subtitleClearFuture?.cancel(false)
        if (text.isNotBlank()) {
            subtitleClearFuture = scheduler.schedule(
                { HudStore.update { it.copy(subtitlePartial = "", subtitleLines = emptyList()) } },
                2,
                TimeUnit.SECONDS,
            )
        }
    }

    private fun trimSubtitle(raw: String, maxWords: Int = 18): String {
        val cleaned = raw.replace('\n', ' ').trim()
        if (cleaned.isBlank()) return ""
        val parts = cleaned.split(Regex("\\s+")).filter { it.isNotBlank() }
        if (parts.size <= maxWords) return cleaned
        return parts.takeLast(maxWords).joinToString(" ")
    }

    private fun handleStatus(obj: JSONObject) {
        val esp32 = obj.optJSONObject("esp32")
        val hasLeft = esp32?.has("left") == true
        val hasRight = esp32?.has("right") == true

        val alarms = obj.optJSONObject("alarms")
        val fireActive = alarms?.optBoolean("fireActive", false) == true
        val hornActive = alarms?.optBoolean("carHornActive", false) == true
        val sirenActive = alarms?.optBoolean("sirenActive", false) == true
        HudStore.update {
            it.copy(
                serverStatus = obj.optString("server", it.serverStatus),
                esp32ConnectedLeft = hasLeft,
                esp32ConnectedRight = hasRight,
                fireAlarm = if (fireActive || sirenActive) "active" else "idle",
                carHorn = if (hornActive) "active" else "idle",
                siren = if (sirenActive) "active" else "idle",
            )
        }
    }

    private fun handleDirection(obj: JSONObject) {
        val dots =
            obj.optJSONArray("radarDots")?.let { arr ->
                buildList {
                    for (i in 0 until arr.length()) {
                        val d = arr.optJSONObject(i) ?: continue
                        add(
                            RadarDot(
                                trackId = d.optInt("trackId", 0),
                                freqHz = d.optDouble("freqHz", 0.0).toFloat(),
                                radarX = d.optDouble("radarX", 0.0).toFloat(),
                                radarY = d.optDouble("radarY", 0.0).toFloat(),
                                intensity = d.optDouble("intensity", 0.0).toFloat(),
                            )
                        )
                    }
                }
            } ?: emptyList()

        HudStore.update {
            it.copy(
                directionDeg = obj.optDouble("directionDeg", 0.0).toFloat(),
                intensity = obj.optDouble("intensity", 0.0).toFloat(),
                radarX = obj.optDouble("radarX", 0.0).toFloat(),
                radarY = obj.optDouble("radarY", 0.0).toFloat(),
                radarDots = dots,
                glowEdge = obj.optString("glowEdge", "top"),
                glowStrength = obj.optDouble("glowStrength", 0.0).toFloat(),
            )
        }
    }

    private enum class AlarmType {
        FIRE,
        CAR_HORN,
        SIREN,
    }

    private fun handleAlarm(obj: JSONObject, type: AlarmType) {
        val stateRaw = obj.optString("state", "ongoing")
        val state = when (stateRaw) {
            "started", "ongoing" -> "active"
            "ended" -> "idle"
            else -> stateRaw
        }
        HudStore.update {
            when (type) {
                AlarmType.FIRE -> it.copy(fireAlarm = state)
                AlarmType.CAR_HORN -> it.copy(carHorn = state)
                // Treat siren as "fire alarm" in the client UI (same urgency bucket).
                AlarmType.SIREN -> it.copy(fireAlarm = state, siren = state)
            }
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
