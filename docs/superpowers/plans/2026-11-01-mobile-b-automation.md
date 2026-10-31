# zhuzhu Copilot Mobile · 计划 B：无障碍自动化层

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让应用真正能操控手机：无障碍服务读取节点树、执行点击/输入/滚动/全局动作，节点失败时用截图 + 视觉坐标兑底，并在执行期间显示玻璃 HUD 与目标高亮。

**Architecture:** `AccessibilityService` 负责节点与动作；`AutomationForegroundService` 跑 Agent 循环并维护 HUD；工具以 `AgentTool` 实现经 Hilt `@IntoSet` 注入注册表。快照拍平为 `UiNode` 纯数据，便于 JVM 单测。

**Tech Stack:** AccessibilityService、WindowManager Overlay、MediaProjection（API 26–29 兑底）、Haze（应用内玻璃）、Compose。

**前置：** 计划 A 全部完成，`:app:assembleDebug` 通过。

---

### Task 9: 无障碍服务、节点快照与查找

**Files:**
- Create: `mobile/service/accessibility/src/main/AndroidManifest.xml`
- Create: `mobile/service/accessibility/src/main/res/xml/accessibility_service.xml`
- Create: `mobile/service/accessibility/src/main/res/values/strings.xml`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/UiNode.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/NodeSnapshotter.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/NodeFinder.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/AgentAccessibilityService.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/NodeSnapshotterTest.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/NodeFinderTest.kt`

- [ ] **Step 1: 写快照失败测试**

```kotlin
package com.zhuzhu.copilot.service.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NodeSnapshotterTest {
    private fun node(
        text: String = "",
        left: Int = 0, top: Int = 0, right: Int = 100, bottom: Int = 40,
        clickable: Boolean = false,
        visible: Boolean = true,
        children: List<UiNode> = emptyList(),
    ) = UiNode(
        text = text, desc = "", className = "android.widget.TextView", viewId = "",
        left = left, top = top, right = right, bottom = bottom,
        clickable = clickable, editable = false, scrollable = false,
        checked = false, enabled = true, visible = visible,
        packageName = "com.demo", children = children,
    )

    @Test
    fun `prunes invisible and zero area nodes`() {
        val root = node(children = listOf(node(visible = false), node(right = 0, bottom = 0), node(text = "可见")))
        val rows = NodeSnapshotter().flatten(root)
        assertEquals(1, rows.size)
        assertEquals("可见", rows.first().node.text)
    }

    @Test
    fun `dedupes identical text and bounds`() {
        val root = node(children = listOf(node(text = "重复"), node(text = "重复")))
        assertEquals(1, NodeSnapshotter().flatten(root).size)
    }

    @Test
    fun `respects max node cap`() {
        val children = (1..1000).map { node(text = "n$it", top = it * 40) }
        val rows = NodeSnapshotter(maxNodes = 800).flatten(node(children = children))
        assertEquals(800, rows.size)
        assertTrue(rows.first().idx == 0)
    }
}
```

- [ ] **Step 2: 写查找失败测试**

```kotlin
package com.zhuzhu.copilot.service.accessibility

import org.junit.Assert.assertEquals
import org.junit.Test

class NodeFinderTest {
    private val rows = listOf(
        NodeRow(0, UiNode("发送", "", "Button", "", 0, 0, 100, 40, true, false, false, false, true, true, "com.demo")),
        NodeRow(1, UiNode("发送消息", "", "TextView", "", 0, 50, 100, 90, false, false, false, false, true, true, "com.demo")),
        NodeRow(2, UiNode("取消", "", "Button", "", 0, 100, 100, 140, true, false, false, false, true, true, "com.demo")),
    )

    @Test
    fun `exact text wins over contains`() {
        val result = NodeFinder().find(rows, NodeQuery(text = "发送"))
        assertEquals(0, result.first().row.idx)
    }

    @Test
    fun `contains matches`() {
        val result = NodeFinder().find(rows, NodeQuery(text = "消息"))
        assertEquals(1, result.size)
    }
}
```

- [ ] **Step 3: 确认失败**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`UiNode`、`NodeSnapshotter` 未定义。

- [ ] **Step 4: 实现 UiNode 与快照**

`UiNode.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

data class UiNode(
    val text: String,
    val desc: String,
    val className: String,
    val viewId: String,
    val left: Int,
    val top: Int,
    val right: Int,
    val bottom: Int,
    val clickable: Boolean,
    val editable: Boolean,
    val scrollable: Boolean,
    val checked: Boolean,
    val enabled: Boolean,
    val visible: Boolean,
    val packageName: String,
    val children: List<UiNode> = emptyList(),
) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top
}

data class NodeRow(val idx: Int, val node: UiNode)
```

`NodeSnapshotter.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

class NodeSnapshotter(private val maxNodes: Int = 800) {
    fun flatten(root: UiNode?): List<NodeRow> {
        if (root == null) return emptyList()
        val out = mutableListOf<UiNode>()
        val seen = HashSet<String>()
        fun visit(node: UiNode) {
            if (out.size >= maxNodes) return
            val usable = node.visible && node.width >= 20 && node.height >= 20
            if (usable) {
                val signature = "${node.text}|${node.desc}|${node.left},${node.top},${node.right},${node.bottom}"
                if (seen.add(signature)) out += node
            }
            node.children.forEach(::visit)
        }
        visit(root)
        return out.mapIndexed { index, node -> NodeRow(index, node) }
    }
}
```

`NodeFinder.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

data class NodeQuery(
    val text: String? = null,
    val desc: String? = null,
    val className: String? = null,
    val viewId: String? = null,
    val clickableOnly: Boolean = false,
)

data class ScoredRow(val row: NodeRow, val score: Int)

class NodeFinder {
    fun find(rows: List<NodeRow>, query: NodeQuery, limit: Int = 15): List<ScoredRow> {
        val scored = rows.mapNotNull { row ->
            val node = row.node
            if (query.clickableOnly && !node.clickable) return@mapNotNull null
            if (query.className != null && !node.className.contains(query.className)) return@mapNotNull null
            if (query.viewId != null && node.viewId != query.viewId) return@mapNotNull null
            val textScore = score(node.text, query.text)
            val descScore = score(node.desc, query.desc)
            if (query.text != null && textScore == 0) return@mapNotNull null
            if (query.desc != null && descScore == 0) return@mapNotNull null
            ScoredRow(row, maxOf(textScore, descScore))
        }
        return scored.sortedWith(compareByDescending<ScoredRow> { it.score }.thenBy { it.row.idx }).take(limit)
    }

    private fun score(value: String, query: String?): Int {
        if (query.isNullOrBlank()) return 0
        return when {
            value == query -> 100
            value.startsWith(query) -> 80
            value.contains(query, ignoreCase = true) -> 60
            else -> 0
        }
    }
}
```

- [ ] **Step 5: 无障碍服务与配置**

`accessibility_service.xml`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<accessibility-service xmlns:android="http://schemas.android.com/apk/res/android"
    android:accessibilityEventTypes="typeWindowStateChanged|typeWindowContentChanged|typeViewClicked|typeViewScrolled"
    android:accessibilityFeedbackType="feedbackGeneric"
    android:accessibilityFlags="flagRetrieveInteractiveWindows|flagReportViewIds|flagIncludeNotImportantViews"
    android:canRetrieveWindowContent="true"
    android:canPerformGestures="true"
    android:notificationTimeout="100"
    android:description="@string/accessibility_description" />
```

`strings.xml`：

```xml
<resources>
    <string name="accessibility_description">允许 zhuzhu Copilot 读取屏幕内容并模拟点击、输入与滚动，用于执行你下达的任务。随时可在系统设置中关闭。</string>
    <string name="accessibility_label">zhuzhu Copilot 自动化</string>
</resources>
```

