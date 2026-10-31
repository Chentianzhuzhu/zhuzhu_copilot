package com.zhuzhu.copilot.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import dev.chrisbanes.haze.HazeState

@Composable
fun GlassTopBar(title: String, hazeState: HazeState, trailing: @Composable () -> Unit = {}) {
    GlassSurface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp)
            .height(38.dp),
        hazeState = hazeState,
        corner = 14.dp,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = GlassTokens.TextPrimary)
            trailing()
        }
    }
}

@Composable
fun GlassTabBar(
    selected: Int,
    hazeState: HazeState,
    onSelect: (Int) -> Unit,
) {
    val labels = listOf("对话", "任务", "设置")
    val icons = listOf(Icons.Outlined.AutoAwesome, Icons.Outlined.Add, Icons.Outlined.Settings)
    GlassSurface(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp).height(52.dp),
        hazeState = hazeState,
        corner = 16.dp,
    ) {
        Row(Modifier.fillMaxWidth().padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            labels.forEachIndexed { index, label ->
                val active = index == selected
                Column(
                    Modifier
                        .weight(1f)
                        .background(
                            if (active) GlassTokens.BlueHighlight.copy(alpha = 0.55f) else Color.Transparent,
                            RoundedCornerShape(11.dp),
                        )
                        .clickable { onSelect(index) }
                        .padding(vertical = 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(
                        icons[index],
                        contentDescription = label,
                        tint = if (active) Color.White else GlassTokens.TextTertiary,
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        label,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (active) Color.White else GlassTokens.TextTertiary,
                    )
                }
            }
        }
    }
}

@Composable
fun StatusDot(color: Color, modifier: Modifier = Modifier) {
    Box(modifier.size(6.dp).background(color, CircleShape))
}
