package com.zhuzhu.copilot.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.material.icons.outlined.List
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.chrisbanes.haze.HazeState

@Composable
fun GlassTopBar(title: String, hazeState: HazeState, trailing: @Composable () -> Unit = {}) {
    val colors = LocalAppColors.current
    GlassSurface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp)
            .height(44.dp),
        hazeState = hazeState,
        corner = 16.dp,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = colors.textPrimary)
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
    val colors = LocalAppColors.current
    val labels = listOf(R.string.tab_chat, R.string.tab_tasks, R.string.tab_settings)
    val icons = listOf(Icons.Outlined.AutoAwesome, Icons.Outlined.List, Icons.Outlined.Settings)
    GlassSurface(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp).height(60.dp),
        hazeState = hazeState,
        corner = 20.dp,
    ) {
        Row(Modifier.fillMaxWidth().padding(5.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            labels.forEachIndexed { index, labelRes ->
                val active = index == selected
                val label = stringResource(labelRes)
                Column(
                    Modifier
                        .weight(1f)
                        .then(
                            if (active) {
                                Modifier.background(
                                    Brush.verticalGradient(
                                        listOf(
                                            colors.accentStrong.copy(alpha = 0.80f),
                                            colors.accent.copy(alpha = 0.60f),
                                        )
                                    ),
                                    RoundedCornerShape(14.dp),
                                )
                            } else Modifier
                        )
                        .then(
                            if (active) {
                                Modifier.border(
                                    width = 1.dp,
                                    brush = Brush.verticalGradient(
                                        listOf(Color.White.copy(alpha = 0.50f), Color.White.copy(alpha = 0.06f))
                                    ),
                                    shape = RoundedCornerShape(14.dp),
                                )
                            } else Modifier
                        )
                        .clickable { onSelect(index) }
                        .padding(vertical = 5.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(
                        icons[index],
                        contentDescription = label,
                        tint = if (active) Color.White else colors.textTertiary,
                        modifier = Modifier.size(18.dp),
                    )
                    Text(
                        label,
                        style = MaterialTheme.typography.labelSmall,
                        color = if (active) Color.White else colors.textTertiary,
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
