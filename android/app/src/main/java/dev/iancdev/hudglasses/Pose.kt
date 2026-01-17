package dev.iancdev.hudglasses

import org.json.JSONObject

data class Pose(
    val yaw: Float,
    val pitch: Float,
    val roll: Float,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("type", "head_pose")
        .put("yaw", yaw)
        .put("pitch", pitch)
        .put("roll", roll)
}

