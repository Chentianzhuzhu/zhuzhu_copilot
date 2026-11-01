package com.zhuzhu.copilot.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.zhuzhu.copilot.model.ProviderConfig
import com.zhuzhu.copilot.ui.GlassTokens
import com.zhuzhu.copilot.ui.LocalAppColors

@Composable
fun ProviderSetupDialog(
    presets: List<ProviderConfig>,
    active: ProviderConfig?,
    savedApiKey: String,
    saving: Boolean,
    errorRes: Int?,
    onDismiss: () -> Unit,
    onSave: (ProviderConfig, String) -> Unit,
) {
    val customName = stringResource(R.string.provider_setup_custom_name)
    val colors = LocalAppColors.current
    var name by remember(active) { mutableStateOf(active?.name.orEmpty()) }
    var baseUrl by remember(active) { mutableStateOf(active?.baseUrl.orEmpty()) }
    var model by remember(active) { mutableStateOf(active?.model.orEmpty()) }
    var apiKey by remember(active) { mutableStateOf(savedApiKey) }
    var presetId by remember(active) { mutableStateOf(active?.id.orEmpty()) }
    var builtin by remember(active) { mutableStateOf(active?.isBuiltin ?: false) }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            Modifier
                .widthIn(max = 520.dp)
                .fillMaxWidth()
                .imePadding()
                .padding(horizontal = 20.dp, vertical = 28.dp)
                .heightIn(max = 620.dp)
                .clip(RoundedCornerShape(GlassTokens.SheetCorner))
                .background(colors.background)
                .border(
                    GlassTokens.RimWidthDp.dp,
                    colors.accent.copy(alpha = 0.7f),
                    RoundedCornerShape(GlassTokens.SheetCorner),
                )
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                stringResource(R.string.provider_setup_title),
                style = MaterialTheme.typography.titleMedium,
                color = colors.textPrimary,
            )
            Text(
                stringResource(R.string.provider_setup_notice),
                style = MaterialTheme.typography.labelSmall,
                color = colors.textTertiary,
            )

            SectionLabel(stringResource(R.string.provider_setup_presets))
            presets.forEach { preset ->
                PresetRow(
                    preset = preset,
                    selected = preset.id == presetId,
                    onClick = {
                        presetId = preset.id
                        name = preset.name
                        baseUrl = preset.baseUrl
                        model = preset.model
                        builtin = preset.isBuiltin
                    },
                )
            }

            SetupField(
                value = baseUrl,
                onValueChange = { baseUrl = it },
                label = stringResource(R.string.provider_setup_base_url),
            )
            SetupField(
                value = model,
                onValueChange = { model = it },
                label = stringResource(R.string.provider_setup_model),
            )
            SetupField(
                value = apiKey,
                onValueChange = { apiKey = it },
                label = stringResource(R.string.provider_setup_api_key),
                visualTransformation = PasswordVisualTransformation(),
            )

            errorRes?.let { res ->
                Text(
                    stringResource(res),
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.danger,
                )
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onDismiss) {
                    Text(stringResource(R.string.provider_setup_cancel), color = colors.textSecondary)
                }
                TextButton(
                    onClick = {
                        onSave(
                            ProviderConfig(
                                id = presetId,
                                name = name.ifBlank { customName },
                                baseUrl = baseUrl.trim(),
                                model = model.trim(),
                                isBuiltin = builtin,
                            ),
                            apiKey.trim(),
                        )
                    },
                    enabled = !saving && baseUrl.isNotBlank() && model.isNotBlank() && apiKey.isNotBlank(),
                ) {
                    Text(
                        stringResource(
                            if (saving) R.string.provider_setup_saving else R.string.provider_setup_save
                        ),
                        color = colors.accentSoft,
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    val colors = LocalAppColors.current
    Text(text, style = MaterialTheme.typography.labelSmall, color = colors.textTertiary)
}

@Composable
private fun PresetRow(preset: ProviderConfig, selected: Boolean, onClick: () -> Unit) {
    val colors = LocalAppColors.current
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(
                if (selected) colors.accentStrong.copy(alpha = 0.35f)
                else colors.textPrimary.copy(alpha = 0.05f)
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(preset.name, style = MaterialTheme.typography.bodySmall, color = colors.textPrimary)
            Text(
                "${preset.model} · ${preset.baseUrl}",
                style = MaterialTheme.typography.labelSmall,
                color = colors.textTertiary,
            )
        }
        if (selected) {
            Icon(
                Icons.Outlined.Check,
                contentDescription = null,
                tint = colors.accentSoft,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@Composable
private fun SetupField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    val colors = LocalAppColors.current
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, color = colors.textTertiary) },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        shape = RoundedCornerShape(12.dp),
        textStyle = MaterialTheme.typography.bodySmall,
        visualTransformation = visualTransformation,
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = colors.textPrimary,
            unfocusedTextColor = colors.textPrimary,
            focusedContainerColor = Color.Transparent,
            unfocusedContainerColor = Color.Transparent,
            focusedBorderColor = colors.accentStrong,
            unfocusedBorderColor = colors.divider,
            cursorColor = colors.accentSoft,
            focusedLabelColor = colors.accentSoft,
            unfocusedLabelColor = colors.textTertiary,
        ),
    )
}
