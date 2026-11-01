package com.zhuzhu.copilot.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.hazeSource

/**
 * 背景：纯色，不使用渐变。
 * 颜色来自用户外观设置（深色 / 亮色 / 自定义色），玻璃依靠它做高斯模糊底图。
 */
@Composable
fun SceneBackground(hazeState: HazeState, modifier: Modifier = Modifier) {
    val colors = LocalAppColors.current
    Box(
        modifier
            .fillMaxSize()
            .background(colors.background)
            .hazeSource(hazeState),
    )
}
