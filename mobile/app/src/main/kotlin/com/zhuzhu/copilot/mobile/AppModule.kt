package com.zhuzhu.copilot.mobile

import android.content.Context
import androidx.room.Room
import com.zhuzhu.copilot.agent.AgentConfig
import com.zhuzhu.copilot.agent.AgentEngine
import com.zhuzhu.copilot.agent.ChatTurn
import com.zhuzhu.copilot.agent.ConfirmMode
import com.zhuzhu.copilot.agent.ConfirmationCenter
import com.zhuzhu.copilot.agent.LlmCaller
import com.zhuzhu.copilot.agent.RiskPolicy
import com.zhuzhu.copilot.agent.ToolRegistry
import com.zhuzhu.copilot.data.KeystoreSecretStore
import com.zhuzhu.copilot.data.SecretStore
import com.zhuzhu.copilot.data.db.ProviderDao
import com.zhuzhu.copilot.data.db.ZhuzhuDatabase
import com.zhuzhu.copilot.data.repo.ProviderRepository
import com.zhuzhu.copilot.llm.LlmClient
import com.zhuzhu.copilot.llm.LlmDelta
import com.zhuzhu.copilot.llm.WireModels
import com.zhuzhu.copilot.model.ToolSpec
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {
    @Provides
    @Singleton
    fun secretStore(@ApplicationContext context: Context): SecretStore = KeystoreSecretStore(context)

    @Provides
    @Singleton
    fun providerRepository(
        @ApplicationContext context: Context,
        secrets: SecretStore,
    ): ProviderRepository = ProviderRepository(
        dao = DatabaseProvider.providerDao(context),
        secrets = secrets,
        assets = { name -> context.assets.open(name).bufferedReader().use { it.readText() } },
    )

    @Provides
    @Singleton
    fun llmClient(): LlmClient = LlmClient()

    @Provides
    @Singleton
    fun llmCaller(client: LlmClient, repo: ProviderRepository): LlmCaller = object : LlmCaller {
        override suspend fun stream(
            messages: List<ChatTurn>,
            tools: List<ToolSpec>,
        ): Flow<LlmDelta> {
            val provider = repo.active() ?: error("未配置模型服务商")
            return client.stream(
                baseUrl = provider.baseUrl,
                apiKey = repo.apiKeyOf(provider.id).orEmpty(),
                request = WireModels.ChatRequest(
                    model = provider.model,
                    messages = messages.map { turn ->
                        WireModels.WireMessage(
                            role = turn.role.name.lowercase(),
                            content = turn.content,
                            toolCalls = turn.toolCalls.takeIf { it.isNotEmpty() }?.map { call ->
                                WireModels.ToolCallOut(
                                    id = call.id,
                                    function = WireModels.FunctionCall(call.name, call.argumentsJson),
                                )
                            },
                            toolCallId = turn.toolCallId,
                        )
                    },
                    tools = tools.map { spec ->
                        WireModels.WireTool(
                            function = WireModels.WireFunction(
                                name = spec.name,
                                description = spec.description,
                                parameters = Json.parseToJsonElement(spec.parametersJson) as JsonObject,
                            )
                        )
                    },
                ),
            )
        }
    }

    @Provides
    @Singleton
    fun toolRegistry(): ToolRegistry = ToolRegistry(emptySet())

    @Provides
    @Singleton
    fun confirmationCenter(): ConfirmationCenter = ConfirmationCenter()

    @Provides
    @Singleton
    fun agentEngine(
        llm: LlmCaller,
        registry: ToolRegistry,
        center: ConfirmationCenter,
    ): AgentEngine = AgentEngine(
        llm = llm,
        registry = registry,
        riskPolicy = RiskPolicy(ConfirmMode.SENSITIVE_AND_ABOVE),
        // 唯一网关：确认请求挂起等待 UI 决策，不存在自动放行兜底
        gateway = center,
        config = AgentConfig(),
    )
}

object DatabaseProvider {
    @Volatile
    private var db: ZhuzhuDatabase? = null

    private fun instance(context: Context): ZhuzhuDatabase =
        db ?: synchronized(this) {
            db ?: Room.databaseBuilder(
                context.applicationContext,
                ZhuzhuDatabase::class.java,
                "zhuzhu.db",
            ).build().also { db = it }
        }

    fun providerDao(context: Context): ProviderDao = instance(context).providerDao()
}
