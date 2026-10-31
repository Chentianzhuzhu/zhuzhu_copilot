package com.zhuzhu.copilot.data

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.zhuzhu.copilot.data.db.ZhuzhuDatabase
import com.zhuzhu.copilot.data.repo.ProviderRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ProviderRepositoryTest {
    private lateinit var db: ZhuzhuDatabase
    private lateinit var repo: ProviderRepository
    private lateinit var secrets: InMemorySecretStore

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, ZhuzhuDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        secrets = InMemorySecretStore()
        repo = ProviderRepository(
            dao = db.providerDao(),
            secrets = secrets,
            assets = { name -> context.assets.open(name).bufferedReader().use { it.readText() } },
        )
    }

    @After
    fun tearDown() = db.close()

    @Test
    fun `presets are loaded from assets`() = runTest {
        val presets = repo.loadPresets()
        assertTrue(presets.any { it.id == "agnes" })
        assertTrue(presets.any { it.id == "deepseek" })
    }

    @Test
    fun `save and read config with encrypted key`() = runTest {
        repo.saveConfig(repo.loadPresets().first(), apiKey = "sk-test")
        val active = repo.active()
        assertEquals("agnes", active!!.id)
        assertEquals("sk-test", repo.apiKeyOf(active.id))
        assertEquals("sk-test", secrets.get("api_key_agnes"))
    }

    @Test
    fun `saving another provider keeps single active row`() = runTest {
        val presets = repo.loadPresets()
        repo.saveConfig(presets.first(), apiKey = "key-agnes")
        repo.saveConfig(presets.first { it.id == "deepseek" }, apiKey = "key-deepseek")

        assertEquals("deepseek", repo.active()!!.id)
        assertEquals(2, repo.all().size)
        assertEquals("key-deepseek", repo.apiKeyOf("deepseek"))
        assertEquals("key-agnes", repo.apiKeyOf("agnes"))
    }
}

private class InMemorySecretStore : SecretStore {
    private val values = mutableMapOf<String, String>()

    override fun put(name: String, value: String) {
        values[name] = value
    }

    override fun get(name: String): String? = values[name]

    override fun remove(name: String) {
        values.remove(name)
    }
}
