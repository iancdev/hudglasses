package dev.iancdev.hudglasses

import org.json.JSONObject

sealed class HudEvent {
    data class EventsMessage(val json: JSONObject) : HudEvent()
    data class SttMessage(val json: JSONObject) : HudEvent()
}

