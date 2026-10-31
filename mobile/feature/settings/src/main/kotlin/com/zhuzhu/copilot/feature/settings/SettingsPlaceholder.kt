package com.zhuzhu.copilot.feature.settings

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.zhuzhu.copilot.ui.GlassTokens

@Composable
fun SettingsPlaceholder() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text("设置页将在计划 C 接入", color = GlassTokens.TextTertiary)
    }
}
