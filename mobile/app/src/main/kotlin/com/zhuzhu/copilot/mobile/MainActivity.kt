package com.zhuzhu.copilot.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.zhuzhu.copilot.feature.chat.ChatScreen
import com.zhuzhu.copilot.feature.settings.SettingsPlaceholder
import com.zhuzhu.copilot.feature.tasks.TasksPlaceholder
import com.zhuzhu.copilot.ui.GlassTabBar
import com.zhuzhu.copilot.ui.SceneBackground
import com.zhuzhu.copilot.ui.ZhuzhuTheme
import dagger.hilt.android.AndroidEntryPoint
import dev.chrisbanes.haze.HazeState

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { ZhuzhuTheme { AppRoot() } }
    }
}

@Composable
private fun AppRoot() {
    val hazeState = remember { HazeState() }
    var tab by remember { mutableIntStateOf(0) }

    Box(Modifier.fillMaxSize()) {
        SceneBackground(hazeState)

        Box(Modifier.fillMaxSize().systemBarsPadding()) {
            Box(Modifier.fillMaxSize().padding(bottom = 84.dp).animateContentSize()) {
                when (tab) {
                    0 -> ChatScreen(hazeState)
                    1 -> TasksPlaceholder()
                    else -> SettingsPlaceholder()
                }
            }
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
                GlassTabBar(selected = tab, hazeState = hazeState, onSelect = { tab = it })
            }
        }
    }
}
