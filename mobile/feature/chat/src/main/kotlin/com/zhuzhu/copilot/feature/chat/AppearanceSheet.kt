package com.zhuzhu.copilot.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.zhuzhu.copilot.ui.Appearance
import com.zhuzhu.copilot.ui.AppearanceMode
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.GlassTokens
import com.zhuzhu.copilot.ui.LocalAppColors
import dev.chrisbanes.haze.HazeState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppearanceSheet(
    current: Appearance,
    hazeState: HazeState,
    onDismiss: () -> Unit,
    onApply: (Appearance) -> Unit,
) {
    val colors = LocalAppColors.current
    var mode by remember { mutableStateOf(current.mode) }
    val initial = Color(current.customColor.toInt())
    var red by remember { mutableFloatStateOf(initial.red * 255f) }
    var green by remember { mutableFloatStateOf(initial.green * 255f) }
    var blue by remember { mutableFloatStateOf(initial.blue * 255f) }
    var imageUri by remember { mutableStateOf(current.backgroundImage) }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) imageUri = uri.toString()
    }

    val customColor = Color(red.toInt(), green.toInt(), blue.toInt())
    val preview = when (mode) {
        AppearanceMode.DARK -> Color(0xFF000000)
        AppearanceMode.LIGHT -> Color(0xFFF3F4F6)
        AppearanceMode.CUSTOM -> customColor
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = Color.Transparent,
        scrimColor = Color.Black.copy(alpha = 0.45f),
        dragHandle = null,
    ) {
        GlassSurface(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            hazeState = hazeState,
            corner = 24.dp,
        ) {
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    stringResource(R.string.appearance_title),
                    color = colors.textPrimary,
                    style = androidx.compose.material3.MaterialTheme.typography.titleMedium,
                )

                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ModeChip(
                        label = stringResource(R.string.appearance_dark),
                        selected = mode == AppearanceMode.DARK,
                        preview = Color(0xFF000000),
                        modifier = Modifier.weight(1f),
                    ) { mode = AppearanceMode.DARK }
                    ModeChip(
                        label = stringResource(R.string.appearance_light),
                        selected = mode == AppearanceMode.LIGHT,
                        preview = Color(0xFFF3F4F6),
                        modifier = Modifier.weight(1f),
                    ) { mode = AppearanceMode.LIGHT }
                    ModeChip(
                        label = stringResource(R.string.appearance_custom),
                        selected = mode == AppearanceMode.CUSTOM,
                        preview = customColor,
                        modifier = Modifier.weight(1f),
                    ) { mode = AppearanceMode.CUSTOM }
                }

                if (mode == AppearanceMode.CUSTOM) {
                    ChannelSlider(
                        label = stringResource(R.string.appearance_red),
                        value = red,
                        tint = Color(0xFFC9736E),
                    ) { red = it }
                    ChannelSlider(
                        label = stringResource(R.string.appearance_green),
                        value = green,
                        tint = Color(0xFF6FA57F),
                    ) { green = it }
                    ChannelSlider(
                        label = stringResource(R.string.appearance_blue),
                        value = blue,
                        tint = Color(0xFF7FA0DC),
                    ) { blue = it }
                }

                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { picker.launch("image/*") }) {
                        Text(
                            stringResource(
                                if (imageUri.isEmpty()) R.string.appearance_pick_image
                                else R.string.appearance_change_image
                            ),
                            color = colors.accentSoft,
                        )
                    }
                    if (imageUri.isNotEmpty()) {
                        TextButton(onClick = { imageUri = "" }) {
                            Text(stringResource(R.string.appearance_clear_image), color = colors.textSecondary)
                        }
                    }
                }

                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(
                        Modifier
                            .size(36.dp)
                            .background(preview, CircleShape)
                            .then(
                                Modifier.background(Color.White.copy(alpha = 0.0f))
                            )
                    )
                    Text(
                        hexOf(preview),
                        color = colors.textSecondary,
                        style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
                    )
                    Box(Modifier.weight(1f))
                    TextButton(onClick = onDismiss) {
                        Text(stringResource(R.string.appearance_cancel), color = colors.textSecondary)
                    }
                    TextButton(
                        onClick = {
                            onApply(
                                Appearance(
                                    mode = mode,
                                    customColor = customColor.toArgb().toLong() and 0xFFFFFFFFL,
                                    backgroundImage = imageUri,
                                )
                            )
                        }
                    ) {
                        Text(stringResource(R.string.appearance_apply), color = colors.accentSoft)
                    }
                }
            }
        }
    }
}

@Composable
private fun ModeChip(
    label: String,
    selected: Boolean,
    preview: Color,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val colors = LocalAppColors.current
    Column(
        modifier
            .background(
                if (selected) colors.accentStrong.copy(alpha = 0.22f) else colors.glassTint.copy(alpha = 0.06f),
                RoundedCornerShape(14.dp),
            )
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(22.dp).background(preview, CircleShape))
        Text(
            label,
            color = if (selected) colors.textPrimary else colors.textSecondary,
            style = androidx.compose.material3.MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun ChannelSlider(label: String, value: Float, tint: Color, onChange: (Float) -> Unit) {
    val colors = LocalAppColors.current
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, color = colors.textSecondary, style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
            Text(value.toInt().toString(), color = colors.textTertiary, style = androidx.compose.material3.MaterialTheme.typography.labelSmall)
        }
        Slider(
            value = value,
            onValueChange = onChange,
            valueRange = 0f..255f,
            colors = SliderDefaults.colors(
                thumbColor = tint,
                activeTrackColor = tint.copy(alpha = 0.8f),
                inactiveTrackColor = colors.glassTint.copy(alpha = 0.12f),
            ),
        )
    }
}

private fun hexOf(color: Color): String {
    val argb = color.toArgb()
    return "#%08X".format(argb)
}
