---
name: browser-control
description: 浏览器元素级自动化（CDP 独立实例）：截图、按元素编号/文字点击输入、执行JS解析HTML/CSS、读取页面内容，完全不影响用户其他操作。给用户打开网页/网站/视频一律用用户默认浏览器（preview_open），不用本技能
---

# browser-control：AI 元素级浏览器自动化（独立实例，不影响用户）

**浏览器操作策略（先读）**：一切「给用户打开网页/网站/在线视频」的浏览器任务一律用**用户默认
浏览器**——调 preview_open(url=网址)（经系统默认浏览器打开），抓取网页内容用 web_fetch；
不要为「打开网页」启动独立实例。仅当任务需要页面内元素级自动化（在网页上登录/填写表单/点击/
滚动/抓取动态渲染数据）时，才用本技能工具（browser_open/browser_navigate/browser_snapshot/
browser_click/browser_type/browser_scroll/browser_eval/browser_html/browser_close）——
它们操控的是独立浏览器实例，不用鼠标键盘去点用户正在用的浏览器。

## 1. 启动（第一步）
- `browser_open`：启动**独立浏览器实例**（独立持久用户目录+调试端口，与用户正在用的浏览器完全隔离，
  不碰鼠标键盘、不影响用户其他操作）。留空 engine 自动找 Edge/Chrome。
- **登录态持久保留**：登录的 cookie/token 保存在持久目录，`browser_close` 后仍保留，
  下次 `browser_open` 自动恢复、无需用户重复登录。

## 2. 打开网页
- `browser_navigate(url=网址)` 打开目标网页（自动补全 http/https）。

## 3. 观察（每步操作前后）
- `browser_snapshot`：返回页面截图 + **可交互元素编号清单 [id] (标签) 文字**。
- 分析网页内容用 `browser_html(selector=可选CSS选择器)` 读取 HTML/文本摘要。
- 需要自定义分析/抓取时用 `browser_eval(js=JS代码)` 直接执行 JavaScript（读取/修改 DOM、调用页面函数）。

## 4. 操作（精确点击/输入/滑动）
- **点击**：`browser_click(id=编号)` 或 `browser_click(text=按钮/链接文字)`，或 `browser_click(selector=CSS选择器)`
  精确定位（如 #submit / .btn-primary / form button）。系统解析 DOM 坐标派发点击，兼容 React/Vue 框架事件。
- **输入**：`browser_type(text=内容, id=输入框编号)`、`browser_type(text=内容, target=占位符/标签)` 或
  `browser_type(text=内容, selector=CSS选择器)`。
- **滑动**：页面内容超出屏幕（列表/长文/评论区/视频流）需查看更多时，用
  `browser_scroll(direction=down/up/top/bottom, amount=像素步长, id/selector=滚动容器)` 滚动后再 snapshot。
- 操作后通常自动返回最新截图确认结果；不确认时可再 `browser_snapshot` 或 `browser_html` 验证。

## 5. 弹窗/iframe/影子DOM/新窗口（关闭按钮点不到时的处理）
- 元素清单由系统**穿透解析**：跨域 iframe（广告/客服弹窗）与 shadow DOM 里的可交互元素（含关闭按钮）
  也已在 browser_snapshot 清单中（编号同样可用），直接按 [id] 或文字点击即可，无需估算坐标。
- 关闭弹窗：优先 `browser_click(text=关闭)` 或按清单找到"关闭/×"图标按钮的编号点击；
  点不到/没反应时先 `browser_snapshot` 看当前状态再操作，不要无脑重复点击。
- 若当前页面里的关闭按钮始终找不到，可能是**新窗口/新标签弹窗**：
  1. `browser_tabs` 列出所有页面标签（含弹窗）；
  2. `browser_switch_tab(id=弹窗编号)` 切到弹窗标签；
  3. 再 `browser_snapshot` 找到关闭按钮后 `browser_click`。
- 确认弹窗是否真的关闭：关闭后再 `browser_snapshot`，弹窗消失即成功。

## 6. 登录页必须等待用户登录完成（强制）
- 打开需要登录的页面（检测到"登录/注册/输入密码/验证码"等登录表单，或访问受限需登录跳转）时：
  1. 先用 `browser_snapshot` / `browser_html` 确认当前确实是登录页。
  2. **不要盲目操作登录表单**：不猜测账号密码、不重复点击登录按钮。
  3. **通知用户登录**：用 ask_user 告知用户"请在已打开的浏览器窗口中完成登录（账号/密码/验证码）"，
     并说明登录完成后会继续任务。
  4. **等待登录完成**：登录期间停止一切网页操作，循环用 `browser_snapshot` / `browser_html`
     检查是否已登录（登录页消失、出现用户头像/主页内容/跳转回目标页、URL 变化等）。
  5. 确认已登录后才继续后续操作；登录超时（用户仍未登录）时再次提示用户，不要跳过登录直接操作。

## 7. 收尾
- 任务完成后 `browser_close` 关闭独立浏览器实例（不影响用户正在用的浏览器）。
- **登录态/token 保留**：关闭后 cookie/token 仍在持久目录，下次打开浏览器自动恢复，无需重复登录。

## 8. 原则
- 每步先想清楚目标再操作；操作结果不确定时 snapshot/html 验证，失败先自查再换方案。
- 涉及账号密码/验证码/手机验证等敏感登录信息时，一律交给用户手动完成，AI 不代为填写、不猜测。
