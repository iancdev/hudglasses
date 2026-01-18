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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModelStoreOwner
import androidx.savedstate.SavedStateRegistryOwner
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.roundToInt

class HudPresentation(
    context: Context,
    display: Display,
    private val owner: ComponentActivity,
) : Presentation(context, display) {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val displayContext = context.createDisplayContext(display)
        val dm = displayContext.resources.displayMetrics
        val mode = display.mode
        HudStore.update {
            it.copy(
                hudWidthPx = mode.physicalWidth,
                hudHeightPx = mode.physicalHeight,
                hudDensityDpi = dm.densityDpi,
            )
        }

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
    if (state.hudShowSplash) {
        Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
            SplashScreen()
        }
        return
    }

    val smoothedDots = rememberSmoothedRadarDots(state.radarDots)
    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        if (state.hudShowRadar) Radar(state, smoothedDots)
        AlarmAlert(state)
        if (state.hudShowSubtitles) Subtitles(state)
        KeywordAlert(state)
        if (state.hudShowGlow) EdgeGlow(state, smoothedDots)
        if (state.hudShowDebugText) StatusOverlay(state)
    }
}

@Composable
private fun SplashScreen() {
    Box(modifier = Modifier.fillMaxSize()) {
        Text(
            text = "HUDson",
            color = Color.White,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Clip,
            style = MaterialTheme.typography.displayLarge.copy(fontSize = 96.sp, fontWeight = FontWeight.Black),
            modifier = Modifier.align(Alignment.Center),
        )
    }
}

