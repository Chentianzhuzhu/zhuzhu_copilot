package com.zhuzhu.copilot.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.GlassTokens
import com.zhuzhu.copilot.ui.GlassTopBar
import dev.chrisbanes.haze.HazeState

@Composable
fun ChatScreen(hazeState: HazeState, modifier: Modifier = Modifier, viewModel: ChatViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsState()
    var input by remember { mutableStateOf("") }

    Column(modifier.fillMaxSize()) {
        GlassTopBar("zhuzhu", hazeState)

        LazyColumn(
            Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(state.messages) { bubble ->
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = if (bubble.fromUser) Arrangement.End else Arrangement.Start,
                ) {
                    GlassSurface(
                        modifier = Modifier.padding(vertical = 2.dp),
                        hazeState = hazeState,
                        corner = 12.dp,
                    ) {
                        Text(
                            bubble.text,
                            color = if (bubble.fromUser) Color.White else GlassTokens.TextPrimary,
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                        )
                    }
                }
            }
            if (state.streaming.isNotEmpty()) {
                item {
                    Text(state.streaming, color = GlassTokens.TextSecondary, modifier = Modifier.padding(8.dp))
                }
            }
        }

        if (state.error.isNotEmpty()) {
            Text(state.error, color = GlassTokens.Danger, modifier = Modifier.padding(horizontal = 16.dp))
        }

        GlassSurface(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            hazeState = hazeState,
            corner = 17.dp,
        ) {
            Row(Modifier.fillMaxWidth().padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
                TextField(
                    value = input,
                    onValueChange = { input = it },
                    placeholder = { Text("描述任务，或按住说话", color = GlassTokens.TextTertiary) },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(14.dp),
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        focusedTextColor = GlassTokens.TextPrimary,
                        unfocusedTextColor = GlassTokens.TextPrimary,
                    ),
                )
                TextButton(
                    onClick = {
                        if (state.running) {
                            viewModel.stop()
                        } else {
                            viewModel.send(input)
                            input = ""
                        }
                    },
                ) {
                    Text(if (state.running) "中止" else "发送", color = GlassTokens.BlueSoft)
                }
            }
        }
    }
}
