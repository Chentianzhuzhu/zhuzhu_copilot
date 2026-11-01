package com.zhuzhu.copilot.feature.tasks

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.zhuzhu.copilot.ui.LocalAppColors

@Composable
fun TasksPlaceholder() {
    val colors = LocalAppColors.current
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(stringResource(R.string.tasks_placeholder), color = colors.textTertiary)
    }
}
