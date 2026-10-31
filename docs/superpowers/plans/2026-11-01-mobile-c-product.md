# zhuzhu Copilot Mobile · 计划 C：产品功能、语音、定时任务与交付

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐任务页、设置与权限引导、模型/账号配置、语音输入与播报、定时任务，并完成自签名 release 打包与用户操作手册。

**Architecture:** 任务页读取 Room 中的任务与步骤快照，实时任务由 `TaskSessionStore` 内存流驱动；设置页拆成模型、权限、自动化、语音四组；语音走系统 `RecognizerIntent` 与 `TextToSpeech`；定时任务由 WorkManager 拉起前台服务。

**Tech Stack:** Room、WorkManager、SpeechRecognizer、TextToSpeech、OkHttp、WebView（账号登录）。

**前置：** 计划 A、B 全部完成。

---

### Task 15: 任务页与任务记录

**Files:**
- Create: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/repo/TaskRepository.kt`
- Create: `mobile/core/agent/src/main/kotlin/com/zhuzhu/copilot/agent/TaskSessionStore.kt`
- Create: `mobile/feature/tasks/src/main/kotlin/com/zhuzhu/copilot/feature/tasks/TasksViewModel.kt`
- Create: `mobile/feature/tasks/src/main/kotlin/com/zhuzhu/copilot/feature/tasks/TasksScreen.kt`
- Modify: `mobile/feature/tasks/src/main/kotlin/com/zhuzhu/copilot/feature/tasks/TasksPlaceholder.kt`（删除占位）
- Modify: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/Entities.kt`（新增 ToolCallEntity、TaskStepEntity）
- Modify: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/Daos.kt`（新增 TaskDao、StepDao、ToolCallDao）
- Modify: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/ZhuzhuDatabase.kt`（新增三张表，version = 2）
- Test: `mobile/core/data/src/test/kotlin/com/zhuzhu/copilot/data/TaskRepositoryTest.kt`

- [ ] **Step 1: 写失败测试**

```kotlin
package com.zhuzhu.copilot.data

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.zhuzhu.copilot.data.db.ZhuzhuDatabase
import com.zhuzhu.copilot.data.repo.TaskRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class TaskRepositoryTest {
    private lateinit var db: ZhuzhuDatabase
    private lateinit var repo: TaskRepository

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, ZhuzhuDatabase::class.java).allowMainThreadQueries().build()
        repo = TaskRepository(db.taskDao())
    }

    @After
    fun tearDown() = db.close()

    @Test
    fun `save then list running tasks`() = runTest {
        repo.save(id = "t1", title = "关蓝牙", status = "RUNNING", reason = "")
        val running = repo.byStatus("RUNNING")
        assertEquals(1, running.size)
        assertEquals("关蓝牙", running.first().title)
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
```

Expected：`TaskRepository` 或 `taskDao` 未定义。

- [ ] **Step 3: 实现 DAO 与仓储**

`Daos.kt` 追加：

```kotlin
@Dao
interface TaskDao {
    @Query("SELECT * FROM task WHERE status = :status ORDER BY createdAt DESC")
    suspend fun byStatus(status: String): List<TaskEntity>

    @Query("SELECT * FROM task ORDER BY createdAt DESC LIMIT 50")
    suspend fun recent(): List<TaskEntity>

    @Upsert
    suspend fun upsert(entity: TaskEntity)
}
```

`TaskRepository.kt`：

```kotlin
package com.zhuzhu.copilot.data.repo

import com.zhuzhu.copilot.data.db.TaskDao
import com.zhuzhu.copilot.data.db.TaskEntity
import java.util.UUID

class TaskRepository(private val dao: TaskDao) {
    suspend fun save(id: String = UUID.randomUUID().toString(), title: String, status: String, reason: String = "") {
        dao.upsert(TaskEntity(id = id, title = title, status = status, createdAt = System.currentTimeMillis(), failureReason = reason))
    }

    suspend fun byStatus(status: String): List<TaskEntity> = dao.byStatus(status)

    suspend fun recent(): List<TaskEntity> = dao.recent()
}
```

`Entities.kt` 追加（补齐设计文档要求的 tool_call 与 task_step 两张表）：

```kotlin
@Entity(tableName = "task_step")
data class TaskStepEntity(
    @PrimaryKey val id: String,
    val taskId: String,
    val order: Int,
    val title: String,
    val detail: String,
    val status: String,
)

@Entity(tableName = "tool_call")
data class ToolCallEntity(
    @PrimaryKey val id: String,
    val messageId: String,
    val name: String,
    val argumentsJson: String,
    val resultSummary: String,
    val risk: String,
    val ok: Boolean,
    val durationMs: Long,
)
```

