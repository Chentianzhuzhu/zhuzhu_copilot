package com.zhuzhu.copilot.ui

import android.os.Build
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
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

/**
 * 液态玻璃：
 * - 高斯模糊 + 白色微 tint + 颗粒 = 实时磨砂
 * - 环形光带 + 顶部镜面 = 边缘高光
 * - 顶部/底部流动光段 + 斜向扫光 = 实时液态感
 *
 * 模糊只能采样同窗口内容，调用方必须用 SceneBackground(hazeSource) 提供纯色底图。
 */
@Composable
fun GlassSurface(
    modifier: Modifier = Modifier,
    hazeState: HazeState,
    level: GlassLevel = GlassLevel.STANDARD,
    corner: Dp = GlassTokens.CardCorner,
    tintColor: Color? = null,
    liquid: Boolean = true,
    content: @Composable BoxScope.() -> Unit,
) {
    val spec = level.spec()
    val colors = LocalAppColors.current
    val tint = tintColor ?: colors.glassTint
    val shape = RoundedCornerShape(corner)

    val flow: Float
    val breathe: Float
    if (liquid) {
        val transition = rememberInfiniteTransition(label = "glass-liquid")
        flow = transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(5200, easing = LinearEasing), RepeatMode.Restart),
            label = "flow",
        ).value
        breathe = transition.animateFloat(
            initialValue = 0.72f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(2600, easing = LinearEasing), RepeatMode.Reverse),
            label = "breathe",
        ).value
    } else {
        flow = 0f
        breathe = 1f
    }

    Box(
        modifier
            .shadow(
                elevation = GlassTokens.ElevationDp.dp,
                shape = shape,
                clip = false,
                ambientColor = Color.Black,
                spotColor = Color.Black,
            )
            .clip(shape)
            .then(
                if (Build.VERSION.SDK_INT >= 31) {
                    Modifier.hazeEffect(state = hazeState) {
                        backgroundColor = Color.Transparent
                        tints = listOf(HazeTint(tint.copy(alpha = spec.tintAlpha)))
                        blurRadius = spec.blurDp.dp
                        noiseFactor = spec.grain
                    }
                } else Modifier
            )
            .background(
                Brush.verticalGradient(
                    listOf(
                        tint.copy(alpha = spec.tintAlpha * 1.4f),
                        tint.copy(alpha = spec.tintAlpha * 0.35f),
                        tint.copy(alpha = spec.tintAlpha * 0.9f),
                    )
                )
            )
            .drawWithContent {
                drawContent()

                val cornerRadius = CornerRadius(corner.toPx(), corner.toPx())
                val rim = GlassTokens.RimWidthDp.dp.toPx()

                // 1. 整圈液态光带（静态底光，保证边缘始终清晰）
                drawRoundRect(
                    brush = Brush.sweepGradient(
                        listOf(
                            colors.rimWarm.copy(alpha = 0.18f * spec.rimAlpha),
                            colors.rimCool.copy(alpha = 0.50f * spec.rimAlpha),
                            colors.rimWarm.copy(alpha = 0.28f * spec.rimAlpha),
                            colors.rimWarm.copy(alpha = 0.95f * spec.rimAlpha),
                            colors.rimWarm.copy(alpha = 0.18f * spec.rimAlpha),
                        ),
                        center = Offset(size.width * 0.5f, size.height * 0.5f),
                    ),
                    cornerRadius = cornerRadius,
                    style = Stroke(width = rim),
                )

                // 2. 亮色主题下补一条极淡的暗边，避免白玻璃在白底上“消失”
                if (colors.isLight) {
                    drawRoundRect(
                        color = Color.Black.copy(alpha = 0.10f),
                        cornerRadius = cornerRadius,
                        style = Stroke(width = 1.dp.toPx()),
                    )
                }

                // 3. 顶部镜面高光（固定）
                drawRoundRect(
                    brush = Brush.verticalGradient(
                        listOf(colors.rimWarm.copy(alpha = 0.38f * breathe), Color.Transparent),
                        endY = size.height * 0.55f,
                    ),
                    topLeft = Offset(rim * 0.8f, rim * 0.8f),
                    size = Size(size.width - rim * 1.6f, size.height - rim * 1.6f),
                    cornerRadius = CornerRadius(corner.toPx() - rim, corner.toPx() - rim),
                    style = Stroke(width = 1.dp.toPx()),
                )

                // 4. 顶部流动光段（液态感）
                drawLine(
                    brush = Brush.horizontalGradient(
                        listOf(
                            Color.Transparent,
                            colors.rimWarm.copy(alpha = 0.85f * spec.rimAlpha),
                            Color.Transparent,
                        ),
                        startX = size.width * (flow - 0.35f),
                        endX = size.width * (flow + 0.05f),
                    ),
                    start = Offset(0f, rim * 0.5f),
                    end = Offset(size.width, rim * 0.5f),
                    strokeWidth = rim,
                )

                // 5. 底部流动冷光（反方向）
                drawLine(
                    brush = Brush.horizontalGradient(
                        listOf(
                            Color.Transparent,
                            colors.rimCool.copy(alpha = 0.75f * spec.rimAlpha),
                            Color.Transparent,
                        ),
                        startX = size.width * (1f - flow - 0.15f),
                        endX = size.width * (1f - flow + 0.25f),
                    ),
                    start = Offset(0f, size.height - rim * 0.5f),
                    end = Offset(size.width, size.height - rim * 0.5f),
                    strokeWidth = rim,
                )

                // 6. 斜向扫光（整块玻璃缓慢流动的镜面反射）
                drawRect(
                    brush = Brush.linearGradient(
                        listOf(
                            Color.Transparent,
                            colors.rimWarm.copy(alpha = 0.10f),
                            Color.Transparent,
                        ),
                        start = Offset(size.width * (flow - 0.45f), 0f),
                        end = Offset(size.width * (flow + 0.15f), size.height),
                    ),
                )

                // 7. 内缘折射带：模拟光穿过玻璃厚度时的明暗弯折（顶部压亮、底部压暗）
                drawRoundRect(
                    brush = Brush.verticalGradient(
                        listOf(
                            colors.rimWarm.copy(alpha = 0.22f),
                            Color.Transparent,
                        ),
                        endY = size.height * 0.28f,
                    ),
                    topLeft = Offset(rim * 1.8f, rim * 1.8f),
                    size = Size(size.width - rim * 3.6f, size.height - rim * 3.6f),
                    cornerRadius = CornerRadius(corner.toPx() - rim * 1.8f, corner.toPx() - rim * 1.8f),
                    style = Stroke(width = 1.5.dp.toPx()),
                )
                drawRoundRect(
                    brush = Brush.verticalGradient(
                        listOf(
                            Color.Transparent,
                            Color.Black.copy(alpha = 0.16f),
                        ),
                        startY = size.height * 0.72f,
                        endY = size.height,
                    ),
                    topLeft = Offset(rim * 3.2f, rim * 3.2f),
                    size = Size(size.width - rim * 6.4f, size.height - rim * 6.4f),
                    cornerRadius = CornerRadius(
                        (corner.toPx() - rim * 3.2f).coerceAtLeast(0f),
                        (corner.toPx() - rim * 3.2f).coerceAtLeast(0f),
                    ),
                    style = Stroke(width = 2.dp.toPx()),
                )

                // 8. 色散：边缘 1px 的冷/暖分离（顶部青、底品红），制造光学厚感
                drawLine(
                    color = Color(0xFF5CC8FF).copy(alpha = 0.16f),
                    start = Offset(size.width * 0.18f, rim * 1.9f),
                    end = Offset(size.width * 0.82f, rim * 1.9f),
                    strokeWidth = 1.dp.toPx(),
                )
                drawLine(
                    color = Color(0xFFFF6FA0).copy(alpha = 0.13f),
                    start = Offset(size.width * 0.22f, size.height - rim * 1.9f),
                    end = Offset(size.width * 0.78f, size.height - rim * 1.9f),
                    strokeWidth = 1.dp.toPx(),
                )
            },
        content = content,
    )
}
