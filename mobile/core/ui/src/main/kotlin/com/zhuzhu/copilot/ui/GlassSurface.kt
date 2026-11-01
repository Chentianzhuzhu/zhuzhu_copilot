package com.zhuzhu.copilot.ui

import android.os.Build
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
 * iOS 风格液态玻璃：
 * 1. 外部柔和投影，让玻璃悬浮于场景之上
 * 2. 背景模糊 + 白色微 tint + 颗粒，形成内部磨砂
 * 3. 整圈环形光带（右上冷白、右下深蓝、左上最强）
 * 4. 顶部 1dp 镜面高光 + 左上弧形反光
 *
 * 注意：模糊只能采样同窗口内容。调用方必须用 SceneBackground + hazeSource 提供底层场景，
 * 否则纯黑背景会让模糊退化成实色面板。
 */
@Composable
fun GlassSurface(
    modifier: Modifier = Modifier,
    hazeState: HazeState,
    level: GlassLevel = GlassLevel.STANDARD,
    corner: Dp = GlassTokens.CardCorner,
    tintColor: Color = Color.White,
    content: @Composable BoxScope.() -> Unit,
) {
    val spec = level.spec()
    val shape = RoundedCornerShape(corner)

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
                        tints = listOf(HazeTint(tintColor.copy(alpha = spec.tintAlpha)))
                        blurRadius = spec.blurDp.dp
                        noiseFactor = spec.grain
                    }
                } else Modifier
            )
            .background(
                Brush.verticalGradient(
                    listOf(
                        tintColor.copy(alpha = spec.tintAlpha * 1.5f),
                        Color.White.copy(alpha = 0.015f),
                        tintColor.copy(alpha = spec.tintAlpha * 0.85f),
                    )
                )
            )
            .drawWithContent {
                drawContent()

                val cornerRadius = CornerRadius(corner.toPx(), corner.toPx())
                val rim = GlassTokens.RimWidthDp.dp.toPx()

                // 整圈液态光带：0°=右，90°=下，180°=左，270°=上
                drawRoundRect(
                    brush = Brush.sweepGradient(
                        listOf(
                            Color.White.copy(alpha = 0.20f * spec.rimAlpha),
                            GlassTokens.BlueSoft.copy(alpha = 0.55f * spec.rimAlpha),
                            Color.White.copy(alpha = 0.32f * spec.rimAlpha),
                            Color.White.copy(alpha = 1.00f * spec.rimAlpha),
                            Color.White.copy(alpha = 0.20f * spec.rimAlpha),
                        ),
                        center = Offset(size.width * 0.5f, size.height * 0.5f),
                    ),
                    cornerRadius = cornerRadius,
                    style = Stroke(width = rim),
                )

                // 顶部镜面高光（比整圈更亮更细）
                drawRoundRect(
                    brush = Brush.verticalGradient(
                        listOf(Color.White.copy(alpha = 0.42f), Color.Transparent),
                        endY = size.height * 0.55f,
                    ),
                    topLeft = Offset(rim * 0.8f, rim * 0.8f),
                    size = Size(size.width - rim * 1.6f, size.height - rim * 1.6f),
                    cornerRadius = CornerRadius(corner.toPx() - rim, corner.toPx() - rim),
                    style = Stroke(width = 1.dp.toPx()),
                )

                // 左上弧形反光（液态玻璃的“高光弧”）
                drawArc(
                    brush = Brush.radialGradient(
                        colors = listOf(Color.White.copy(alpha = 0.30f), Color.Transparent),
                        center = Offset(size.width * 0.12f, -size.height * 0.05f),
                        radius = size.width * 0.95f,
                    ),
                    startAngle = 175f,
                    sweepAngle = 105f,
                    useCenter = false,
                    topLeft = Offset(-size.width * 0.04f, -size.height * 0.18f),
                    size = Size(size.width * 0.92f, size.height * 1.15f),
                    style = Stroke(width = 2.dp.toPx()),
                )

                // 底部冷色反光，制造厚度
                drawLine(
                    brush = Brush.horizontalGradient(
                        listOf(
                            Color.Transparent,
                            GlassTokens.BlueSoft.copy(alpha = 0.22f * spec.rimAlpha),
                            Color.Transparent,
                        )
                    ),
                    start = Offset(size.width * 0.22f, size.height - rim * 0.6f),
                    end = Offset(size.width * 0.78f, size.height - rim * 0.6f),
                    strokeWidth = 1.dp.toPx(),
                )
            },
        content = content,
    )
}
