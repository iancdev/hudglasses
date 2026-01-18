package dev.iancdev.hudglasses

import android.app.Presentation
import android.content.Context
import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.view.Display
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import androidx.activity.ComponentActivity
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModelStoreOwner
import androidx.savedstate.SavedStateRegistryOwner

class HudPresentation(
    context: Context,
    display: Display,
    private val owner: ComponentActivity,
) : Presentation(context, display) {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val compose = ComposeView(context).apply {
            // These ViewTree* helpers are not in the public API surface on some builds,
            // so we invoke them reflectively to keep the HUD-on-external-display stable.
            setViewTreeOwnersReflective(this, owner)
            setContent { HudUi() }
        }
        setContentView(compose, android.view.ViewGroup.LayoutParams(MATCH_PARENT, MATCH_PARENT))
    }
}

private fun setViewTreeOwnersReflective(view: View, owner: ComponentActivity) {
    runCatching {
        val clazz = Class.forName("androidx.lifecycle.ViewTreeLifecycleOwner")
        val method = clazz.getMethod("set", View::class.java, LifecycleOwner::class.java)
        method.invoke(null, view, owner)
    }
    runCatching {
        val clazz = Class.forName("androidx.savedstate.ViewTreeSavedStateRegistryOwner")
        val method = clazz.getMethod("set", View::class.java, SavedStateRegistryOwner::class.java)
        method.invoke(null, view, owner)
    }
    runCatching {
        val clazz = Class.forName("androidx.lifecycle.ViewTreeViewModelStoreOwner")
        val method = clazz.getMethod("set", View::class.java, ViewModelStoreOwner::class.java)
        method.invoke(null, view, owner)
    }
}

@Composable
private fun HudUi() {
    val state by HudStore.state.collectAsState()
    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        Radar(state)
        Subtitles(state)
        KeywordAlert(state)
        EdgeGlow(state)
        StatusOverlay(state)
    }
}

@Composable
private fun StatusOverlay(state: HudState) {
    val line = buildString {
        append(if (state.eventsConnected) "EVT✓" else "EVT×")
        append("  ")
        append(if (state.sttConnected) "STT✓" else "STT×")
        if (state.sttStatus.isNotBlank()) {
            append("(${state.sttStatus})")
        }
        append("  ")
        append(if (state.esp32ConnectedLeft) "L✓" else "L×")
        append(if (state.esp32ConnectedRight) " R✓" else " R×")
        append("  ")
        append(if (state.wristbandConnected) "WB✓" else "WB×")
    }

    Box(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Column(modifier = Modifier.align(Alignment.TopStart)) {
            Text(
                text = line,
                color = Color(0xFFB0B0B0),
                style = MaterialTheme.typography.bodyLarge,
            )
            if (state.sttError.isNotBlank()) {
                Text(
                    text = "STT error: ${state.sttError}",
                    color = Color.Red,
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
        }
    }
}

@Composable
private fun Radar(state: HudState) {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val center = Offset(size.width * 0.5f, size.height * 0.35f)
        val radius = size.minDimension * 0.12f
        drawCircle(color = Color(0xFF2A2A2A), radius = radius, center = center)

        for (d in state.radarDots) {
            val dot = Offset(center.x + d.radarX * radius, center.y - d.radarY * radius)
            val dotColor = radarDotColor(d.freqHz)
            val alpha = (0.25f + 0.75f * d.intensity).coerceIn(0f, 1f)
            val r = 6f + (10f * d.intensity).coerceIn(0f, 10f)
            drawCircle(color = dotColor, radius = r, center = dot, alpha = alpha)
        }

        val dot = Offset(center.x + state.radarX * radius, center.y - state.radarY * radius)
        val dotColor = when {
            state.fireAlarm != "idle" -> Color.Red
            state.carHorn != "idle" -> Color.Yellow
            else -> Color.White
        }
        drawCircle(color = dotColor, radius = 10f, center = dot, alpha = 0.95f)
    }
}

private fun radarDotColor(freqHz: Float): Color {
    val t = ((freqHz - 200f) / (4000f - 200f)).coerceIn(0f, 1f)
    val hue = 240f * (1f - t) // blue -> red
    val argb = AndroidColor.HSVToColor(floatArrayOf(hue, 0.85f, 1f))
    return Color(argb)
}

@Composable
private fun Subtitles(state: HudState) {
    val text = state.subtitlePartial.trim()

    if (text.isBlank()) return

    BoxWithConstraints(modifier = Modifier.fillMaxSize().padding(24.dp)) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.TopCenter)
                .padding(top = maxHeight * 0.6f)
                .background(Color(0xA0000000))
                .padding(14.dp)
        ) {
            Text(
                text = text,
                color = Color.White,
                style = MaterialTheme.typography.headlineMedium,
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun KeywordAlert(state: HudState) {
    if (state.keywordAlert.isBlank()) return
    Box(modifier = Modifier.fillMaxSize().padding(24.dp)) {
        Text(
            text = "KEYWORD: ${state.keywordAlert}",
            color = Color.Red,
            style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.align(Alignment.TopCenter),
        )
    }
}

@Composable
private fun EdgeGlow(state: HudState) {
    val glow = when {
        state.fireAlarm != "idle" -> Color.Red
        state.carHorn != "idle" -> Color.Yellow
        else -> Color.White
    }
    val alpha = state.glowStrength.coerceIn(0f, 1f) * 0.85f
    if (alpha <= 0f) return

    val thickness: Dp = 90.dp
    val c = glow.copy(alpha = alpha)
    Box(modifier = Modifier.fillMaxSize()) {
        when (state.glowEdge) {
            "left" -> {
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .width(thickness)
                        .align(Alignment.CenterStart)
                        .background(
                            Brush.horizontalGradient(
                                colors = listOf(c, Color.Transparent),
                            )
                        )
                )
            }

            "right" -> {
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .width(thickness)
                        .align(Alignment.CenterEnd)
                        .background(
                            Brush.horizontalGradient(
                                colors = listOf(Color.Transparent, c),
                            )
                        )
                )
            }

            "bottom" -> {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(thickness)
                        .align(Alignment.BottomCenter)
                        .background(
                            Brush.verticalGradient(
                                colors = listOf(Color.Transparent, c),
                            )
                        )
                )
            }

            else -> { // top
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(thickness)
                        .align(Alignment.TopCenter)
                        .background(
                            Brush.verticalGradient(
                                colors = listOf(c, Color.Transparent),
                            )
                        )
                )
            }
        }
    }
}