@Composable
private fun AlarmAlert(state: HudState) {
    val fireLike = state.fireAlarm != "idle" || state.siren != "idle"
    val horn = state.carHorn != "idle"
    if (!fireLike && !horn) return

    val lines = buildList {
        if (fireLike) add("FIRE ALARM" to Color.Red)
        if (horn) add("CAR HORN" to Color.Yellow)
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
            for ((text, color) in lines) {
                Text(
                    text = text,
                    color = color,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Clip,
                    style = MaterialTheme.typography.displayLarge.copy(fontSize = 88.sp, fontWeight = FontWeight.Black),
                )
            }
        }
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
            val w = state.hudWidthPx
            val h = state.hudHeightPx
            val dpi = state.hudDensityDpi
            if (w != null && h != null && dpi != null) {
                Text(
                    text = "HUD ${w}x${h} @ ${dpi}dpi",
                    color = Color(0xFF808080),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
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
private fun Radar(state: HudState, dots: List<RadarDot>) {
    Canvas(modifier = Modifier.fillMaxSize()) {
        val radius = size.minDimension * 0.12f
        val pad = 24.dp.toPx()
        val center = Offset(pad + radius, pad + radius)
        drawCircle(color = Color(0xFF2A2A2A), radius = radius, center = center)

        for (d in dots) {
            val dot = Offset(center.x + d.radarX * radius, center.y - d.radarY * radius)
            val dotColor = radarDotColor(d.freqHz)
            val alpha = (0.25f + 0.75f * d.intensity).coerceIn(0f, 1f)
            val r = 6f + (10f * d.intensity).coerceIn(0f, 10f)
            drawCircle(color = dotColor, radius = r, center = dot, alpha = alpha)
        }

        val dot = Offset(center.x + state.radarX * radius, center.y - state.radarY * radius)
        val fireLike = state.fireAlarm != "idle" || state.siren != "idle"
        val dotColor = when {
            fireLike -> Color.Red
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

    val maxLines =
        when {
            text.length >= 80 -> 4
            text.length >= 48 -> 3
            else -> 2
        }
    val style = if (maxLines >= 3) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.headlineMedium

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
                style = style,
                textAlign = TextAlign.Center,
                maxLines = maxLines,
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

private data class DotAnim(
    val freqHz: androidx.compose.animation.core.Animatable<Float, androidx.compose.animation.core.AnimationVector1D>,
    val radarX: androidx.compose.animation.core.Animatable<Float, androidx.compose.animation.core.AnimationVector1D>,
    val radarY: androidx.compose.animation.core.Animatable<Float, androidx.compose.animation.core.AnimationVector1D>,
    val intensity: androidx.compose.animation.core.Animatable<Float, androidx.compose.animation.core.AnimationVector1D>,
)

@Composable
private fun rememberSmoothedRadarDots(rawDots: List<RadarDot>): List<RadarDot> {
    val scope = rememberCoroutineScope()
    val anims = remember { mutableStateMapOf<Int, DotAnim>() }

    fun keyOf(d: RadarDot): Int = if (d.trackId != 0) d.trackId else d.freqHz.roundToInt()

    LaunchedEffect(rawDots) {
        val present = rawDots.associateBy(::keyOf)

        // Fade out tracks we didn't see this update.
        for ((k, a) in anims) {
            if (!present.containsKey(k)) {
                scope.launch {
                    a.intensity.animateTo(
                        targetValue = 0f,
                        animationSpec = androidx.compose.animation.core.tween(durationMillis = 280),
                    )
                    if (a.intensity.value <= 0.01f) {
                        anims.remove(k)
                    }
                }
            }
        }

        for ((k, d) in present) {
            val a =
                anims.getOrPut(k) {
                    DotAnim(
                        freqHz = androidx.compose.animation.core.Animatable(d.freqHz),
                        radarX = androidx.compose.animation.core.Animatable(d.radarX),
                        radarY = androidx.compose.animation.core.Animatable(d.radarY),
                        intensity = androidx.compose.animation.core.Animatable(d.intensity),
                    )
                }

            // Blend towards the new sample so we "average over the last few" updates,
            // then animate for interpolation.
            val blendedX = 0.65f * a.radarX.value + 0.35f * d.radarX
            val blendedY = 0.65f * a.radarY.value + 0.35f * d.radarY
            val blendedI = 0.60f * a.intensity.value + 0.40f * d.intensity
            val blendedF = 0.85f * a.freqHz.value + 0.15f * d.freqHz

            scope.launch {
                a.freqHz.animateTo(
                    targetValue = blendedF,
                    animationSpec = androidx.compose.animation.core.tween(durationMillis = 240),
                )
            }
            scope.launch {
                a.radarX.animateTo(
                    targetValue = blendedX,
                    animationSpec = androidx.compose.animation.core.tween(durationMillis = 220),
                )
            }
            scope.launch {
                a.radarY.animateTo(
                    targetValue = blendedY,
                    animationSpec = androidx.compose.animation.core.tween(durationMillis = 220),
                )
            }
            scope.launch {
                a.intensity.animateTo(
                    targetValue = blendedI.coerceIn(0f, 1f),
                    animationSpec = androidx.compose.animation.core.tween(durationMillis = 180),
                )
            }
        }
    }

    return anims.entries
        .map { (k, a) ->
            RadarDot(
                trackId = k,
                freqHz = a.freqHz.value,
                radarX = a.radarX.value,
                radarY = a.radarY.value,
                intensity = a.intensity.value,
            )
        }
        .filter { it.intensity > 0.02f }
        .sortedByDescending { it.intensity }
}

@Composable
private fun EdgeGlow(state: HudState, dots: List<RadarDot>) {
    val thickness: Dp = 100.dp

    // Alarm overlays keep the old full-edge glow.
    val fireLike = state.fireAlarm != "idle" || state.siren != "idle"
    val alarmGlow: Color? =
        when {
            fireLike -> Color.Red
            state.carHorn != "idle" -> Color.Yellow
            else -> null
        }

    Canvas(modifier = Modifier.fillMaxSize()) {
        val w = size.width
        val h = size.height
        val t = thickness.toPx()

        fun yFromRadarY(radarY: Float): Float = ((1f - radarY.coerceIn(-1f, 1f)) * 0.5f) * h
        fun xFromRadarX(radarX: Float): Float = ((radarX.coerceIn(-1f, 1f) + 1f) * 0.5f) * w

        fun drawDirectionalGlowAtEdge(
            center: Offset,
            edge: String,
            color: Color,
            intensity: Float,
        ) {
            val i = intensity.coerceIn(0f, 1f)
            if (i <= 0.01f) return

            // Glow a bit more overall, but keep it smaller and make the falloff smoother.
            val alpha = (0.16f + 0.62f * i).coerceIn(0f, 1f)
            val c0 = color.copy(alpha = alpha * 1.00f)
            val c1 = color.copy(alpha = alpha * 0.72f)
            val c2 = color.copy(alpha = alpha * 0.50f)
            val c3 = color.copy(alpha = alpha * 0.34f)
            val c4 = color.copy(alpha = alpha * 0.22f)

            // Keep the glow confined to the edge band (thickness = t) and ensure it fades out
            // before reaching the inner edge, so it never looks "cut off".
            val band = t
            val fadePx = band * 0.98f
            // Larger radius => wider glow spread along the edge. The fadeStop below keeps the
            // glow confined to the edge band so it doesn't "reach inward" more than before.
            val radius = (band * (7.0f + 14.0f * i)).coerceAtLeast(band * 5.0f)
            val fadeStop = (fadePx / radius).coerceIn(0.05f, 0.98f)
            val brush =
                Brush.radialGradient(
                    colorStops =
                        arrayOf(
                            0.00f to c0,
                            (fadeStop * 0.10f) to c1,
                            (fadeStop * 0.28f) to c2,
                            (fadeStop * 0.52f) to c3,
                            (fadeStop * 0.78f) to c4,
                            fadeStop to Color.Transparent,
                            1.00f to Color.Transparent,
                        ),
                    center = center,
                    radius = radius,
                )

            val ovalScale = (1.8f + 0.7f * i).coerceIn(1.8f, 2.5f)
            val scaleX = if (edge == "top" || edge == "bottom") ovalScale else 1f
            val scaleY = if (edge == "left" || edge == "right") ovalScale else 1f

            when (edge) {
                "left" ->
                    withTransform({ scale(scaleX = scaleX, scaleY = scaleY, pivot = center) }) {
                        drawRect(brush = brush, topLeft = Offset(0f, 0f), size = androidx.compose.ui.geometry.Size(t, h))
                    }
                "right" ->
                    withTransform({ scale(scaleX = scaleX, scaleY = scaleY, pivot = center) }) {
                        drawRect(
                            brush = brush,
                            topLeft = Offset(w - t, 0f),
                            size = androidx.compose.ui.geometry.Size(t, h),
                        )
                    }
                "bottom" ->
                    withTransform({ scale(scaleX = scaleX, scaleY = scaleY, pivot = center) }) {
                        drawRect(
                            brush = brush,
                            topLeft = Offset(0f, h - t),
                            size = androidx.compose.ui.geometry.Size(w, t),
                        )
                    }
                else ->
                    withTransform({ scale(scaleX = scaleX, scaleY = scaleY, pivot = center) }) {
                        drawRect(brush = brush, topLeft = Offset(0f, 0f), size = androidx.compose.ui.geometry.Size(w, t))
                    }
            }
        }

        // New: directional, edge-compressed multi-source glow (color by frequency).
        for (d in dots) {
            val ax = abs(d.radarX)
            val ay = abs(d.radarY)
            val edge =
                if (ay >= ax) {
                    if (d.radarY >= 0f) "top" else "bottom"
                } else {
                    if (d.radarX <= 0f) "left" else "right"
                }

            val center =
                when (edge) {
                    "left" -> Offset(0f, yFromRadarY(d.radarY))
                    "right" -> Offset(w, yFromRadarY(d.radarY))
                    "bottom" -> Offset(xFromRadarX(d.radarX), h)
                    else -> Offset(xFromRadarX(d.radarX), 0f)
                }

            val color = radarDotColor(d.freqHz)
            drawDirectionalGlowAtEdge(center = center, edge = edge, color = color, intensity = d.intensity)
        }

        // Fallback: if no radar sources, use the server-provided single-edge glow.
        if (dots.isEmpty()) {
            val alpha = state.glowStrength.coerceIn(0f, 1f) * 0.85f
            if (alpha > 0f) {
                val c = Color.White.copy(alpha = alpha)
                val edge = state.glowEdge
                val center =
                    when (edge) {
                        "left" -> Offset(0f, h * 0.5f)
                        "right" -> Offset(w, h * 0.5f)
                        "bottom" -> Offset(w * 0.5f, h)
                        else -> Offset(w * 0.5f, 0f)
                    }
                drawDirectionalGlowAtEdge(center = center, edge = edge, color = c, intensity = 1f)
            }
        }

        // Alarm overlay last (so it's always visible).
        if (alarmGlow != null) {
            val alpha = 0.82f
            val c = alarmGlow.copy(alpha = alpha)
            val band = t
            val radius = t * 19.0f
            val fadePx = band * 0.98f
            val fadeStop = (fadePx / radius).coerceIn(0.05f, 0.98f)
            val brush =
                Brush.radialGradient(
                    colorStops =
                        arrayOf(
                            0.00f to c.copy(alpha = alpha * 1.00f),
                            (fadeStop * 0.10f) to c.copy(alpha = alpha * 0.72f),
                            (fadeStop * 0.28f) to c.copy(alpha = alpha * 0.50f),
                            (fadeStop * 0.52f) to c.copy(alpha = alpha * 0.34f),
                            (fadeStop * 0.78f) to c.copy(alpha = alpha * 0.22f),
                            fadeStop to Color.Transparent,
                            1.00f to Color.Transparent,
                        ),
                    center = Offset(w * 0.5f, 0f),
                    radius = radius,
                )
            val center = Offset(w * 0.5f, 0f)
            withTransform({ scale(scaleX = 2.2f, scaleY = 1f, pivot = center) }) {
                drawRect(brush = brush, topLeft = Offset(0f, 0f), size = androidx.compose.ui.geometry.Size(w, t))
            }
        }
    }
}
