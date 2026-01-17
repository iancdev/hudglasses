package dev.iancdev.hudglasses

data class HudState(
    val serverUrl: String = "ws://192.168.1.2:8765",
    val eventsConnected: Boolean = false,
    val sttConnected: Boolean = false,
    val esp32ConnectedLeft: Boolean = false,
    val esp32ConnectedRight: Boolean = false,
    val directionDeg: Float = 0f,
    val intensity: Float = 0f,
    val radarX: Float = 0f,
    val radarY: Float = 0f,
    val glowEdge: String = "top",
    val glowStrength: Float = 0f,
    val fireAlarm: String = "idle",
    val carHorn: String = "idle",
    val subtitlePartial: String = "",
    val subtitleLines: List<String> = emptyList(),
)