Manifest：

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application>
        <service
            android:name=".AgentAccessibilityService"
            android:exported="false"
            android:label="@string/accessibility_label"
            android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE">
            <intent-filter>
                <action android:name="android.accessibilityservice.AccessibilityService" />
            </intent-filter>
            <meta-data
                android:name="android.accessibilityservice"
                android:resource="@xml/accessibility_service" />
        </service>
    </application>
</manifest>
```

`AgentAccessibilityService.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

object AccessibilityBridge {
    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected.asStateFlow()
    private val _packageName = MutableStateFlow("")
    val foregroundPackage: StateFlow<String> = _packageName.asStateFlow()

    var service: AgentAccessibilityService? = null
        private set

    internal fun attach(service: AgentAccessibilityService) {
        this.service = service
        _connected.value = true
    }

    internal fun detach() {
        service = null
        _connected.value = false
        _packageName.value = ""
    }

    internal fun onWindowChanged(pkg: String) {
        _packageName.value = pkg
    }
}

class AgentAccessibilityService : AccessibilityService() {
    private val snapshotter = NodeSnapshotter()
    private val registry = NodeRegistry()

    override fun onServiceConnected() {
        AccessibilityBridge.attach(this)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val pkg = event?.packageName?.toString().orEmpty()
        if (pkg.isNotEmpty()) AccessibilityBridge.onWindowChanged(pkg)
        registry.invalidate()
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        AccessibilityBridge.detach()
        super.onDestroy()
    }

    fun currentRoot(): AccessibilityNodeInfo? =
        rootInActiveWindow ?: windows.firstOrNull()?.root

    fun snapshot(): List<NodeRow> {
        val rawRoot = currentRoot() ?: return emptyList()
        val rows = snapshotter.flatten(NodeReader.read(rawRoot))
        registry.rebuild(rawRoot, rows, rawRoot.packageName?.toString().orEmpty())
        return rows
    }

    fun nodeAt(idx: Int): AccessibilityNodeInfo? = registry.nodeAt(idx)

    companion object {
        fun isConnected(): Boolean = AccessibilityBridge.connected.value
    }
}
```

`NodeReader.kt`（递归读取 bounds 与子节点）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo

object NodeReader {
    fun read(node: AccessibilityNodeInfo): UiNode {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        val children = buildList {
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { add(read(it)) }
            }
        }
        return UiNode(
            text = node.text?.toString().orEmpty(),
            desc = node.contentDescription?.toString().orEmpty(),
            className = node.className?.toString().orEmpty(),
            viewId = node.viewIdResourceName.orEmpty(),
            left = rect.left,
            top = rect.top,
            right = rect.right,
            bottom = rect.bottom,
            clickable = node.isClickable,
            editable = node.isEditable,
            scrollable = node.isScrollable,
            checked = node.isChecked,
            enabled = node.isEnabled,
            visible = node.isVisibleToUser,
            packageName = node.packageName?.toString().orEmpty(),
            children = children,
        )
    }
}
```

`NodeRegistry.kt`（维护 idx → 真实节点，顺序与快照一致）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo

class NodeRegistry {
    private var nodes: List<AccessibilityNodeInfo> = emptyList()

    fun rebuild(root: AccessibilityNodeInfo, rows: List<NodeRow>, packageName: String) {
        nodes = flattenNodes(root, rows.size)
    }

    fun nodeAt(idx: Int): AccessibilityNodeInfo? = nodes.getOrNull(idx)

    fun invalidate() { nodes = emptyList() }

    private fun flattenNodes(root: AccessibilityNodeInfo, cap: Int): List<AccessibilityNodeInfo> {
        val out = mutableListOf<AccessibilityNodeInfo>()
        fun visit(node: AccessibilityNodeInfo) {
            if (out.size >= cap) return
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (node.isVisibleToUser && rect.width() >= 20 && rect.height() >= 20) out += node
            for (i in 0 until node.childCount) node.getChild(i)?.let(::visit)
        }
        visit(root)
        return out
    }
}
```

- [ ] **Step 6: 测试通过**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`BUILD SUCCESSFUL`，快照与查找测试通过。

- [ ] **Step 7: 提交**

```powershell
git add mobile/service/accessibility
git commit -m "feat(accessibility): 无障碍服务、节点快照与查找"
```

---

### Task 10: 动作执行、窗口指纹与校验

**Files:**
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/NodeActions.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/WindowFingerprint.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/ActionVerifier.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/WindowFingerprintTest.kt`

- [ ] **Step 1: 写指纹测试**

```kotlin
package com.zhuzhu.copilot.service.accessibility

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WindowFingerprintTest {
    @Test
    fun `same content yields same fingerprint`() {
        val rows = listOf(
            NodeRow(0, UiNode("发送", "", "Button", "", 0, 0, 100, 40, true, false, false, false, true, true, "com.demo")),
        )
        assertTrue(WindowFingerprint.of("com.demo", rows) == WindowFingerprint.of("com.demo", rows))
    }

    @Test
    fun `different package yields different fingerprint`() {
        val rows = listOf(
            NodeRow(0, UiNode("发送", "", "Button", "", 0, 0, 100, 40, true, false, false, false, true, true, "com.demo")),
        )
        assertFalse(WindowFingerprint.of("com.demo", rows) == WindowFingerprint.of("com.other", rows))
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`WindowFingerprint` 未定义。

- [ ] **Step 3: 实现指纹与动作**

`WindowFingerprint.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

object WindowFingerprint {
    fun of(packageName: String, rows: List<NodeRow>): String {
        val text = rows.joinToString("|") { "${it.node.text}${it.node.desc}" }
        return "$packageName:${rows.size}:${text.hashCode()}"
    }
}
```

`NodeActions.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo

class NodeActions(private val service: AgentAccessibilityService) {

    fun click(idx: Int): Boolean {
        val node = service.nodeAt(idx) ?: return false
        val target = clickableAncestor(node) ?: return false
        return target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    fun longClick(idx: Int): Boolean {
        val node = service.nodeAt(idx) ?: return false
        val target = clickableAncestor(node) ?: return false
        return target.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
    }

    fun setText(idx: Int, text: String, submit: Boolean): Boolean {
        val node = service.nodeAt(idx) ?: return false
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (ok && submit) node.performAction(AccessibilityNodeInfo.ACTION_IME_ENTER)
        return ok
    }

    fun scroll(idx: Int?, forward: Boolean): Boolean {
        val node = idx?.let { service.nodeAt(it) } ?: service.currentRoot() ?: return false
        return node.performAction(
            if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
        )
    }

    fun global(action: GlobalAction): Boolean =
        service.performGlobalAction(
            when (action) {
                GlobalAction.BACK -> AccessibilityService.GLOBAL_ACTION_BACK
                GlobalAction.HOME -> AccessibilityService.GLOBAL_ACTION_HOME
                GlobalAction.RECENTS -> AccessibilityService.GLOBAL_ACTION_RECENTS
                GlobalAction.NOTIFICATIONS -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
                GlobalAction.QUICK_SETTINGS -> AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS
            }
        )

    fun tap(x: Float, y: Float): Boolean = gesture(
        GestureDescription.StrokeDescription(
            Path().apply { moveTo(x, y) },
            0L,
            60L,
        )
    )

    fun longPress(x: Float, y: Float, durationMs: Long = 600L): Boolean = gesture(
        GestureDescription.StrokeDescription(
            Path().apply { moveTo(x, y) },
            0L,
            durationMs,
        )
    )

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long = 300L): Boolean {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        return gesture(GestureDescription.StrokeDescription(path, 0L, durationMs))
    }

    private fun gesture(stroke: GestureDescription.StrokeDescription): Boolean {
        val description = GestureDescription.Builder().addStroke(stroke).build()
        return service.dispatchGesture(description, null, null)
    }

    private fun clickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var current: AccessibilityNodeInfo? = node
        while (current != null) {
            if (current.isClickable && current.isEnabled) return current
            current = current.parent
        }
        return null
    }
}

enum class GlobalAction { BACK, HOME, RECENTS, NOTIFICATIONS, QUICK_SETTINGS }
```

`ActionVerifier.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import kotlinx.coroutines.delay