`Daos.kt` 追加：

```kotlin
@Dao
interface StepDao {
    @Query("SELECT * FROM task_step WHERE taskId = :taskId ORDER BY `order`")
    suspend fun byTask(taskId: String): List<TaskStepEntity>

    @Upsert
    suspend fun upsertAll(items: List<TaskStepEntity>)
}

@Dao
interface ToolCallDao {
    @Query("SELECT * FROM tool_call WHERE messageId = :messageId")
    suspend fun byMessage(messageId: String): List<ToolCallEntity>

    @Upsert
    suspend fun upsert(entity: ToolCallEntity)
}
```

`ZhuzhuDatabase` 的 `entities` 增加 `TaskStepEntity::class`、`ToolCallEntity::class`，`version = 2`，并暴露 `abstract fun stepDao(): StepDao`、`abstract fun toolCallDao(): ToolCallDao`。`TaskRepository.saveSteps(taskId, steps)` 在任务结束时把 `TaskSessionStore` 的步骤快照写入 `task_step`。

`TaskSessionStore`（内存实时状态，UI 直接订阅）：

```kotlin
package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.AgentStep
import com.zhuzhu.copilot.model.AgentTask
import com.zhuzhu.copilot.model.StepStatus
import com.zhuzhu.copilot.model.TaskStatus
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

object TaskSessionStore {
    private val _current = MutableStateFlow<AgentTask?>(null)
    val current: StateFlow<AgentTask?> = _current.asStateFlow()

    fun start(id: String, title: String) {
        _current.value = AgentTask(id = id, title = title, status = TaskStatus.RUNNING)
    }

    fun addStep(title: String, detail: String = "") {
        val task = _current.value ?: return
        val next = AgentStep(
            id = "step-${task.steps.size + 1}",
            order = task.steps.size + 1,
            title = title,
            detail = detail,
            status = StepStatus.RUNNING,
        )
        _current.value = task.copy(steps = task.steps + next)
    }

    fun finish(status: TaskStatus, reason: String = "") {
        _current.value = _current.value?.copy(status = status, failureReason = reason)
    }

    fun clear() {
        _current.value = null
    }
}
```

- [ ] **Step 4: 实现任务页**

`TasksViewModel.kt`：

```kotlin
package com.zhuzhu.copilot.feature.tasks

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.zhuzhu.copilot.agent.AgentEngine
import com.zhuzhu.copilot.agent.TaskSessionStore
import com.zhuzhu.copilot.data.repo.TaskRepository
import com.zhuzhu.copilot.model.AgentTask
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class TaskListState(
    val filter: Int = 0,
    val completed: List<String> = emptyList(),
    val aborted: List<String> = emptyList(),
)

@HiltViewModel
class TasksViewModel @Inject constructor(
    private val repository: TaskRepository,
    private val engine: AgentEngine,
) : ViewModel() {
    private val _list = MutableStateFlow(TaskListState())
    val list: StateFlow<TaskListState> = _list.asStateFlow()
    val current: StateFlow<AgentTask?> = TaskSessionStore.current

    init { refresh() }

    fun refresh() {
        viewModelScope.launch {
            _list.value = TaskListState(
                completed = repository.byStatus("DONE").map { "${it.title} · 已完成" },
                aborted = repository.byStatus("ABORTED").map { "${it.title} · 已中止" },
            )
        }
    }

    fun selectFilter(index: Int) { _list.value = _list.value.copy(filter = index) }

    fun abort() {
        engine.cancel()
        TaskSessionStore.finish(com.zhuzhu.copilot.model.TaskStatus.ABORTED)
    }
}
```

`TasksScreen.kt`：

