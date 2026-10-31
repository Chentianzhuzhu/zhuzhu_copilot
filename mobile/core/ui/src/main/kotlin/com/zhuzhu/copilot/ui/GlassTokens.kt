package com.zhuzhu.copilot.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

object GlassTokens {
    val BgBase = Color(0xFF000000)
    val BgPanel = Color(0xFF0B0F14)
    val BgPanelAlt = Color(0xFF12171E)
    val BluePrimary = Color(0xFF27407A)
    val BlueHighlight = Color(0xFF3B5BA5)
    val BlueSoft = Color(0xFF7FA0DC)
    val TextPrimary = Color(0xFFE6E8EB)
    val TextSecondary = Color(0xFF9AA3AE)
    val TextTertiary = Color(0xFF6B747F)
    val Success = Color(0xFF6FA57F)
    val Warning = Color(0xFFC9A77E)
    val Danger = Color(0xFFC9736E)
    val Divider = Color(0x0FFFFFFF)

    val CardCorner = 12.dp
    val PanelCorner = 16.dp
    val SheetCorner = 22.dp

    const val RimWidthDp = 1.2f
    const val StandardBlurDp = 22
    const val QuietBlurDp = 16
    const val VividBlurDp = 26
    const val GrainOpacity = 0.15f
}

enum class GlassLevel { QUIET, STANDARD, VIVID }

data class GlassSpec(
    val blurDp: Int,
    val saturation: Float,
    val tintAlpha: Float,
    val rimAlpha: Float,
)

fun GlassLevel.spec(): GlassSpec = when (this) {
    GlassLevel.QUIET -> GlassSpec(GlassTokens.QuietBlurDp, 1.35f, 0.06f, 0.45f)
    GlassLevel.STANDARD -> GlassSpec(GlassTokens.StandardBlurDp, 1.75f, 0.10f, 1.0f)
    GlassLevel.VIVID -> GlassSpec(GlassTokens.VividBlurDp, 2.05f, 0.13f, 1.0f)
}

object MotionTokens {
    const val EnterMs = 200
    const val HudInMs = 320
    const val HudOutMs = 200
    const val HighlightMoveMs = 120
    const val BreatheMs = 2200
}