class ActionVerifier(private val service: AgentAccessibilityService) {
    suspend fun verify(before: String, waitMs: Long = 1000L): Boolean {
        delay(waitMs)
        val rows = service.snapshot()
        val pkg = AccessibilityBridge.foregroundPackage.value
        return WindowFingerprint.of(pkg, rows) != before
    }
}
```

- [ ] **Step 4: 测试通过**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 5: 提交**

```powershell
git add mobile/service/accessibility
git commit -m "feat(accessibility): 动作执行、窗口指纹与动作校验"
```

---

### Task 11: 截图与视觉坐标兑底

**Files:**
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/CoordinateMapper.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/ScreenshotSource.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/AccessibilityScreenshotSource.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/MediaProjectionScreenshotSource.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/CapturePermissionActivity.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/CoordinateMapperTest.kt`
- Modify: `mobile/service/accessibility/src/main/AndroidManifest.xml`

- [ ] **Step 1: 写坐标换算测试**

```kotlin
package com.zhuzhu.copilot.service.accessibility

import org.junit.Assert.assertEquals
import org.junit.Test

class CoordinateMapperTest {
    @Test
    fun `maps normalized 0 to 1000 to screen pixels`() {
        val point = CoordinateMapper.normalizedToPixels(
            nx = 500, ny = 500,
            seenWidth = 1000, seenHeight = 1000,
            screenWidth = 1080, screenHeight = 2400,
        )
        assertEquals(540f, point.first, 0.01f)
        assertEquals(1200f, point.second, 0.01f)
    }

    @Test
    fun `clamps out of range`() {
        val point = CoordinateMapper.normalizedToPixels(1200, -100, 1000, 1000, 1080, 2400)
        assertEquals(1080f, point.first, 0.01f)
        assertEquals(0f, point.second, 0.01f)
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`CoordinateMapper` 未定义。

- [ ] **Step 3: 实现坐标与截图**

`CoordinateMapper.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

object CoordinateMapper {
    fun normalizedToPixels(
        nx: Float,
        ny: Float,
        seenWidth: Int,
        seenHeight: Int,
        screenWidth: Int,
        screenHeight: Int,
    ): Pair<Float, Float> {
        val reference = if (seenWidth > screenWidth || seenHeight > screenHeight) {
            screenWidth.toFloat() to screenHeight.toFloat()
        } else {
            seenWidth.toFloat() to seenHeight.toFloat()
        }
        val x = (nx / 1000f) * reference.first
        val y = (ny / 1000f) * reference.second
        return x.coerceIn(0f, screenWidth.toFloat()) to y.coerceIn(0f, screenHeight.toFloat())
    }
}
```

`ScreenshotSource.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.graphics.Bitmap

interface ScreenshotSource {
    suspend fun capture(): CaptureResult
}

data class CaptureResult(val bitmap: Bitmap?, val error: String = "")
```

`AccessibilityScreenshotSource.kt`（API 30+）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class AccessibilityScreenshotSource(private val service: AccessibilityService) : ScreenshotSource {
    override suspend fun capture(): CaptureResult {
        if (Build.VERSION.SDK_INT < 30) return CaptureResult(null, "无障碍截图需要 Android 11 及以上")
        return suspendCancellableCoroutine { cont ->
            service.takeScreenshot(
                android.view.Display.DEFAULT_DISPLAY,
                service.mainExecutor,
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(screenshot: AccessibilityService.ScreenshotResult) {
                        val bitmap = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
                            ?.copy(Bitmap.Config.ARGB_8888, false)
                        screenshot.hardwareBuffer.close()
                        cont.resume(if (bitmap == null) CaptureResult(null, "截图转换失败") else CaptureResult(bitmap))
                    }

                    override fun onFailure(errorCode: Int) {
                        cont.resume(CaptureResult(null, "截图失败，错误码 $errorCode"))
                    }
                },
            )
        }
    }
}
```

`MediaProjectionScreenshotSource.kt`（API 26–29 兑底）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.content.Context
import android.graphics.Bitmap
import android.hardware.display.DisplayManager
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.Looper
import android.util.DisplayMetrics
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class MediaProjectionScreenshotSource(
    private val context: Context,
    private val resultCode: Int,
    private val data: android.content.Intent,
) : ScreenshotSource {
    override suspend fun capture(): CaptureResult {
        val metrics = DisplayMetrics()
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
        wm.defaultDisplay.getRealMetrics(metrics)
        val reader = ImageReader.newInstance(metrics.widthPixels, metrics.heightPixels, android.graphics.PixelFormat.RGBA_8888, 1)
        val manager = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val projection: MediaProjection = manager.getMediaProjection(resultCode, data)
            ?: return CaptureResult(null, "屏幕捕获授权已失效")
        projection.createVirtualDisplay(
            "zhuzhu-capture", metrics.widthPixels, metrics.heightPixels, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.surface, null, Handler(Looper.getMainLooper()),
        )
        return suspendCancellableCoroutine { cont ->
            reader.setOnImageAvailableListener({ r ->
                val image = r.acquireLatestImage()
                val plane = image.planes[0]
                val buffer = plane.buffer
                val rowPadding = plane.rowStride - plane.pixelStride * metrics.widthPixels
                val bitmap = Bitmap.createBitmap(metrics.widthPixels + rowPadding / plane.pixelStride, metrics.heightPixels, Bitmap.Config.ARGB_8888)
                bitmap.copyPixelsFromBuffer(buffer)
                image.close()
                reader.close()
                projection.stop()
                cont.resume(CaptureResult(Bitmap.createBitmap(bitmap, 0, 0, metrics.widthPixels, metrics.heightPixels)))
            }, Handler(Looper.getMainLooper()))
        }
    }
}
```

`CapturePermissionActivity.kt`（透明中转页，仅 API < 30 使用）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle

class CapturePermissionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 30) {
            finish()
            return
        }
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_CODE)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_CODE && resultCode == RESULT_OK && data != null) {
            CapturePermissionStore.save(resultCode, data)
        }
        finish()
    }

    companion object { private const val REQUEST_CODE = 2001 }
}
```

`CapturePermissionStore` 保存最近一次授权结果（内存单例）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.content.Intent

object CapturePermissionStore {
    var resultCode: Int = 0
        private set
    var data: Intent? = null
        private set

    fun save(code: Int, intent: Intent) {
        resultCode = code
        data = intent
    }

    fun clear() {
        resultCode = 0
        data = null
    }
}
```

Manifest 增加：

```xml
<activity android:name=".CapturePermissionActivity" android:exported="false" android:theme="@android:style/Theme.Translucent.NoTitleBar" />
```

- [ ] **Step 4: 测试通过**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 5: 提交**

```powershell
git add mobile/service/accessibility
git commit -m "feat(accessibility): 坐标归一化与无障碍/MediaProjection 截图"
```

---

### Task 11B: 视觉定位器（截图 → 模型 → 归一化坐标）

**Files:**
- Create: `mobile/core/llm/src/main/kotlin/com/zhuzhu/copilot/llm/VisionModels.kt`
- Modify: `mobile/core/llm/src/main/kotlin/com/zhuzhu/copilot/llm/LlmClient.kt`（新增 `vision`）
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/VisionLocator.kt`
- Test: `mobile/core/llm/src/test/kotlin/com/zhuzhu/copilot/llm/VisionRequestTest.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/VisionResponseParserTest.kt`

- [ ] **Step 1: 写失败测试**

`VisionRequestTest.kt`：

```kotlin
package com.zhuzhu.copilot.llm

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Test

class VisionRequestTest {
    @Test
    fun `vision call returns text content`() = runTest {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setBody(
                """{"choices":[{"message":{"content":"{\"x\":512,\"y\":300}"}}]}"""
            )
        )
        server.start()
        val text = LlmClient().vision(
            baseUrl = server.url("/v1").toString().trimEnd('/'),
            apiKey = "k",
            model = "vision-model",
            prompt = "定位发送按钮",
            imageBase64 = "AAAA",
        )
        assertEquals("""{"x":512,"y":300}""", text)
        server.shutdown()
    }
}
```