```kotlin
package com.zhuzhu.copilot.feature.tasks

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.zhuzhu.copilot.model.AgentStep
import com.zhuzhu.copilot.ui.GlassSurface
import com.zhuzhu.copilot.ui.GlassTokens
import com.zhuzhu.copilot.ui.GlassTopBar
import dev.chrisbanes.haze.HazeState

@Composable
fun TasksScreen(hazeState: HazeState, viewModel: TasksViewModel = hiltViewModel()) {
    val list by viewModel.list.collectAsState()
    val current by viewModel.current.collectAsState()

    Column(Modifier.fillMaxSize()) {
        GlassTopBar("任务", hazeState)
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("进行中", "已完成", "已中止").forEachIndexed { index, label ->
                val active = list.filter == index
                Text(
                    label,
                    color = if (active) Color.White else GlassTokens.TextSecondary,
                    modifier = Modifier
                        .weight(1f)
                        .background(if (active) GlassTokens.BlueHighlight.copy(alpha = 0.55f) else GlassTokens.BgPanelAlt, RoundedCornerShape(10.dp))
                        .padding(vertical = 7.dp),
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                )
            }
        }

        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (list.filter == 0) {
                current?.let { task ->
                    item {
                        GlassSurface(Modifier.fillMaxWidth(), hazeState) {
                            Column(Modifier.padding(12.dp)) {
                                Text(task.title, color = GlassTokens.TextPrimary, fontWeight = FontWeight.SemiBold)
                                Text("${task.status}", color = GlassTokens.TextSecondary, style = MaterialTheme.typography.bodySmall)
                                task.steps.forEach { step -> StepRow(step) }
                                Text(
                                    "中止任务",
                                    color = GlassTokens.Warning,
                                    modifier = Modifier.padding(top = 8.dp),
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            } else {
                val items = if (list.filter == 1) list.completed else list.aborted
                items(items) { line ->
                    GlassSurface(Modifier.fillMaxWidth(), hazeState) {
                        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(line, color = GlassTokens.TextPrimary)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StepRow(step: AgentStep) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
        Text("·", color = GlassTokens.BlueSoft, modifier = Modifier.padding(end = 6.dp))
        Column {
            Text(step.title, color = GlassTokens.TextPrimary, style = MaterialTheme.typography.bodySmall)
            if (step.detail.isNotEmpty()) {
                Text(step.detail, color = GlassTokens.TextTertiary, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
```

`TasksViewModel.abort()` 里的 `engine.cancel()` 由 HUD 同样调用，保证两个入口行为一致。

- [ ] **Step 5: 接入 MainActivity 与任务会话**

`TasksPlaceholder()` 替换为 `TasksScreen(hazeState)`；删除 `TasksPlaceholder.kt`。

`ChatViewModel.send()` 内接入实时任务状态，保证任务页“进行中”有内容：

```kotlin
TaskSessionStore.start(id = UUID.randomUUID().toString(), title = prompt)
...
is AgentEvent.ToolStarted -> TaskSessionStore.addStep(event.call.name)
is AgentEvent.ToolResult -> if (!event.ok) TaskSessionStore.addStep("${event.call.name} 失败：${event.summary}")
...
TaskSessionStore.finish(if (state.error.isEmpty()) TaskStatus.DONE else TaskStatus.FAILED, state.error)
repository.save(title = prompt, status = TaskSessionStore.current.value?.status?.name ?: "FAILED")
```

`DatabaseProvider` 增加 `taskDao()`（复用同一个 `ZhuzhuDatabase` 单例），`AppModule` 提供：

```kotlin
@Provides
@Singleton
fun taskRepository(@ApplicationContext context: Context): TaskRepository =
    TaskRepository(DatabaseProvider.taskDao(context))
```

- [ ] **Step 6: 测试与构建**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "feat(tasks): 任务页、步骤时间线与会话记录"
```

---

### Task 16: 设置页、权限引导、模型配置与账号登录

**Files:**
- Create: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/SettingsViewModel.kt`
- Create: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/SettingsScreen.kt`
- Create: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/ProviderScreen.kt`
- Create: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/OnboardingScreen.kt`
- Create: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/repo/SettingsRepository.kt`
- Create: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/auth/MobileAuthClient.kt`
- Create: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/LoginWebViewActivity.kt`
- Modify: `mobile/feature/settings/src/main/AndroidManifest.xml`
- Modify: `mobile/app/src/main/AndroidManifest.xml`
- Delete: `mobile/feature/settings/src/main/kotlin/com/zhuzhu/copilot/feature/settings/SettingsPlaceholder.kt`
- Test: `mobile/core/data/src/test/kotlin/com/zhuzhu/copilot/data/SettingsRepositoryTest.kt`

- [ ] **Step 1: 写失败测试（DataStore 设置项持久化）**

```kotlin
package com.zhuzhu.copilot.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.zhuzhu.copilot.data.repo.SettingsRepository
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class SettingsRepositoryTest {
    @Test
    fun `confirm mode round trips`() = runTest {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val repo = SettingsRepository(context)
        repo.setConfirmMode("EVERY_STEP")
        assertEquals("EVERY_STEP", repo.confirmMode())
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
```

