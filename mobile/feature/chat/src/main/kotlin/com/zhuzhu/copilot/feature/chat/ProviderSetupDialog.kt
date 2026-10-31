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
                .background(GlassTokens.BgPanel)
                .border(
                    GlassTokens.RimWidthDp.dp,
                    GlassTokens.BluePrimary.copy(alpha = 0.7f),
                    RoundedCornerShape(GlassTokens.SheetCorner),
                )
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                stringResource(R.string.provider_setup_title),
                style = MaterialTheme.typography.titleMedium,
                color = GlassTokens.TextPrimary,
            )
            Text(
                stringResource(R.string.provider_setup_notice),
                style = MaterialTheme.typography.labelSmall,
                color = GlassTokens.TextTertiary,
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
                    color = GlassTokens.Danger,
                )
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = onDismiss) {
                    Text(stringResource(R.string.provider_setup_cancel), color = GlassTokens.TextSecondary)
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
                        color = GlassTokens.BlueSoft,
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = GlassTokens.TextTertiary)
}

@Composable
private fun PresetRow(preset: ProviderConfig, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(
                if (selected) GlassTokens.BlueHighlight.copy(alpha = 0.35f) else GlassTokens.BgPanelAlt
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(preset.name, style = MaterialTheme.typography.bodySmall, color = GlassTokens.TextPrimary)
            Text(
                "${preset.model} · ${preset.baseUrl}",
                style = MaterialTheme.typography.labelSmall,
                color = GlassTokens.TextTertiary,
            )
        }
        if (selected) {
            Icon(
                Icons.Outlined.Check,
                contentDescription = null,
                tint = GlassTokens.BlueSoft,
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
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, color = GlassTokens.TextTertiary) },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
        shape = RoundedCornerShape(12.dp),
        textStyle = MaterialTheme.typography.bodySmall,
        visualTransformation = visualTransformation,
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = GlassTokens.TextPrimary,
            unfocusedTextColor = GlassTokens.TextPrimary,
            focusedContainerColor = Color.Transparent,
            unfocusedContainerColor = Color.Transparent,
            focusedBorderColor = GlassTokens.BlueHighlight,
            unfocusedBorderColor = GlassTokens.Divider,
            cursorColor = GlassTokens.BlueSoft,
            focusedLabelColor = GlassTokens.BlueSoft,
            unfocusedLabelColor = GlassTokens.TextTertiary,
        ),
    )
}