`VisionResponseParserTest.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VisionResponseParserTest {
    @Test
    fun `parses json in plain text`() {
        val point = VisionResponseParser.parse("""{"x":500,"y":800}""")
        assertEquals(500f, point!!.first)
        assertEquals(800f, point.second)
    }

    @Test
    fun `parses json wrapped in prose`() {
        val point = VisionResponseParser.parse("""结果是 {"x":120,"y":640} 请点击""")
        assertEquals(120f, point!!.first)
    }

    @Test
    fun `returns null on garbage`() {
        assertNull(VisionResponseParser.parse("找不到"))
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :core:llm:test
```

Expected：`vision` 方法与 `VisionResponseParser` 未定义。

- [ ] **Step 3: 实现视觉请求**

`VisionModels.kt`：

```kotlin
package com.zhuzhu.copilot.llm

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class VisionRequest(
    val model: String,
    val messages: List<VisionMessage>,
    val max_tokens: Int = 512,
    val temperature: Double = 0.1,
)

@Serializable
data class VisionMessage(val role: String, val content: List<VisionContent>)

@Serializable
data class VisionContent(
    val type: String,
    val text: String? = null,
    @SerialName("image_url") val imageUrl: VisionImage? = null,
)

@Serializable
data class VisionImage(val url: String)

@Serializable
data class VisionResponse(val choices: List<VisionChoice> = emptyList())

@Serializable
data class VisionChoice(val message: VisionMessageOut? = null)

@Serializable
data class VisionMessageOut(val content: String? = null)
```

`LlmClient` 追加：

```kotlin
fun vision(baseUrl: String, apiKey: String, model: String, prompt: String, imageBase64: String): String {
    val url = baseUrl.trimEnd('/') + "/chat/completions"
    val body = json.encodeToString(
        VisionRequest.serializer(),
        VisionRequest(
            model = model,
            messages = listOf(
                VisionMessage(
                    role = "user",
                    content = listOf(
                        VisionContent(type = "text", text = prompt),
                        VisionContent(type = "image_url", imageUrl = VisionImage("data:image/jpeg;base64,$imageBase64")),
                    ),
                )
            ),
        ),
    )
    val request = Request.Builder()
        .url(url)
        .addHeader("Authorization", "Bearer $apiKey")
        .post(body.toRequestBody(mediaType))
        .build()
    http.newCall(request).execute().use { response ->
        if (!response.isSuccessful) throw LlmHttpException(response.code, response.body?.string().orEmpty())
        val raw = response.body?.string().orEmpty()
        return json.decodeFromString(VisionResponse.serializer(), raw)
            .choices.firstOrNull()?.message?.content.orEmpty()
    }
}
```

- [ ] **Step 4: 实现视觉定位器**

`VisionLocator.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.graphics.Bitmap
import com.zhuzhu.copilot.llm.LlmClient
import java.io.ByteArrayOutputStream
import kotlin.math.roundToInt

object VisionResponseParser {
    private val jsonObject = Regex("""\{[^{}]*"x"[^{}]*"y"[^{}]*\}""")
    private val pair = Regex(""""x"\s*:\s*(\d+)\s*,\s*"y"\s*:\s*(\d+)""")

    fun parse(text: String): Pair<Float, Float>? {
        val block = jsonObject.find(text)?.value ?: text
        val match = pair.find(block) ?: return null
        return match.groupValues[1].toFloat() to match.groupValues[2].toFloat()
    }
}

class VisionLocator(
    private val client: LlmClient,
    private val baseUrl: String,
    private val apiKey: String,
    private val model: String,
) {
    suspend fun locate(bitmap: Bitmap, target: String): Pair<Float, Float>? {
        val scaled = scale(bitmap, 720)
        val base64 = ByteArrayOutputStream().use { out ->
            scaled.compress(Bitmap.CompressFormat.JPEG, 80, out)
            android.util.Base64.encodeToString(out.toByteArray(), android.util.Base64.NO_WRAP)
        }
        val prompt = "这是一张手机截图，尺寸 ${scaled.width}x${scaled.height}。请找到「$target」，只返回 JSON：{\"x\":0-1000,\"y\":0-1000}，坐标以整屏宽高归一化。"
        val text = client.vision(baseUrl, apiKey, model, prompt, base64)
        return VisionResponseParser.parse(text)
    }

    private fun scale(bitmap: Bitmap, maxWidth: Int): Bitmap {
        if (bitmap.width <= maxWidth) return bitmap
        val ratio = maxWidth.toFloat() / bitmap.width
        return Bitmap.createScaledBitmap(bitmap, maxWidth, (bitmap.height * ratio).roundToInt(), true)
    }
}
```

- [ ] **Step 5: 测试通过**

```powershell
.\gradlew.bat :core:llm:test
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 6: 提交**

```powershell
git add mobile
git commit -m "feat(vision): 截图视觉定位与归一化坐标解析"
```

---

### Task 12: HUD、目标高亮与前台服务

**Files:**
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/OverlayController.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/HudView.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/HighlightView.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/OverlayEvents.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/AutomationForegroundService.kt`
- Modify: `mobile/service/automation/src/main/AndroidManifest.xml`
- Modify: `mobile/service/accessibility/src/main/AndroidManifest.xml`

**关键约束：** overlay 是独立窗口，Haze 的背景模糊只作用于同一窗口内内容，无法模糊第三方 App。跨窗口模糊用 API 31+ 的 `WindowManager.LayoutParams.setBlurBehindRadius(20)` + `FLAG_BLUR_BEHIND`，失败或低版本自动降级为半透明 + 光带 + 颗粒，行为与外观不退化到纯色块。

- [ ] **Step 1: 实现高亮层（不可触摸、全屏）**

`HighlightView.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View

class HighlightView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3f
        color = Color.parseColor("#B37FA0DC")
        setShadowLayer(18f, 0f, 0f, Color.parseColor("#593B5BA5"))
    }
    var target: RectF? = null
        set(value) {
            field = value
            invalidate()
        }
    private var phase = 0f
    private val frame = Runnable { phase = (phase + 0.02f) % 1f; invalidate(); postDelayed(this.frame, 33) }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        postDelayed(frame, 33)
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(frame)
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val rect = target ?: return
        val alpha = 130 + (60 * kotlin.math.sin(phase * Math.PI * 2)).toInt()
        paint.alpha = alpha.coerceIn(80, 200)
        val inset = 3f
        canvas.drawRoundRect(
            RectF(rect.left - inset, rect.top - inset, rect.right + inset, rect.bottom + inset),
            12f, 12f, paint,
        )
    }
}
```

`OverlayController.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.RectF
import android.os.Build
import android.view.Gravity
import android.view.WindowManager
import android.widget.FrameLayout

class OverlayController(private val service: AccessibilityService) {
    private val wm = service.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val highlight = HighlightView(service)
    private val hud = HudView(service, onStop = { OverlayEvents.requestStop() })
    private var attached = false

    fun show() {
        if (attached) return
        wm.addView(highlight, highlightParams())
        wm.addView(hud, hudParams())
        attached = true
    }

    fun hide() {
        if (!attached) return
        runCatching { wm.removeView(highlight) }
        runCatching { wm.removeView(hud) }
        attached = false
    }

    fun updateTarget(rect: RectF?) { highlight.target = rect }

    fun updateStatus(text: String, step: String) = hud.update(text, step)

    private fun highlightParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT,
    )

    private fun hudParams(): WindowManager.LayoutParams {
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        )
        params.gravity = Gravity.TOP
        params.x = 0
        params.y = 48
        if (Build.VERSION.SDK_INT >= 31) {
            runCatching {
                params.blurBehindRadius = 20
                params.flags = params.flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
            }
        }
        return params
    }
}
```