Expected：`SettingsRepository` 未定义。

- [ ] **Step 3: 实现设置仓储与账号客户端**

`SettingsRepository.kt`：

```kotlin
package com.zhuzhu.copilot.data.repo

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first

private val Context.settingsStore by preferencesDataStore("settings")

class SettingsRepository(private val context: Context) {
    private val confirmModeKey = stringPreferencesKey("confirm_mode")
    private val glassLevelKey = stringPreferencesKey("glass_level")
    private val ttsKey = stringPreferencesKey("tts_enabled")
    private val languageKey = stringPreferencesKey("language")

    suspend fun setConfirmMode(value: String) = context.settingsStore.edit { it[confirmModeKey] = value }
    suspend fun confirmMode(): String = context.settingsStore.data.first()[confirmModeKey] ?: "SENSITIVE_AND_ABOVE"

    suspend fun setGlassLevel(value: String) = context.settingsStore.edit { it[glassLevelKey] = value }
    suspend fun glassLevel(): String = context.settingsStore.data.first()[glassLevelKey] ?: "STANDARD"

    suspend fun setTtsEnabled(value: Boolean) = context.settingsStore.edit { it[ttsKey] = value.toString() }
    suspend fun ttsEnabled(): Boolean = (context.settingsStore.data.first()[ttsKey] ?: "false").toBoolean()

    suspend fun setLanguage(value: String) = context.settingsStore.edit { it[languageKey] = value }
    suspend fun language(): String = context.settingsStore.data.first()[languageKey] ?: "zh"
}
```

`mobile/core/data/src/main/assets/config.json`（服务端地址与平台标识外置，不在代码里写死）：

```json
{
  "authServer": "https://chentian.dpdns.org/authapp",
  "platform": "zhuzhu-copilot-mobile",
  "version": "1.0.0"
}
```

`AppConfig.kt`：

```kotlin
package com.zhuzhu.copilot.data

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class AppConfig(
    val authServer: String,
    val platform: String,
    val version: String,
) {
    companion object {
        fun load(context: Context): AppConfig =
            Json { ignoreUnknownKeys = true }.decodeFromString(
                context.assets.open("config.json").bufferedReader().use { it.readText() }
            )
    }
}
```

`MobileAuthClient.kt`（真实调用 auth-server，禁止 mock）：

```kotlin
package com.zhuzhu.copilot.data.auth

import android.content.Context
import com.zhuzhu.copilot.data.AppConfig
import com.zhuzhu.copilot.data.SecretStore
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class MobileAuthClient(
    private val secrets: SecretStore,
    private val config: AppConfig,
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build(),
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val server: String get() = config.authServer.trimEnd('/')

    fun issueState(): String {
        val url = "$server/api/auth/state?platform=${config.platform}&version=${config.version}"
        val request = Request.Builder().url(url).post("{}".toRequestBody(JSON)).build()
        http.newCall(request).execute().use { response ->
            require(response.isSuccessful) { "state 请求失败：${response.code}" }
            val body = response.body?.string().orEmpty()
            return json.parseToJsonElement(body).let { root ->
                root.asObjectOrThrow()["data"].asObjectOrThrow()["state"].toString().trim('"')
            }
        }
    }

    fun loginUrl(state: String): String =
        "$server/static/login/index.html?platform=${config.platform}&state=$state&version=${config.version}"

    fun pollResult(state: String): String? {
        val url = "$server/api/auth/login_result?state=$state"
        repeat(80) {
            http.newCall(Request.Builder().url(url).get().build()).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    val token = runCatching {
                        json.parseToJsonElement(body).asObjectOrThrow()["data"].asObjectOrThrow()["token"].toString().trim('"')
                    }.getOrNull()
                    if (token != null && token != "null" && token.isNotEmpty()) return token
                }
            }
            Thread.sleep(1500)
        }
        return null
    }

    fun saveToken(token: String) = secrets.put("auth_token", token)
    fun token(): String? = secrets.get("auth_token")
    fun logout() = secrets.remove("auth_token")

    private fun kotlinx.serialization.json.JsonElement.asObjectOrThrow() =
        this as kotlinx.serialization.json.JsonObject

    companion object {
        private val JSON = "application/json".toMediaType()
    }
}
```

`LoginWebViewActivity.kt`：

