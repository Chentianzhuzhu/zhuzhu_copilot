package com.zhuzhu.copilot.feature.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Palette
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.zhuzhu.copilot.ui.AppearanceHolder
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.GlassTokens
import com.zhuzhu.copilot.ui.GlassTopBar
import com.zhuzhu.copilot.ui.LocalAppColors
import com.zhuzhu.copilot.ui.MotionTokens
import dev.chrisbanes.haze.HazeState

@Composable
fun ChatScreen(hazeState: HazeState, modifier: Modifier = Modifier, viewModel: ChatViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsState()
    val colors = LocalAppColors.current
    val appearance by AppearanceHolder.state.collectAsState()
    var input by remember { mutableStateOf("") }
    var showSetup by remember { mutableStateOf(false) }
    var showAppearance by remember { mutableStateOf(false) }

    LaunchedEffect(state.setupSaved) {
        if (state.setupSaved) {
            showSetup = false
            viewModel.consumeSetupSaved()
            viewModel.refreshProviderState()
        }
    }

    Column(modifier.fillMaxSize()) {
        GlassTopBar("zhuzhu", hazeState) {
            Row(horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(28.dp)
                        .clip(CircleShape)
                        .clickable { showAppearance = true },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Outlined.Palette,
                        contentDescription = stringResource(R.string.appearance_open_cd),
                        tint = colors.textSecondary,
                        modifier = Modifier.size(17.dp),
                    )
                }
                Box(
                    Modifier
                        .size(28.dp)
                        .clip(CircleShape)
                        .clickable {
                            viewModel.refreshProviderState()
                            showSetup = true
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Outlined.Settings,
                        contentDescription = stringResource(R.string.chat_settings_cd),
                        tint = colors.textSecondary,
                        modifier = Modifier.size(17.dp),
                    )
                }
            }
        }

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
                        modifier = Modifier.padding(vertical = 3.dp),
                        hazeState = hazeState,
                        corner = 16.dp,
                        tintColor = if (bubble.fromUser) colors.accentStrong else null,
                        liquid = false,
                    ) {
                        Text(
                            bubble.text,
                            color = if (bubble.fromUser) Color.White else colors.textPrimary,
                            modifier = Modifier.padding(horizontal = 13.dp, vertical = 9.dp),
                        )
                    }
                }
            }
            if (state.streaming.isNotEmpty()) {
                item {
                    Text(state.streaming, color = colors.textSecondary, modifier = Modifier.padding(8.dp))
                }
            }
            items(state.toolRows, key = { it.callId }) { row ->
                ToolCallCard(row, hazeState)
            }
        }

        val errorRes = state.errorRes
        val errorText = errorRes?.let { stringResource(it) } ?: state.error
        AnimatedVisibility(
            visible = errorText.isNotEmpty(),
            enter = fadeIn(tween(MotionTokens.EnterMs)),
            exit = fadeOut(tween(MotionTokens.EnterMs)),
        ) {
            Text(
                errorText,
                color = colors.danger,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }

        state.pendingConfirm?.let { request ->
            ConfirmSheet(
                request = request,
                hazeState = hazeState,
                onDecision = viewModel::answerConfirm,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }

        GlassSurface(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            hazeState = hazeState,
            corner = GlassTokens.CapsuleCorner,
        ) {
            Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                TextField(
                    value = input,
                    onValueChange = { input = it },
                    placeholder = {
                        Text(stringResource(R.string.chat_input_hint), color = colors.textTertiary)
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(14.dp),
                    colors = TextFieldDefaults.colors(
                        focusedContainerColor = Color.Transparent,
                        unfocusedContainerColor = Color.Transparent,
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        focusedTextColor = colors.textPrimary,
                        unfocusedTextColor = colors.textPrimary,
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
                    Text(
                        stringResource(if (state.running) R.string.chat_stop else R.string.chat_send),
                        color = colors.accentSoft,
                    )
                }
            }
        }
    }

    if (showSetup) {
        ProviderSetupDialog(
            presets = state.presets,
            active = state.activeConfig,
            savedApiKey = state.activeApiKey,
            saving = state.setupSaving,
            errorRes = state.setupErrorRes,
            onDismiss = {
                showSetup = false
                viewModel.refreshProviderState()
            },
            onSave = viewModel::saveProvider,
        )
    }

    if (showAppearance) {
        AppearanceSheet(
            current = appearance,
            hazeState = hazeState,
            onDismiss = { showAppearance = false },
            onApply = {
                viewModel.saveAppearance(it)
                showAppearance = false
            },
        )
    }
}
