package dev.iancdev.hudglasses

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

object HudStore {
    private val _state = MutableStateFlow(HudState())
    val state: StateFlow<HudState> = _state

    fun update(transform: (HudState) -> HudState) {
        _state.value = transform(_state.value)
    }
}