```kotlin
package com.zhuzhu.copilot.feature.settings

import android.app.Activity
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.zhuzhu.copilot.data.auth.MobileAuthClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class LoginWebViewActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val config = com.zhuzhu.copilot.data.AppConfig.load(this)
        val client = MobileAuthClient(com.zhuzhu.copilot.data.SecretStore(this), config)
        val state = runCatching { client.issueState() }.getOrElse {
            finish()
            return
        }
        setContent {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { context ->
                    WebView(context).apply {
                        webViewClient = WebViewClient()
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        loadUrl(client.loginUrl(state))
                    }
                },
            )
        }
        CoroutineScope(Dispatchers.IO).launch {
            val token = client.pollResult(state)
            if (token != null) client.saveToken(token)
            runOnUiThread { setResult(if (token != null) Activity.RESULT_OK else Activity.RESULT_CANCELED); finish() }
        }
    }
}
```

Manifest（settings 模块）：

```xml
<application>
    <activity android:name=".LoginWebViewActivity" android:exported="false" />
</application>
```

`ProviderScreen` 提供预设列表、Base URL/模型名手填、API Key 输入与保存；保存走 `ProviderRepository`（密钥入 Keystore），保存后刷新 `ChatViewModel.refreshProviderState()`。

`OnboardingScreen` 是 5 步向导，每步一个状态卡：

1. 欢迎（能力说明）
2. 无障碍：`Settings.ACTION_ACCESSIBILITY_SETTINGS`
3. 通知使用权：`Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS`；短信权限用 `ActivityResultContracts.RequestMultiplePermissions`
4. 麦克风：`ActivityResultContracts.RequestPermission`
5. 模型：跳 `ProviderScreen`，完成后写入 DataStore `onboarded=true`

- [ ] **Step 4: 设置主界面**

`SettingsScreen` 分组：

- 模型与账号：当前服务商/模型、剩余积分（调用 `/api/auth/me` 返回字段 `points`，失败显示“未登录”）、登录/退出、自填 Key 入口
- 权限：无障碍（已授权/未授权 + 去开启）、通知使用权、短信、麦克风
- 自动化：确认模式（三选一）、玻璃强度（三档）、失败重试开关
- 语音：TTS 播报开关
- 隐私：诊断日志开关（默认关闭，开启后所有日志先经 `Redactor.mask` 脱敏）、清除全部本地数据（会话、任务、记忆；密钥保留）
- 关于：版本号、隐私说明入口

状态检测：

```kotlin
fun accessibilityEnabled(context: Context): Boolean {
    val manager = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as android.view.accessibility.AccessibilityManager
    return manager.getEnabledAccessibilityServiceList(android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
        .any { it.resolveInfo.serviceInfo.packageName == context.packageName }
}
```

- [ ] **Step 5: 接入 MainActivity**

`SettingsPlaceholder()` 替换为 `SettingsScreen(hazeState)`；应用启动时读取 DataStore `onboarded`，未完成则先显示 `OnboardingScreen`。

- [ ] **Step 6: 测试与构建**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 7: 真机验收**

1. 全新安装 → 走完 5 步引导 → 无障碍与通知使用权确实在系统设置中开启
2. 配置 DeepSeek Key → 保存 → 回对话页发消息，收到真实回复
3. 登录内置账号（若服务端已登记该 platform）→ 显示积分；未登记时登录页会拒绝，此时确认自填 Key 路径仍可用
4. 三档玻璃切换后重启应用仍保持

- [ ] **Step 8: 提交**

```powershell
git add mobile
git commit -m "feat(settings): 权限引导、模型配置、账号登录与设置页"
```

---

### Task 17: 语音输入与 TTS 播报

**Files:**
- Create: `mobile/feature/chat/src/main/kotlin/com/zhuzhu/copilot/feature/chat/VoiceInput.kt`
- Create: `mobile/feature/chat/src/main/kotlin/com/zhuzhu/copilot/feature/chat/TtsSpeaker.kt`
- Modify: `mobile/feature/chat/src/main/kotlin/com/zhuzhu/copilot/feature/chat/ChatScreen.kt`
- Modify: `mobile/feature/chat/src/main/kotlin/com/zhuzhu/copilot/feature/chat/ChatViewModel.kt`
- Test: `mobile/feature/chat/src/test/kotlin/com/zhuzhu/copilot/feature/chat/TtsSegmenterTest.kt`

- [ ] **Step 1: 写分段测试（按句末标点切分，控制合成频率）**

