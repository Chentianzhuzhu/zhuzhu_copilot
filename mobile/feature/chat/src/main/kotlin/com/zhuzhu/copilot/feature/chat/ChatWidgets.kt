package com.zhuzhu.copilot.feature.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.zhuzhu.copilot.agent.ConfirmationDecision
import com.zhuzhu.copilot.model.ConfirmationRequest
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.GlassTokens
import dev.chrisbanes.haze.HazeState

@Composable
fun ToolCallCard(row: ToolRowUi, hazeState: HazeState) {
    GlassSurface(Modifier.fillMaxWidth(), hazeState, corner = 12.dp) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(row.name, color = GlassTokens.TextPrimary, style = MaterialTheme.typography.bodySmall)
            Text(
                if (row.running) "执行中…" else row.summary,
                color = if (row.running) GlassTokens.BlueSoft else GlassTokens.TextSecondary,
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

@Composable
fun ConfirmSheet(
    request: ConfirmationRequest,
    hazeState: HazeState,
    modifier: Modifier = Modifier,
    onDecision: (ConfirmationDecision) -> Unit,
) {
    GlassSurface(modifier.fillMaxWidth(), hazeState, corner = 16.dp) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "${request.toolName} · ${riskLabel(request.risk)}",
                color = if (request.risk == RiskLevel.DESTRUCTIVE) GlassTokens.Danger else GlassTokens.TextPrimary,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(request.summary, color = GlassTokens.TextSecondary, style = MaterialTheme.typography.labelSmall)
            Text(
                request.argumentsJson.take(200),
                color = GlassTokens.TextTertiary,
                style = MaterialTheme.typography.labelSmall,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TextButton(onClick = { onDecision(ConfirmationDecision.DENY) }) {
                    Text("跳过", color = GlassTokens.TextSecondary)
                }
                TextButton(onClick = { onDecision(ConfirmationDecision.ALLOW_ONCE) }) {
                    Text("允许", color = GlassTokens.BlueSoft)
                }
                if (request.risk != RiskLevel.DESTRUCTIVE) {
                    TextButton(onClick = { onDecision(ConfirmationDecision.ALLOW_SESSION) }) {
                        Text("始终允许", color = GlassTokens.TextSecondary)
                    }
                }
            }
        }
    }
}

private fun riskLabel(risk: RiskLevel): String = when (risk) {
    RiskLevel.SAFE -> "安全"
    RiskLevel.SENSITIVE -> "需确认"
    RiskLevel.DESTRUCTIVE -> "危险操作"
}