> 若目标 SDK 36 下 `FLAG_BLUR_BEHIND` 被系统拒绝，`addView` 会抛异常，`show()` 已用 `runCatching` 包裹单个窗口添加；需要在 `show()` 内分别 try/catch 两个窗口，失败的那个跳过并继续另一个。

`HudView.kt` 用普通 View 自绘（避免 overlay 内 Compose 生命周期复杂度）：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.view.MotionEvent
import android.view.View

class HudView(context: Context, private val onStop: () -> Unit) : View(context) {
    private val bg = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#E6E8EB"); textSize = 32f }
    private val sub = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#9AA3AE"); textSize = 24f }
    private val stopPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#C9A77E"); textSize = 26f; textAlign = Paint.Align.RIGHT
    }
    private var title = "准备执行"
    private var detail = ""
    private val stopRect = RectF()

    init { setWillNotDraw(false) }

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        setMeasuredDimension(MeasureSpec.getSize(widthSpec), 150)
    }

    fun update(title: String, detail: String) {
        this.title = title
        this.detail = detail
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val radius = 40f
        bg.shader = LinearGradient(0f, 0f, width.toFloat(), height.toFloat(),
            intArrayOf(0x33FFFFFF, 0x0AFFFFFF, 0x26FFFFFF), null, Shader.TileMode.CLAMP)
        canvas.drawRoundRect(RectF(8f, 0f, width - 8f, 140f), radius, radius, bg)
        canvas.drawText(title, 36f, 62f, text)
        canvas.drawText(detail, 36f, 104f, sub)
        stopRect.set(width - 190f, 34f, width - 36f, 106f)
        canvas.drawText("中止", width - 36f, 78f, stopPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_UP && stopRect.contains(event.x, event.y)) {
            onStop()
            return true
        }
        return true
    }
}
```

`OverlayEvents.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

object OverlayEvents {
    private val _stop = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val stop: SharedFlow<Unit> = _stop.asSharedFlow()

    fun requestStop() { _stop.tryEmit(Unit) }
}
```

- [ ] **Step 2: 前台服务骨架**

`AutomationForegroundService.kt`：

```kotlin
package com.zhuzhu.copilot.service.automation

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.zhuzhu.copilot.service.accessibility.OverlayEvents

class AutomationForegroundService : Service() {
    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            OverlayEvents.requestStop()
            stopSelf()
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "自动化执行", NotificationManager.IMPORTANCE_LOW)
        )
        val stop = PendingIntent.getService(
            this, 1,
            Intent(this, AutomationForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("zhuzhu 正在执行任务")
            .setContentText("点按通知或 HUD 上的中止可停止")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .addAction(0, "中止", stop)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "automation"
        private const val NOTIFICATION_ID = 1001
        const val ACTION_STOP = "com.zhuzhu.copilot.STOP"
    }
}
```

`service/automation/src/main/AndroidManifest.xml`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <application>
        <service
            android:name=".AutomationForegroundService"
            android:exported="false"
            android:foregroundServiceType="specialUse">
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="用户主动发起的手机自动化任务执行" />
        </service>
    </application>
</manifest>
```

- [ ] **Step 3: 构建验证**

