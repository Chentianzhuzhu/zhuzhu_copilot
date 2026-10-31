package com.zhuzhu.copilot.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.zhuzhu.copilot.agent.AgentEngine
import com.zhuzhu.copilot.agent.ToolContext
import com.zhuzhu.copilot.data.repo.ProviderRepository
import com.zhuzhu.copilot.model.AgentEvent
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID
import javax.inject.Inject

data class ChatUiState(
    val messages: List<ChatBubble> = emptyList(),
    val streaming: String = "",
    val running: Boolean = false,
    val error: String = "",
    val providersReady: Boolean = false,
)

data class ChatBubble(val id: String, val fromUser: Boolean, val text: String)

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val providerRepository: ProviderRepository,
    private val engine: AgentEngine,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    init {
        refreshProviderState()
    }

    fun refreshProviderState() {
        viewModelScope.launch {
            _state.update { it.copy(providersReady = providerRepository.active() != null) }
        }
    }

    fun send(prompt: String) {
        val text = prompt.trim()
        if (text.isEmpty() || _state.value.running) return
        viewModelScope.launch {
            val provider = providerRepository.active()
            if (provider == null) {
                _state.update { it.copy(error = "请先在设置中配置模型") }
                return@launch
            }
            val apiKey = providerRepository.apiKeyOf(provider.id).orEmpty()
            if (apiKey.isBlank()) {
                _state.update { it.copy(error = "当前服务商缺少 API Key") }
                return@launch
            }
            _state.update {
                it.copy(
                    messages = it.messages + ChatBubble(UUID.randomUUID().toString(), true, text),
                    streaming = "",
                    running = true,
                    error = "",
                )
            }
            try {
                val answer = engine.run(text, ToolContext(UUID.randomUUID().toString())) { event ->
                    when (event) {
                        is AgentEvent.TokenDelta ->
                            _state.update { s -> s.copy(streaming = s.streaming + event.text) }
                        is AgentEvent.Error ->
                            _state.update { s -> s.copy(error = event.message) }
                        else -> Unit
                    }
                }
                _state.update {
                    it.copy(
                        messages = it.messages + ChatBubble(
                            UUID.randomUUID().toString(),
                            false,
                            answer.ifBlank { it.streaming },
                        ),
                        streaming = "",
                        running = false,
                    )
                }
            } catch (cancelled: CancellationException) {
                _state.update { it.copy(streaming = "", running = false) }
                throw cancelled
            }
        }
    }

    fun stop() = engine.cancel()
}
