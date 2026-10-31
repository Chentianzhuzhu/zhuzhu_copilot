package com.zhuzhu.copilot.llm

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

sealed interface LlmDelta {
    data class Text(val text: String) : LlmDelta
    data class Reasoning(val text: String) : LlmDelta
    data class ToolCallDelta(
        val index: Int,
        val id: String?,
        val name: String?,
        val arguments: String?,
    ) : LlmDelta
    data object Finished : LlmDelta
}

class LlmHttpException(val status: Int, val body: String) :
    RuntimeException("LLM HTTP $status: ${body.take(300)}")

class LlmClient(
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build(),
) {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val mediaType = "application/json".toMediaType()

    fun stream(baseUrl: String, apiKey: String, request: WireModels.ChatRequest): Flow<LlmDelta> = flow {
        val url = baseUrl.trimEnd('/') + "/chat/completions"
        val body = json.encodeToString(WireModels.ChatRequest.serializer(), request)
        val httpRequest = Request.Builder()
            .url(url)
            .addHeader("Authorization", "Bearer $apiKey")
            .addHeader("Accept", "text/event-stream")
            .post(body.toRequestBody(mediaType))
            .build()

        http.newCall(httpRequest).execute().use { response ->
            if (!response.isSuccessful) {
                throw LlmHttpException(response.code, response.body?.string().orEmpty())
            }
            val source = response.body?.source() ?: throw LlmHttpException(-1, "empty body")
            while (true) {
                val line = source.readUtf8Line() ?: break
                val payload = SseParser.payloadOf(line) ?: continue
                val chunk = runCatching {
                    json.decodeFromString(WireModels.StreamChunk.serializer(), payload)
                }.getOrNull() ?: continue
                val delta = chunk.choices.firstOrNull()?.delta ?: continue
                delta.reasoning?.takeIf { it.isNotEmpty() }?.let { emit(LlmDelta.Reasoning(it)) }
                delta.content?.takeIf { it.isNotEmpty() }?.let { emit(LlmDelta.Text(it)) }
                delta.toolCalls?.forEach { call ->
                    emit(
                        LlmDelta.ToolCallDelta(
                            index = call.index,
                            id = call.id,
                            name = call.function?.name,
                            arguments = call.function?.arguments,
                        )
                    )
                }
                if (chunk.choices.firstOrNull()?.finishReason != null) emit(LlmDelta.Finished)
            }
        }
    }.flowOn(Dispatchers.IO)
}