```powershell
.\gradlew.bat :service:automation:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 4: 真机验证 overlay（用户操作）**

在 `SettingsScreen`（计划 C）完成前，先加一个临时调试入口：在 `MainActivity` 顶栏放一个“测试 HUD”图标，点击后 `startForegroundService(Intent(this, AutomationForegroundService::class.java))`，同时调用 `OverlayController.show()`。安装 debug 包并开启无障碍后点击该入口，确认：

- 顶部出现 HUD，玻璃光带与颗粒可见
- 「中止」可点击并停止
- 高亮层不拦截下方 App 的点击（在高亮区域内点其他按钮仍生效）

`AutomationForegroundService` 未导出，因此不能用 `adb am start-foreground-service` 启动，必须走应用内入口。

Expected：HUD 正常显示且不阻挡触摸。

- [ ] **Step 5: 提交**

```powershell
git add mobile/service
git commit -m "feat(service): 玻璃 HUD、目标高亮与前台服务"
```

---

### Task 12B: 用户抢占检测与异常恢复

**Files:**
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/InterventionBus.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/SystemStateChecker.kt`
- Modify: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/AgentAccessibilityService.kt`
- Modify: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/NodeActions.kt`
- Modify: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/AutomationForegroundService.kt`
- Modify: `mobile/core/agent/src/main/kotlin/com/zhuzhu/copilot/agent/AgentEngine.kt`
- Test: `mobile/core/agent/src/test/kotlin/com/zhuzhu/copilot/agent/ConsecutiveFailureTest.kt`

- [ ] **Step 1: 写连续失败终止测试**

```kotlin
package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.llm.LlmDelta
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.model.ToolSpec
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.asFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsecutiveFailureTest {
    private class FailingTool : AgentTool {
        override val spec = ToolSpec("tap_node", "点击", """{"type":"object"}""", RiskLevel.SAFE)
        override suspend fun execute(argumentsJson: String, context: ToolContext) =
            ToolResult(false, "点击失败，界面未变化")
    }

    private class ScriptedLlm : LlmCaller {
        override suspend fun stream(messages: List<ChatTurn>, tools: List<ToolSpec>): Flow<LlmDelta> =
            listOf(
                LlmDelta.ToolCallDelta(0, "call_1", "tap_node", "{}"),
                LlmDelta.Finished,
            ).asFlow()
    }

    @Test
    fun `three consecutive failures abort the task`() = runTest {
        val engine = AgentEngine(
            llm = ScriptedLlm(),
            registry = ToolRegistry(setOf(FailingTool())),
            riskPolicy = RiskPolicy(ConfirmMode.AUTO),
            gateway = { ConfirmationDecision.ALLOW_ONCE },
            config = AgentConfig(maxSteps = 10, maxToolRetries = 0),
        )
        val answer = engine.run("点它", ToolContext("t1")) {}
        assertTrue(answer.contains("连续失败") || answer.contains("失败"))
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :core:agent:test
```

Expected：测试能编译但断言失败（当前实现会一直循环到步数上限）。

- [ ] **Step 3: 实现抢占与锁屏检测**

`InterventionBus.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

object InterventionBus {
    private val _userTouch = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val userTouch: SharedFlow<Unit> = _userTouch.asSharedFlow()

    @Volatile
    var actionInProgress: Boolean = false

    fun onUserTouch() {
        if (!actionInProgress) _userTouch.tryEmit(Unit)
    }
}
```

`SystemStateChecker.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility

import android.app.KeyguardManager
import android.content.Context

object SystemStateChecker {
    fun isLocked(context: Context): Boolean =
        (context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
}
```

`AgentAccessibilityService.onAccessibilityEvent` 增加：

```kotlin
if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED ||
    event.eventType == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START
) {
    InterventionBus.onUserTouch()
}
```

`NodeActions` 的所有动作统一返回 Boolean，用守卫包裹：

```kotlin
private inline fun guarded(block: () -> Boolean): Boolean {
    if (SystemStateChecker.isLocked(service)) return false
    InterventionBus.actionInProgress = true
    return try {
        block()
    } finally {
        InterventionBus.actionInProgress = false
    }
}
```

所有 `click/longClick/setText/scroll/global/tap/longPress/swipe` 内部改为 `guarded { ... }`；返回 false 时由工具层转成“设备已锁屏或动作未执行”的失败结果。

- [ ] **Step 4: 前台服务监听抢占**

`AutomationForegroundService` 构造时用 `CoroutineScope(SupervisorJob() + Dispatchers.Main)` 收集：

```kotlin
scope.launch {
    InterventionBus.userTouch.collect {
        OverlayEvents.requestStop()
        overlay.updateStatus("检测到人工操作，已暂停", "任务已挂起")
    }
}
```

`OverlayEvents.requestStop()` 由前台服务转发给 Agent 引擎的取消入口。

- [ ] **Step 5: AgentEngine 连续失败终止**

在循环内维护：

```kotlin
var consecutiveFailures = 0
...
val result = executeWithRetry(...)
if (result.ok) consecutiveFailures = 0 else consecutiveFailures++
onEvent(AgentEvent.ToolResult(call, result.ok, result.summary))
if (consecutiveFailures >= 3) {
    onEvent(AgentEvent.Error("连续失败 3 次，已停止任务"))
    return@withTimeoutOrNull "连续失败 3 次，已停止任务"
}
```

- [ ] **Step 6: 测试通过**

```powershell
.\gradlew.bat :core:agent:test
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "feat(accessibility): 用户抢占检测、锁屏守卫与连续失败终止"
```

---

### Task 13: 节点与系统动作工具（19 个）

**Files:**
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/tools/NodeTools.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/tools/SystemTools.kt`
- Create: `mobile/service/accessibility/src/main/kotlin/com/zhuzhu/copilot/service/accessibility/tools/ToolModule.kt`
- Test: `mobile/service/accessibility/src/test/kotlin/com/zhuzhu/copilot/service/accessibility/tools/SnapshotFormatTest.kt`

所有工具实现 `AgentTool`，`requiredPermission = "accessibility"`。

- [ ] **Step 1: 写快照格式化失败测试**

```kotlin
package com.zhuzhu.copilot.service.accessibility.tools

import com.zhuzhu.copilot.service.accessibility.NodeRow
import com.zhuzhu.copilot.service.accessibility.UiNode
import org.junit.Assert.assertEquals
import org.junit.Test

class SnapshotFormatTest {
    @Test
    fun `rows render one per line with pipe separator`() {
        val rows = listOf(
            NodeRow(0, UiNode("发送", "", "Button", "btn_send", 10, 10, 110, 50, true, false, false, false, true, true, "com.demo")),
        )
        val formatted = NodeSnapshotFormatter.format(rows)
        assertEquals("0|\"发送\"||Button|btn_send|10,10,110,50|clickable|com.demo", formatted)
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
```

Expected：`NodeSnapshotFormatter` 未定义。

- [ ] **Step 3: 实现格式化与节点工具**

`NodeSnapshotFormatter`：

```kotlin
package com.zhuzhu.copilot.service.accessibility.tools

import com.zhuzhu.copilot.service.accessibility.NodeRow

object NodeSnapshotFormatter {
    fun format(rows: List<NodeRow>): String = rows.joinToString("\n") { row ->
        val n = row.node
        val flags = buildList {
            if (n.clickable) add("clickable")
            if (n.editable) add("editable")
            if (n.scrollable) add("scrollable")
            if (n.checked) add("checked")
        }.joinToString(",")
        "${row.idx}|\"${n.text}\"|\"${n.desc}\"|${n.className}|${n.viewId}|${n.left},${n.top},${n.right},${n.bottom}|$flags|${n.packageName}"
    }
}
```

`NodeTools.kt`（每个工具完整实现）：

```kotlin
package com.zhuzhu.copilot.service.accessibility.tools

import com.zhuzhu.copilot.agent.AgentTool
import com.zhuzhu.copilot.agent.ToolContext
import com.zhuzhu.copilot.agent.ToolResult
import com.zhuzhu.copilot.model.RiskLevel
import com.zhuzhu.copilot.model.ToolSpec
import com.zhuzhu.copilot.service.accessibility.AccessibilityBridge
import com.zhuzhu.copilot.service.accessibility.NodeFinder
import com.zhuzhu.copilot.service.accessibility.NodeQuery
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull

private val json = Json { ignoreUnknownKeys = true }

private fun service() = AccessibilityBridge.service
    ?: error("无障碍服务未开启，请先在系统设置中启用")

class SnapshotScreenTool : AgentTool {
    override val spec = ToolSpec(
        name = "snapshot_screen",
        description = "读取当前屏幕的节点树，返回每行一个节点，格式：idx|\"文本\"|\"描述\"|类名|资源id|左,上,右,下|标志|包名",
        parametersJson = """{"type":"object","properties":{}}""",
        risk = RiskLevel.SAFE,
        readOnly = true,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val svc = service()
        val rows = svc.snapshot()
        context.onProgress("命中 ${rows.size} 个节点")
        return ToolResult(true, "当前应用 ${svc.currentRoot()?.packageName ?: "未知"}", NodeSnapshotFormatter.format(rows))
    }
}

class FindNodeTool : AgentTool {
    override val spec = ToolSpec(
        name = "find_node",
        description = "按文本/描述/类名/资源id查找节点，返回候选 idx 与得分",
        parametersJson = """{"type":"object","properties":{"text":{"type":"string"},"desc":{"type":"string"},"className":{"type":"string"},"viewId":{"type":"string"},"clickableOnly":{"type":"boolean"}}}""",
        risk = RiskLevel.SAFE,
        readOnly = true,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val obj = json.parseToJsonElement(argumentsJson) as JsonObject
        val rows = service().snapshot()
        val query = NodeQuery(
            text = obj["text"]?.jsonPrimitive?.content,
            desc = obj["desc"]?.jsonPrimitive?.content,
            className = obj["className"]?.jsonPrimitive?.content,
            viewId = obj["viewId"]?.jsonPrimitive?.content,
            clickableOnly = obj["clickableOnly"]?.jsonPrimitive?.booleanOrNull ?: false,
        )
        val found = NodeFinder().find(rows, query)
        if (found.isEmpty()) return ToolResult(false, "未找到匹配节点")
        val text = found.joinToString("\n") { "${it.row.idx} | score=${it.score} | ${it.row.node.text} | ${it.row.node.className} | ${it.row.node.left},${it.row.node.top},${it.row.node.right},${it.row.node.bottom}" }
        return ToolResult(true, "找到 ${found.size} 个候选", text)
    }
}

class TapNodeTool : AgentTool {
    override val spec = ToolSpec(
        name = "tap_node",
        description = "点击指定 idx 的节点，自动上溯到可点击祖先",
        parametersJson = """{"type":"object","properties":{"idx":{"type":"integer"}},"required":["idx"]}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val idx = (json.parseToJsonElement(argumentsJson) as JsonObject)["idx"]!!.jsonPrimitive.int
        val ok = com.zhuzhu.copilot.service.accessibility.NodeActions(service()).click(idx)
        return if (ok) ToolResult(true, "已点击节点 $idx") else ToolResult(false, "点击失败，节点不可点击")
    }
}

class LongClickNodeTool : AgentTool {
    override val spec = ToolSpec(
        name = "long_click_node",
        description = "长按指定 idx 的节点",
        parametersJson = """{"type":"object","properties":{"idx":{"type":"integer"}},"required":["idx"]}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val idx = (json.parseToJsonElement(argumentsJson) as JsonObject)["idx"]!!.jsonPrimitive.int
        val ok = com.zhuzhu.copilot.service.accessibility.NodeActions(service()).longClick(idx)
        return ToolResult(ok, if (ok) "已长按节点 $idx" else "长按失败")
    }
}

class SetTextTool : AgentTool {
    override val spec = ToolSpec(
        name = "set_text",
        description = "向输入框写入文本，可选回车提交",
        parametersJson = """{"type":"object","properties":{"idx":{"type":"integer"},"text":{"type":"string"},"submit":{"type":"boolean"}},"required":["idx","text"]}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val obj = json.parseToJsonElement(argumentsJson) as JsonObject
        val idx = obj["idx"]!!.jsonPrimitive.int
        val text = obj["text"]!!.jsonPrimitive.content
        val submit = obj["submit"]?.jsonPrimitive?.booleanOrNull ?: false
        val ok = com.zhuzhu.copilot.service.accessibility.NodeActions(service()).setText(idx, text, submit)
        return ToolResult(ok, if (ok) "已写入文本" else "写入失败，可能不是输入框")
    }
}

class ClearTextTool : AgentTool {
    override val spec = ToolSpec(
        name = "clear_text",
        description = "清空指定输入框",
        parametersJson = """{"type":"object","properties":{"idx":{"type":"integer"}},"required":["idx"]}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val idx = (json.parseToJsonElement(argumentsJson) as JsonObject)["idx"]!!.jsonPrimitive.int
        val ok = com.zhuzhu.copilot.service.accessibility.NodeActions(service()).setText(idx, "", false)
        return ToolResult(ok, if (ok) "已清空" else "清空失败")
    }
}

class ScrollTool : AgentTool {
    override val spec = ToolSpec(
        name = "scroll",
        description = "滚动屏幕，direction 为 forward 或 backward，可指定节点 idx",
        parametersJson = """{"type":"object","properties":{"direction":{"type":"string"},"idx":{"type":"integer"}},"required":["direction"]}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val obj = json.parseToJsonElement(argumentsJson) as JsonObject
        val forward = obj["direction"]?.jsonPrimitive?.content != "backward"
        val idx = obj["idx"]?.jsonPrimitive?.intOrNull
        val ok = com.zhuzhu.copilot.service.accessibility.NodeActions(service()).scroll(idx, forward)
        return ToolResult(ok, if (ok) "已滚动" else "该区域不支持滚动")
    }
}
```

`SystemTools.kt` 实现 `list_apps`、`open_app`、`get_current_app`、`system_action`、`set_quick_setting`、`set_volume`、`wait`、`tap_point`、`swipe`、`screenshot`、`system_info`、`open_settings_page`。按同样模板逐一实现，关键代码：

```kotlin
class ListAppsTool : AgentTool {
    override val spec = ToolSpec(
        name = "list_apps",
        description = "列出可启动应用，返回 应用名|包名",
        parametersJson = """{"type":"object","properties":{}}""",
        risk = RiskLevel.SAFE,
        readOnly = true,
    )
    override val requiredPermission = "accessibility"
    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val pm = AppContextHolder.context.packageManager
        val intent = android.content.Intent(android.content.Intent.ACTION_MAIN).addCategory(android.content.Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(intent, 0).mapNotNull { info ->
            val pkg = info.activityInfo.packageName
            val label = info.loadLabel(pm).toString()
            "$label|$pkg"
        }.distinct().sorted()
        return ToolResult(true, "共 ${apps.size} 个应用", apps.joinToString("\n"))
    }
}
```

`AppContextHolder` 在 `service:accessibility` 中由 Application 注入（`ZhuzhuApp.onCreate` 里赋值），避免在工具里拿不到 Context。

```kotlin
object AppContextHolder {
    lateinit var context: android.content.Context
}
```

`open_app` 用包名精确匹配或应用名包含匹配，找不到返回失败；`system_action` 走 `NodeActions.global`；`set_quick_setting` 用 `Settings.System`/`BluetoothAdapter`（wifi 用 `WifiManager.setWifiEnabled` 在 API 29+ 返回 false，需提示用户手动开），`set_volume` 用 `AudioManager`；`wait` 支持 `ms` 与 `until_fingerprint` 两种；`swipe` 接受归一化坐标四组；`screenshot` 返回尺寸与 base64 图片（限制 720p 缩放，减少 token）；`system_info` 返回型号、API、分辨率、内存、电量、网络类型；`open_settings_page` 通过 `Intent(Settings.ACTION_*)` 打开指定系统页（wifi、蓝牙、无障碍、通知、应用详情）。

**动作后校验统一入口**（放在 `NodeTools.kt` 私有扩展，所有动作类工具共用）：

```kotlin
suspend fun actionWithVerify(
    service: AgentAccessibilityService,
    action: () -> Boolean,
): ToolResult {
    val before = WindowFingerprint.of(AccessibilityBridge.foregroundPackage.value, service.snapshot())
    if (!action()) return ToolResult(false, "动作未执行（节点不可点或设备已锁屏）")
    val changed = ActionVerifier(service).verify(before, waitMs = 1000)
    return if (changed) ToolResult(true, "已执行，界面已变化")
    else ToolResult(false, "动作后界面未变化，可能未生效")
}
```

`TapNodeTool.execute` 改为：

```kotlin
override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
    val idx = (json.parseToJsonElement(argumentsJson) as JsonObject)["idx"]!!.jsonPrimitive.int
    val svc = service()
    return actionWithVerify(svc) { com.zhuzhu.copilot.service.accessibility.NodeActions(svc).click(idx) }
}
```

`TapPointTool` 支持两条路径：

- 传了 `nx/ny`：直接用 `CoordinateMapper` 换算成物理像素后 `tap`
- 只传 `target`：抓当前屏 → `VisionLocator.locate(bitmap, target)` → 归一化坐标 → `tap`；视觉模型取当前激活服务商，若不支持图片输入则返回明确错误，不盲点

```kotlin
class TapPointTool(
    private val client: com.zhuzhu.copilot.llm.LlmClient,
    private val provider: com.zhuzhu.copilot.data.repo.ProviderRepository,
) : AgentTool {
    override val spec = ToolSpec(
        name = "tap_point",
        description = "按归一化坐标点击，或在只给 target 时用截图视觉定位后点击",
        parametersJson = """{"type":"object","properties":{"nx":{"type":"number"},"ny":{"type":"number"},"seen_w":{"type":"integer"},"seen_h":{"type":"integer"},"target":{"type":"string"}}}""",
        risk = RiskLevel.SENSITIVE,
    )
    override val requiredPermission = "accessibility"

    override suspend fun execute(argumentsJson: String, context: ToolContext): ToolResult {
        val obj = json.parseToJsonElement(argumentsJson) as JsonObject
        val svc = service()
        val metrics = svc.resources.displayMetrics
        val point = if (obj["nx"] != null && obj["ny"] != null) {
            CoordinateMapper.normalizedToPixels(
                nx = obj["nx"]!!.jsonPrimitive.float,
                ny = obj["ny"]!!.jsonPrimitive.float,
                seenWidth = obj["seen_w"]?.jsonPrimitive?.intOrNull ?: metrics.widthPixels,
                seenHeight = obj["seen_h"]?.jsonPrimitive?.intOrNull ?: metrics.heightPixels,
                screenWidth = metrics.widthPixels,
                screenHeight = metrics.heightPixels,
            )
        } else {
            val target = obj["target"]?.jsonPrimitive?.content ?: return ToolResult(false, "缺少 target 或坐标")
            val active = provider.active() ?: return ToolResult(false, "未配置模型，无法视觉定位")
            val capture = AccessibilityScreenshotSource(svc).capture()
            val bitmap = capture.bitmap ?: return ToolResult(false, capture.error)
            val normalized = VisionLocator(
                client, active.baseUrl, provider.apiKeyOf(active.id).orEmpty(), active.model
            ).locate(bitmap, target) ?: return ToolResult(false, "视觉模型未能定位「$target」")
            CoordinateMapper.normalizedToPixels(
                normalized.first, normalized.second, bitmap.width, bitmap.height,
                metrics.widthPixels, metrics.heightPixels,
            )
        }
        val changed = actionWithVerify(svc) {
            com.zhuzhu.copilot.service.accessibility.NodeActions(svc).tap(point.first, point.second)
        }
        return changed
    }
}
```

- [ ] **Step 4: 注入注册表**

`ToolModule.kt`：

```kotlin
package com.zhuzhu.copilot.service.accessibility.tools

import com.zhuzhu.copilot.agent.AgentTool
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dagger.multibindings.IntoSet

@Module
@InstallIn(SingletonComponent::class)
object ToolModule {
    @Provides @IntoSet fun snapshotScreen(): AgentTool = SnapshotScreenTool()
    @Provides @IntoSet fun findNode(): AgentTool = FindNodeTool()
    @Provides @IntoSet fun tapNode(): AgentTool = TapNodeTool()
    @Provides @IntoSet fun longClickNode(): AgentTool = LongClickNodeTool()
    @Provides @IntoSet fun setText(): AgentTool = SetTextTool()
    @Provides @IntoSet fun clearText(): AgentTool = ClearTextTool()
    @Provides @IntoSet fun scroll(): AgentTool = ScrollTool()
    @Provides @IntoSet fun listApps(): AgentTool = ListAppsTool()
    @Provides @IntoSet fun openApp(): AgentTool = OpenAppTool()
    @Provides @IntoSet fun currentApp(): AgentTool = GetCurrentAppTool()
    @Provides @IntoSet fun systemAction(): AgentTool = SystemActionTool()
    @Provides @IntoSet fun quickSetting(): AgentTool = SetQuickSettingTool()
    @Provides @IntoSet fun volume(): AgentTool = SetVolumeTool()
    @Provides @IntoSet fun wait(): AgentTool = WaitTool()
    @Provides @IntoSet fun tapPoint(
        client: com.zhuzhu.copilot.llm.LlmClient,
        provider: com.zhuzhu.copilot.data.repo.ProviderRepository,
    ): AgentTool = TapPointTool(client, provider)
    @Provides @IntoSet fun swipe(): AgentTool = SwipeTool()
    @Provides @IntoSet fun screenshot(): AgentTool = ScreenshotTool()
    @Provides @IntoSet fun systemInfo(): AgentTool = SystemInfoTool()
    @Provides @IntoSet fun openSettingsPage(): AgentTool = OpenSettingsPageTool()
}
```

在 `app/AppModule.kt` 中把 `ToolRegistry(emptySet())` 改为 `ToolRegistry(setOfTools)`，并由 `@Provides fun tools(set: Set<AgentTool>) = set` 汇聚：

```kotlin
@Provides
@Singleton
fun toolRegistry(tools: Set<AgentTool>): ToolRegistry = ToolRegistry(tools)
```

- [ ] **Step 5: 测试通过**

```powershell
.\gradlew.bat :service:accessibility:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 6: 真机验收（用户操作）**

开启无障碍后，在对话里输入“打开设置，把蓝牙关掉”。预期：HUD 出现、目标高亮跟随、蓝牙开关被关闭，最终给出结果。若失败，按 Logcat 中 `Accessibility` 标签定位。

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "feat(accessibility): 节点与系统动作工具接入 Agent 循环"
```

---

### Task 14: 设备数据工具（通知、短信、剪贴板、记忆）

**Files:**
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/AgentNotificationListener.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/tools/DeviceTools.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/tools/MemoryTools.kt`
- Create: `mobile/service/automation/src/main/kotlin/com/zhuzhu/copilot/service/automation/tools/AutomationToolModule.kt`
- Modify: `mobile/service/automation/src/main/AndroidManifest.xml`
- Modify: `mobile/app/src/main/AndroidManifest.xml`（权限）
- Test: `mobile/service/automation/src/test/kotlin/com/zhuzhu/copilot/service/automation/SmsCodeParserTest.kt`

- [ ] **Step 1: 写验证码解析失败测试**

```kotlin
package com.zhuzhu.copilot.service.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SmsCodeParserTest {
    @Test
    fun `extracts six digit code`() {
        assertEquals("123456", SmsCodeParser.extract("【某银行】验证码 123456，5分钟内有效"))
    }

    @Test
    fun `ignores unrelated numbers`() {
        assertNull(SmsCodeParser.extract("您的订单 20251101 已发货"))
    }
}
```

- [ ] **Step 2: 确认失败**

```powershell
.\gradlew.bat :service:automation:testDebugUnitTest
```

Expected：`SmsCodeParser` 未定义。

- [ ] **Step 3: 实现通知监听与设备工具**

`AgentNotificationListener.kt`：

```kotlin
package com.zhuzhu.copilot.service.automation

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class AgentNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        NotificationCache.record(
            packageName = sbn.packageName,
            title = sbn.notification.extras.getString("android.title").orEmpty(),
            text = sbn.notification.extras.getString("android.text").orEmpty(),
            postedAt = sbn.postTime,
        )
    }

    companion object {
        fun isEnabled(context: android.content.Context): Boolean {
            val flat = android.provider.Settings.Secure.getString(
                context.contentResolver, "enabled_notification_listeners"
            ).orEmpty()
            return flat.contains(context.packageName)
        }
    }
}
```

`NotificationCache` 保留最近 50 条内存记录，提供 `recent(limit)` 与 `clear()`；不落盘。

`SmsCodeParser.kt`：

```kotlin
package com.zhuzhu.copilot.service.automation

