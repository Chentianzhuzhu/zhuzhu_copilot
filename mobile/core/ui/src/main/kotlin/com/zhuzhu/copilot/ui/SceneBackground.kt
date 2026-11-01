package com.zhuzhu.copilot.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.hazeSource

/**
 * 屏幕底层：纯黑为基，叠三层极淡的深蓝/冷白光斑。
 * 玻璃要“看得见磨砂”，背后就必须有可被模糊的内容；全黑背景会让模糊退化为纯黑面板。
 */
@Composable
fun SceneBackground(hazeState: HazeState, modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxSize()
            .hazeSource(hazeState),
    ) {
        Canvas(Modifier.fillMaxSize()) {
            drawRect(Brush.verticalGradient(listOf(Color(0xFF02040A), Color(0xFF000000), Color(0xFF010205))))

            drawRect(
                Brush.radialGradient(
                    colors = listOf(Color(0x662C4B8A), Color(0x222C4B8A), Color.Transparent),
                    center = Offset(size.width * 0.88f, size.height * 0.06f),
                    radius = size.maxDimension * 0.75f,
                )
            )
            drawRect(
                Brush.radialGradient(
                    colors = listOf(Color(0x22FFFFFF), Color(0x08FFFFFF), Color.Transparent),
                    center = Offset(size.width * 0.06f, size.height * 0.42f),
                    radius = size.maxDimension * 0.62f,
                )
            )
            drawRect(
                Brush.radialGradient(
                    colors = listOf(Color(0x331E3A6E), Color(0x111E3A6E), Color.Transparent),
                    center = Offset(size.width * 0.42f, size.height * 1.02f),
                    radius = size.maxDimension * 0.85f,
                )
            )
        }
    }
}