```kotlin
package com.zhuzhu.copilot.feature.chat

import org.junit.Assert.assertEquals
import org.junit.Test

class TtsSegmenterTest {
    @Test
    fun `splits on sentence enders`() {
        val parts = TtsSegmenter.split("你好。今天过得怎么样？我在。")
        assertEquals(listOf("你好。", "今天过得怎么样？", "我在。"), parts)
    }

    @Test
    fun `keeps long sentence under 60 chars`() {
        val text = "啊".repeat(80) + "。"
        val parts = TtsSegmenter.split(text)
        assertEquals(2, parts.size)
        assert(parts.all { it.length <= 60 })
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :feature:chat:testDebugUnitTest
```

Expected：`TtsSegmenter` 未定义。

- [ ] **Step 3: 实现语音与播报**

`VoiceInput.kt` 用系统识别 Intent：

```kotlin
package com.zhuzhu.copilot.feature.chat

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable

@Composable
fun rememberVoiceInput(onResult: (String) -> Unit): () -> Unit {
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
                ?.let(onResult)
        }
    }
    return {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        }
        launcher.launch(intent)
    }
}
```

`TtsSegmenter.kt`：

```kotlin
package com.zhuzhu.copilot.feature.chat

object TtsSegmenter {
    private val enders = setOf('。', '！', '？', '…', '!', '?')

    fun split(text: String): List<String> {
        val out = mutableListOf<String>()
        val buffer = StringBuilder()
        for (ch in text) {
            buffer.append(ch)
            if (ch in enders || buffer.length >= 60) {
                out += buffer.toString()
                buffer.clear()
            }
        }
        if (buffer.isNotEmpty()) out += buffer.toString()
        return out.filter { it.isNotBlank() }
    }
}
```

`TtsSpeaker.kt`：

```kotlin
package com.zhuzhu.copilot.feature.chat

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

class TtsSpeaker(context: Context) : TextToSpeech.OnInitListener {
    private val tts = TextToSpeech(context, this)
    private var ready = false

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.SIMPLIFIED_CHINESE
            ready = true
        }
    }

    fun speak(text: String) {
        if (!ready) return
        TtsSegmenter.split(text).forEachIndexed { index, part ->
            tts.speak(part, if (index == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, "seg-$index")
        }
    }

    fun stop() = tts.stop()
    fun release() = tts.shutdown()
}
```

- [ ] **Step 4: 接入聊天页**

`ChatScreen` 输入框左侧加麦克风图标，点击调用 `rememberVoiceInput { input = it }`；`ChatViewModel` 完成后若 `settingsRepository.ttsEnabled()` 为真则调用 `TtsSpeaker.speak(answer)`。TTS 在 ViewModel 中持有会泄漏 Context，改为在 `ChatScreen` 里 `remember { TtsSpeaker(context) }` 并用 `DisposableEffect { onDispose { speaker.release() } }`，由 UI 层播报。

- [ ] **Step 5: 测试与构建**

```powershell
.\gradlew.bat :feature:chat:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 6: 真机验收**

点麦克风说“打开设置”，识别文本落在输入框；开启 TTS 后回复会被朗读，关闭后不发声。

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "feat(chat): 系统语音输入与分段 TTS 播报"
```

---

### Task 18: 定时任务

**Files:**
- Create: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/ScheduledTaskEntity.kt`
- Modify: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/Daos.kt`
- Modify: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/db/ZhuzhuDatabase.kt`（version = 2，新增 entity）
- Create: `mobile/core/data/src/main/kotlin/com/zhuzhu/copilot/data/repo/ScheduleRepository.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/ScheduledTaskWorker.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/tools/ScheduleTool.kt`
- Test: `mobile/core/data/src/test/kotlin/com/zhuzhu/copilot/data/ScheduleRepositoryTest.kt`

- [ ] **Step 1: 写失败测试**

```kotlin
package com.zhuzhu.copilot.data

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import com.zhuzhu.copilot.data.db.ZhuzhuDatabase
import com.zhuzhu.copilot.data.repo.ScheduleRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScheduleRepositoryTest {
    private lateinit var db: ZhuzhuDatabase
    private lateinit var repo: ScheduleRepository

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, ZhuzhuDatabase::class.java).allowMainThreadQueries().build()
        repo = ScheduleRepository(db.scheduleDao())
    }

    @After
    fun tearDown() = db.close()

    @Test
    fun `save and list enabled schedules`() = runTest {
        repo.save("s1", "每天早上提醒我带伞", intervalMinutes = 1440)
        val all = repo.enabled()
        assertEquals(1, all.size)
        assertEquals(1440L, all.first().intervalMinutes)
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
```

Expected：`scheduleDao` 未定义。

- [ ] **Step 3: 实现实体、DAO 与 Worker**

`ScheduledTaskEntity.kt`：

```kotlin
package com.zhuzhu.copilot.data.db

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "scheduled_task")
data class ScheduledTaskEntity(
    @PrimaryKey val id: String,
    val prompt: String,
    val intervalMinutes: Long,
    val enabled: Boolean,
    val nextRunAt: Long,
)
```

`Daos.kt` 追加：

```kotlin
@Dao
interface ScheduleDao {
    @Query("SELECT * FROM scheduled_task WHERE enabled = 1")
    suspend fun enabled(): List<ScheduledTaskEntity>

