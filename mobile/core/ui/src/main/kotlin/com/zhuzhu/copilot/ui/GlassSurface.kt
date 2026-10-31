package com.zhuzhu.copilot.ui

import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.hazeEffect

@Composable
fun GlassSurface(
    modifier: Modifier = Modifier,
    hazeState: HazeState,
    level: GlassLevel = GlassLevel.STANDARD,
    corner: Dp = GlassTokens.CardCorner,
    content: @Composable BoxScope.() -> Unit,
) {
    val spec = level.spec()
    val shape = RoundedCornerShape(corner)
    val rim = Brush.sweepGradient(
        listOf(
            Color.White.copy(alpha = 0.78f * spec.rimAlpha),
            Color.White.copy(alpha = 0.10f * spec.rimAlpha),
            Color(0xFF8CB4FF).copy(alpha = 0.42f * spec.rimAlpha),
            Color.White.copy(alpha = 0.06f * spec.rimAlpha),
            Color.White.copy(alpha = 0.62f * spec.rimAlpha),
            Color.White.copy(alpha = 0.78f * spec.rimAlpha),
        )
    )

    Box(
        modifier
            .clip(shape)
            .then(
                if (Build.VERSION.SDK_INT >= 31) {
                    Modifier.hazeEffect(state = hazeState) {
                        backgroundColor = Color.Transparent
                        tints = listOf(HazeTint(Color.White.copy(alpha = spec.tintAlpha)))
                        blurRadius = spec.blurDp.dp
                        noiseFactor = GlassTokens.GrainOpacity
                    }
                } else Modifier
            )
            .background(
                Brush.linearGradient(
                    listOf(
                        Color.White.copy(alpha = spec.tintAlpha),
                        Color.White.copy(alpha = 0.02f),
                        Color.White.copy(alpha = spec.tintAlpha * 0.7f),
                    )
                )
            )
            .border(width = GlassTokens.RimWidthDp.dp, brush = rim, shape = shape)
            .drawWithContent {
                drawContent()
                drawArc(
                    brush = Brush.radialGradient(
                        listOf(Color.White.copy(alpha = 0.22f), Color.Transparent),
                        center = Offset(0f, 0f),
                        radius = size.width * 0.9f,
                    ),
                    startAngle = 0f,
                    sweepAngle = 180f,
                    useCenter = false,
                    topLeft = Offset(-size.width * 0.1f, -size.height * 0.35f),
                    size = Size(size.width * 1.2f, size.height * 0.9f),
                    style = Stroke(width = size.height * 0.25f),
                )
            },
        content = content,
    )
}
