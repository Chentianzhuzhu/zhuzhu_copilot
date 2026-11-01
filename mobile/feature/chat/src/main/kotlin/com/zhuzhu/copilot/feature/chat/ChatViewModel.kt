package com.zhuzhu.copilot.feature.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.zhuzhu.copilot.agent.AgentEngine
import com.zhuzhu.copilot.agent.ConfirmationCenter
import com.zhuzhu.copilot.agent.ConfirmationDecision
import com.zhuzhu.copilot.agent.ToolContext
import com.zhuzhu.copilot.data.repo.ProviderRepository
import com.zhuzhu.copilot.data.repo.SettingsRepository
import com.zhuzhu.copilot.model.AgentEvent
import com.zhuzhu.copilot.model.ConfirmationRequest
import com.zhuzhu.copilot.model.ProviderConfig
import com.zhuzhu.copilot.ui.Appearance
import com.zhuzhu.copilot.ui.AppearanceHolder
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
    val errorRes: Int? = null,
    val providersReady: Boolean = false,
    val toolRows: List<ToolRowUi> = emptyList(),
    val pendingConfirm: ConfirmationRequest? = null,
    val presets: List<ProviderConfig> = emptyList(),
    val activeConfig: ProviderConfig? = null,
    val activeApiKey: String = "",
    val setupSaving: Boolean = false,
    val setupSaved: Boolean = false,
    val setupErrorRes: Int? = null,
)

data class ChatBubble(val id: String, val fromUser: Boolean, val text: String)

data class ToolRowUi(val callId: String, val name: String, val summary: String, val running: Boolean)

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val providerRepository: ProviderRepository,
    private val settingsRepository: SettingsRepository,
    private val engine: AgentEngine,
    private val confirmCenter: ConfirmationCenter,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    init {
        refreshProviderState()
        viewModelScope.launch {
            confirmCenter.pending.collect { request ->
                _state.update { it.copy(pendingConfirm = request) }
            }
        }
    }

    fun refreshProviderState() {
        viewModelScope.launch {
            val presets = providerRepository.loadPresets()
            val active = providerRepository.active()
            val apiKey = active?.let { providerRepository.apiKeyOf(it.id) }.orEmpty()
            _state.update {
                it.copy(
                    presets = presets,
                    activeConfig = active,
                    activeApiKey = apiKey,
                    providersReady = active != null,
                )
            }
        }
    }

    fun saveProvider(config: ProviderConfig, apiKey: String) {
        if (_state.value.setupSaving) return
        viewModelScope.launch {
            _state.update { it.copy(setupSaving = true, setupErrorRes = null) }
            try {
                providerRepository.saveConfig(config, apiKey)
                val active = providerRepository.active()
                _state.update {
                    it.copy(
                        setupSaving = false,
                        setupSaved = true,
                        activeConfig = active,
                        activeApiKey = apiKey,
                        providersReady = active != null,
                        error = "",
                        errorRes = null,
                    )
                }
            } catch (cancelled: CancellationException) {
                _state.update { it.copy(setupSaving = false) }
                throw cancelled
            } catch (ignored: Exception) {
                _state.update { it.copy(setupSaving = false, setupErrorRes = R.string.chat_setup_save_failed) }
            }
        }
    }

    fun consumeSetupSaved() {
        _state.update { it.copy(setupSaved = false) }
    }

    fun send(prompt: String) {
        val text = prompt.trim()
        if (text.isEmpty() || _state.value.running) return
        viewModelScope.launch {
            val provider = providerRepository.active()
            if (provider == null) {
                _state.update { it.copy(errorRes = R.string.chat_need_provider, error = "") }
                return@launch
            }
            val apiKey = providerRepository.apiKeyOf(provider.id).orEmpty()
            if (apiKey.isBlank()) {
                _state.update { it.copy(errorRes = R.string.chat_missing_key, error = "") }
                return@launch
            }
            _state.update {
                it.copy(
                    messages = it.messages + ChatBubble(UUID.randomUUID().toString(), true, text),
                    streaming = "",
                    running = true,
                    error = "",
                    errorRes = null,
                    toolRows = emptyList(),
                )
            }
            try {
                val answer = engine.run(text, ToolContext(UUID.randomUUID().toString())) { event ->
                    when (event) {
                        is AgentEvent.TokenDelta ->
                            _state.update { s -> s.copy(streaming = s.streaming + event.text) }
                        is AgentEvent.Error ->
                            _state.update { s -> s.copy(error = event.message, errorRes = null) }
                        is AgentEvent.ToolStarted -> _state.update { s ->
                            s.copy(
                                toolRows = s.toolRows.filterNot { it.callId == event.call.id } +
                                    ToolRowUi(event.call.id, event.call.name, "", running = true),
                            )
                        }
                        is AgentEvent.ToolResult -> _state.update { s ->
                            s.copy(
                                toolRows = s.toolRows.filterNot { it.callId == event.call.id } +
                                    ToolRowUi(event.call.id, event.call.name, event.summary, running = false),
                            )
                        }
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

    fun answerConfirm(decision: ConfirmationDecision) = confirmCenter.resolve(decision)

    fun stop() = engine.cancel()

    /** 外观切换立即生效，随后异步落盘；不阻塞 UI。 */
    fun saveAppearance(appearance: Appearance) {
        AppearanceHolder.apply(appearance)
        viewModelScope.launch {
            settingsRepository.setAppearanceMode(appearance.mode.name)
            settingsRepository.setBackgroundColor(appearance.customColor)
            settingsRepository.setBackgroundImage(appearance.backgroundImage)
        }
    }
}