    @Upsert
    suspend fun upsert(entity: ScheduledTaskEntity)
}
```

`ZhuzhuDatabase` 的 `entities` 加入 `ScheduledTaskEntity::class`，`version = 2`；因为没有已发布版本，直接 `fallbackToDestructiveMigration()` 可接受（仅在开发期），release 前确认无用户数据。

`ScheduleRepository.kt`：

```kotlin
package com.zhuzhu.copilot.data.repo

import com.zhuzhu.copilot.data.db.ScheduleDao
import com.zhuzhu.copilot.data.db.ScheduledTaskEntity

class ScheduleRepository(private val dao: ScheduleDao) {
    suspend fun save(id: String, prompt: String, intervalMinutes: Long) {
        dao.upsert(
            ScheduledTaskEntity(
                id = id,
                prompt = prompt,
                intervalMinutes = intervalMinutes,
                enabled = true,
                nextRunAt = System.currentTimeMillis() + intervalMinutes * 60_000,
            )
        )
    }

    suspend fun enabled(): List<ScheduledTaskEntity> = dao.enabled()
}
```

`ScheduledTaskWorker.kt`：

```kotlin
package com.zhuzhu.copilot.service.automation

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.zhuzhu.copilot.service.automation.tools.ScheduleStore

class ScheduledTaskWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val prompt = inputData.getString("prompt") ?: return Result.failure()
        ScheduleStore.requestRun(prompt)
        return Result.success()
    }
}
```

`ScheduleStore` 保存待执行提示词，前台服务启动后读取并执行（同一进程内单例，`AtomicReference`）。

`ScheduleTool` 实现 `schedule_task`：解析 `prompt` 与 `interval_minutes`，写入 Room，并用 `WorkManager.enqueueUniquePeriodicWork` 注册周期任务：

```kotlin
val request = PeriodicWorkRequestBuilder<ScheduledTaskWorker>(intervalMinutes, TimeUnit.MINUTES)
    .setInputData(workDataOf("prompt" to prompt))
    .build()
WorkManager.getInstance(context).enqueueUniquePeriodicWork("sched-$id", ExistingPeriodicWorkPolicy.UPDATE, request)
```

工具风险等级 `SENSITIVE`，`requiredPermission = "accessibility"`（执行仍需无障碍）。

- [ ] **Step 4: 测试与构建**

```powershell
.\gradlew.bat :core:data:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 5: 真机验收**

创建一条 15 分钟间隔的测试任务“打开设置并返回”，到点后 WorkManager 拉起前台服务并执行；任务页能看到记录。

- [ ] **Step 6: 提交**

```powershell
git add mobile
git commit -m "feat(schedule): 定时任务与 WorkManager 调度"
```

---

### Task 19: 自签名 release、混淆与操作手册

**Files:**
- Create: `mobile/keystore.properties.example`
- Create: `mobile/app/proguard-rules.pro`
- Modify: `mobile/app/build.gradle.kts`（signingConfig）
- Create: `mobile/README.md`
- Modify: `mobile/.gitignore`（已含 keystore.properties / *.jks）

- [ ] **Step 1: 配置签名读取**

`app/build.gradle.kts` 在 `android {}` 前增加：

```kotlin
val keystoreFile = rootProject.file("keystore.properties")
val keystoreProps = java.util.Properties().apply {
    if (keystoreFile.exists()) keystoreFile.inputStream().use { load(it) }
}

android {
    // ... 原有配置
    signingConfigs {
        create("release") {
            if (keystoreFile.exists()) {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}
```

`keystore.properties.example`：

```properties
storeFile=../zhuzhu-mobile-release.jks
storePassword=CHANGE_ME
keyAlias=zhuzhu-mobile
keyPassword=CHANGE_ME
```

`app/proguard-rules.pro`：

```proguard
# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class **$$serializer { *; }
-keepclasseswithmembers class ** {
    kotlinx.serialization.KSerializer serializer(...);
}

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Hilt / Room 由各自 consumer rules 处理，无需手写
```

