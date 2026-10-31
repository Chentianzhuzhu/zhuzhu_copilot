package com.zhuzhu.copilot.data.repo

import com.zhuzhu.copilot.data.SecretStore
import com.zhuzhu.copilot.data.db.ProviderDao
import com.zhuzhu.copilot.data.db.ProviderEntity
import com.zhuzhu.copilot.model.ProviderConfig
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

@Serializable
private data class PresetDto(
    val id: String,
    val name: String,
    val baseUrl: String,
    val model: String,
    val isBuiltin: Boolean = false,
)

class ProviderRepository(
    private val dao: ProviderDao,
    private val secrets: SecretStore,
    private val assets: (String) -> String,
) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun loadPresets(): List<ProviderConfig> =
        json.decodeFromString<List<PresetDto>>(assets(PRESETS_ASSET)).map {
            ProviderConfig(it.id, it.name, it.baseUrl, it.model, it.isBuiltin)
        }

    suspend fun saveConfig(config: ProviderConfig, apiKey: String) {
        val id = config.id.ifEmpty { UUID.randomUUID().toString() }
        dao.clearActive()
        dao.upsert(
            ProviderEntity(
                id = id,
                name = config.name,
                baseUrl = config.baseUrl,
                model = config.model,
                isBuiltin = config.isBuiltin,
                active = true,
            )
        )
        secrets.put(secretNameOf(id), apiKey)
    }

    suspend fun active(): ProviderConfig? = dao.active()?.toConfig()

    fun apiKeyOf(providerId: String): String? = secrets.get(secretNameOf(providerId))

    suspend fun all(): List<ProviderConfig> = dao.all().map { it.toConfig() }

    private fun ProviderEntity.toConfig() = ProviderConfig(id, name, baseUrl, model, isBuiltin)

    private fun secretNameOf(providerId: String) = "api_key_$providerId"

    private companion object {
        const val PRESETS_ASSET = "providers.json"
    }
}
