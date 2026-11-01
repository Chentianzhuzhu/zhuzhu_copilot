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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import com.zhuzhu.copilot.agent.ConfirmationDecision
import com.zhuzhu.copilot.model.ConfirmationRequest
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.LocalAppColors
import dev.chrisbanes.haze.HazeState

@Composable
fun ToolCallCard(row: ToolRowUi, hazeState: HazeState) {
    val colors = LocalAppColors.current
    GlassSurface(
        Modifier.fillMaxWidth().padding(horizontal = 0.dp, vertical = 2.dp),
        hazeState,
        corner = 16.dp,
        liquid = false,
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(row.name, color = colors.textPrimary, style = MaterialTheme.typography.bodySmall)
            Text(
                if (row.running) stringResource(R.string.chat_tool_running) else row.summary,
                color = if (row.running) colors.accentSoft else colors.textSecondary,
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
    val risk = riskLabel(request.risk)
    val colors = LocalAppColors.current
    GlassSurface(modifier.fillMaxWidth(), hazeState, corner = 20.dp) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "${request.toolName} · $risk",
                color = if (request.risk == RiskLevel.DESTRUCTIVE) colors.danger else colors.textPrimary,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(request.summary, color = colors.textSecondary, style = MaterialTheme.typography.labelSmall)
            Text(
                request.argumentsJson.take(200),
                color = colors.textTertiary,
                style = MaterialTheme.typography.labelSmall,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TextButton(onClick = { onDecision(ConfirmationDecision.DENY) }) {
                    Text(
                        stringResource(com.zhuzhu.copilot.ui.R.string.skip),
                        color = colors.textSecondary,
                    )
                }
                TextButton(onClick = { onDecision(ConfirmationDecision.ALLOW_ONCE) }) {
                    Text(
                        stringResource(com.zhuzhu.copilot.ui.R.string.allow),
                        color = colors.accentSoft,
                    )
                }
                if (request.risk != RiskLevel.DESTRUCTIVE) {
                    TextButton(onClick = { onDecision(ConfirmationDecision.ALLOW_SESSION) }) {
                        Text(
                            stringResource(com.zhuzhu.copilot.ui.R.string.always_allow),
                            color = colors.textSecondary,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun riskLabel(risk: RiskLevel): String = stringResource(
    when (risk) {
        RiskLevel.SAFE -> R.string.chat_risk_safe
        RiskLevel.SENSITIVE -> R.string.chat_risk_sensitive
        RiskLevel.DESTRUCTIVE -> R.string.chat_risk_destructive
    }
)