- [ ] **Step 2: 用户在 Android Studio 生成签名**

Build → Generate Signed App Bundle or APK → APK：

1. Module 选 `app`
2. Key store path 点 Create new：保存到 `C:\Users\zhuzhu\Desktop\zhuzhu Copilot\mobile\zhuzhu-mobile-release.jks`
3. Key store password、Key alias（`zhuzhu-mobile`）、Key password 各填一次；Validity 25 年
4. 勾选 **release**
5. Finish

Expected：产物在 `mobile/app/build/outputs/apk/release/app-release.apk`。

同时把密码填入 `mobile/keystore.properties`（不提交），后续命令行构建也能签名。

- [ ] **Step 3: 命令行构建验证**

```powershell
cd "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\mobile"
.\gradlew.bat clean assembleRelease
```

Expected：`BUILD SUCCESSFUL`，无 `Unable to sign` 报错。

- [ ] **Step 4: 安装与冒烟**

```powershell
adb install -r "app\build\outputs\apk\release\app-release.apk"
adb shell monkey -p com.zhuzhu.copilot.mobile -c 1
```

Expected：安装成功、应用启动无崩溃。随后手工验收：

1. 首启走完引导
2. 配置模型后发一条消息，得到真实回复
3. 开启无障碍后执行“打开设置并返回”
4. 任务页出现该任务记录
5. 断网时发送消息，界面显示明确错误而不是卡死

- [ ] **Step 5: 性能实测（不达标不发布）**

```powershell
adb shell am force-stop com.zhuzhu.copilot.mobile
adb shell am start -W -n com.zhuzhu.copilot.mobile/.MainActivity
adb shell dumpsys gfxinfo com.zhuzhu.copilot.mobile reset
```

然后在应用内快速滑动对话列表 10 秒，再执行：

```powershell
adb shell dumpsys gfxinfo com.zhuzhu.copilot.mobile
```

Expected：

- `am start -W` 输出 `TotalTime` 有具体数值，且首帧不出现空白玻璃
- `gfxinfo` 的 `95th percentile` 帧耗时不超过 16ms（60fps）
- HUD 在真机上拖动/重绘无明显卡顿；若超标，先把玻璃降级到 Quiet 再测，确认是模糊层导致并记录

- [ ] **Step 6: 写操作手册**

`mobile/README.md` 内容必须包含以下小节，命令与本文一致：

```markdown
# zhuzhu Copilot Mobile

## 打开工程
Android Studio → Open → 选择本目录 → 等待 Gradle Sync。

## 环境
- Android Studio 自带 JBR（Gradle JDK 选 jbr-21）
- SDK：Android 16 (API 36)、Build-Tools 36.0.0、Platform-Tools
- minSdk 26 / targetSdk 36

## 调试
1. Device Manager 新建 Pixel 8 / API 36 模拟器，或手机开启 USB 调试
2. 选择 app 配置，点 Run
3. Logcat 过滤：AgentEngine / Accessibility / Overlay / Automation
4. 无障碍服务在「设置 → 无障碍」中开关；通知使用权在「设置 → 通知使用权」

## 打包
1. Build → Generate Signed App Bundle or APK → APK
2. Create new keystore（RSA 2048，有效期 ≥ 25 年）
3. 勾选 release，输出 app/build/outputs/apk/release/app-release.apk
4. 或命令行：.\gradlew.bat assembleRelease（需 keystore.properties）

## 安装
adb install -r app\build\outputs\apk\release\app-release.apk

## 首次使用
按引导开启无障碍、通知使用权、短信、麦克风；配置模型（内置账号登录或自填 API Key）。

## 常见问题
- 国产 ROM 杀后台：在系统设置中允许自启动与后台运行
- 部分机型不支持无障碍截图：自动退化为节点模式
- 登录内置账号提示平台不支持：先在设置中改用自填 API Key
```

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "build(mobile): 自签名 release 配置与操作手册"
```

---

## 计划 C 完成标准

- 任务页、设置页、引导页在真机上可用，无空白占位
- 语音输入与 TTS 可开关
- 定时任务能按周期触发并留下记录
- `assembleRelease` 成功，自签名 APK 可安装启动
- `mobile/README.md` 覆盖打开、调试、打包、安装、常见问题

## 计划 C 明确不做

- 应用商店上架、AAB 发布、崩溃上报
- 远程 MCP、技能包、多 Agent 工作流
- 桌面端与手机端的联动