object SmsCodeParser {
    private val code = Regex("(?:验证码|校验码|动态码|code)[^0-9]{0,6}([0-9]{4,8})", RegexOption.IGNORE_CASE)
    private val fallback = Regex("\\b([0-9]{6})\\b")

    fun extract(text: String): String? {
        code.find(text)?.groupValues?.getOrNull(1)?.let { return it }
        return fallback.find(text)?.groupValues?.getOrNull(1)
    }
}
```

`DeviceTools.kt` 实现：

- `read_clipboard`：API 33+ 需应用在前台时通过 `ClipboardManager.getPrimaryClip()`；后台可能返回空，失败时返回明确错误而非空字符串
- `write_clipboard`：直接 `setPrimaryClip`
- `read_notifications`：读取 `NotificationCache.recent(limit)`
- `get_sms_code`：查询 `content://sms/inbox` 最近 10 条，按 `sender` 关键字过滤后调用 `SmsCodeParser.extract`
- `remember` / `recall`：走 `MemoryRepository`（Room）
- `ask_user`：转发给 UI 确认网关（在 Agent 循环中已经暂停，工具只需返回“已提问”）

`MemoryTools` 依赖 `core:data` 的 `MemoryRepository`；用 Hilt 注入 DAO。

Manifest 增加通知监听声明与权限：

