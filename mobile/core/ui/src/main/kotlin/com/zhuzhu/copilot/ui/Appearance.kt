package com.zhuzhu.copilot.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** 背景模式：深色 / 亮色 / 用户自定义颜色（自定义色按亮度自动归入深色或亮色体系） */
enum class AppearanceMode { DARK, LIGHT, CUSTOM }

data class Appearance(
    val mode: AppearanceMode = AppearanceMode.DARK,
    val customColor: Long = 0xFF000000,
)

/** 全局外观状态：主题、背景色都在这里单点持有，UI 只订阅。 */
object AppearanceHolder {
    private val _state = MutableStateFlow(Appearance())
    val state: StateFlow<Appearance> = _state.asStateFlow()

    fun apply(appearance: Appearance) = _state.update { appearance }
}

data class AppColors(
    val isLight: Boolean,
    val background: Color,
    val glassTint: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    val accent: Color,
    val accentStrong: Color,
    val accentSoft: Color,
    val success: Color,
    val warning: Color,
    val danger: Color,
    val divider: Color,
    val rimWarm: Color,
    val rimCool: Color,
)

val DarkColors = AppColors(
    isLight = false,
    background = Color(0xFF000000),
    glassTint = Color.White,
    textPrimary = Color(0xFFE6E8EB),
    textSecondary = Color(0xFF9AA3AE),
    textTertiary = Color(0xFF6B747F),
    accent = Color(0xFF27407A),
    accentStrong = Color(0xFF3B5BA5),
    accentSoft = Color(0xFF7FA0DC),
    success = Color(0xFF6FA57F),
    warning = Color(0xFFC9A77E),
    danger = Color(0xFFC9736E),
    divider = Color(0x14FFFFFF),
    rimWarm = Color(0xFFFFFFFF),
    rimCool = Color(0xFF8CB4FF),
)

val LightColors = AppColors(
    isLight = true,
    background = Color(0xFFF3F4F6),
    glassTint = Color.White,
    textPrimary = Color(0xFF14171C),
    textSecondary = Color(0xFF4A5260),
    textTertiary = Color(0xFF8A929E),
    accent = Color(0xFF27407A),
    accentStrong = Color(0xFF3B5BA5),
    accentSoft = Color(0xFF4A6AAE),
    success = Color(0xFF3F7A52),
    warning = Color(0xFF8A6430),
    danger = Color(0xFFA4453F),
    divider = Color(0x14000000),
    rimWarm = Color(0xFFFFFFFF),
    rimCool = Color(0xFF5C7FC0),
)

/** 背景色亮度决定用深色还是亮色文字/玻璃体系；自定义色自动归类。 */
fun appearanceColors(appearance: Appearance): AppColors {
    val background = when (appearance.mode) {
        AppearanceMode.DARK -> DarkColors.background
        AppearanceMode.LIGHT -> LightColors.background
        AppearanceMode.CUSTOM -> Color(appearance.customColor.toInt())
    }
    val base = if (background.luminance() > 0.5f) LightColors else DarkColors
    return base.copy(background = background)
}

val LocalAppColors = androidx.compose.runtime.staticCompositionLocalOf { DarkColors }

@Composable
fun ZhuzhuTheme(appearance: Appearance = Appearance(), content: @Composable () -> Unit) {
    val colors = appearanceColors(appearance)
    val scheme = if (colors.isLight) {
        lightColorScheme(
            background = colors.background,
            surface = colors.background,
            primary = colors.accentStrong,
            onPrimary = Color.White,
            onBackground = colors.textPrimary,
            onSurface = colors.textPrimary,
        )
    } else {
        darkColorScheme(
            background = colors.background,
            surface = colors.background,
            primary = colors.accentStrong,
            onPrimary = Color.White,
            onBackground = colors.textPrimary,
            onSurface = colors.textPrimary,
        )
    }
    CompositionLocalProvider(LocalAppColors provides colors) {
        MaterialTheme(colorScheme = scheme, typography = AppTypography, content = content)
    }
}