```xml
<service
    android:name=".AgentNotificationListener"
    android:exported="false"
    android:label="zhuzhu 通知读取"
    android:permission="android.permission.BIND_NOTIFICATION_LISTENER_SERVICE">
    <intent-filter>
        <action android:name="android.service.notification.NotificationListenerService" />
    </intent-filter>
</service>
```

`app` 的 Manifest 增加：

```xml
<uses-permission android:name="android.permission.RECEIVE_SMS" />
<uses-permission android:name="android.permission.READ_SMS" />
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.QUERY_ALL_PACKAGES"
    tools:ignore="QueryAllPackagesPermission" />
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" tools:node="remove" />
```

- [ ] **Step 4: 注入工具与权限过滤**

`AutomationToolModule.kt` 用 `@IntoSet` 提供上述工具。`AgentEngine` 的 `grantedPermissions()` 改为读取真实状态：

```kotlin
fun grantedPermissions(context: android.content.Context): Set<String> = buildSet {
    add("accessibility")
    if (AgentNotificationListener.isEnabled(context)) add("notification")
    if (androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.READ_SMS) == android.content.pm.PackageManager.PERMISSION_GRANTED) add("sms")
    if (androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED) add("audio")
}
```

在 `AppModule` 中提供该 lambda 给 `AgentEngine`。未授权时对应工具不会出现在 schema 中，模型无法调用。

- [ ] **Step 5: 测试通过**

```powershell
.\gradlew.bat :service:automation:testDebugUnitTest
.\gradlew.bat :app:assembleDebug
```

Expected：`BUILD SUCCESSFUL`。

- [ ] **Step 6: 真机验收**

开启通知使用权，让测试短信到达一条含验证码的消息，执行“读一下最新验证码”。预期返回 6 位验证码；关闭通知使用权后再执行，工具应不出现在可调用列表。

- [ ] **Step 7: 提交**

```powershell
git add mobile
git commit -m "feat(automation): 通知、短信、剪贴板与记忆工具"
```

---

## 计划 B 完成标准

- 无障碍服务可开可关，关闭后所有自动化立即停止
- 三个真实任务通过：关蓝牙、发消息、读验证码
- 节点失败时截图 + 视觉坐标能兜底，且落点正确
- HUD 与高亮在真机上可中止、可穿透
- 全部单测与 `:app:assembleDebug` 通过

## 计划 B 明确不做

- 任务页与设置页完整 UI（计划 C）
- 语音输入、定时任务（计划 C）
- 签名与 release 打包（计划 C）
