# WinAppMigrator（zhuzhu Copilot）项目概览

## 基本信息

- **项目名称**：WinAppMigrator（zhuzhu Copilot）
- **项目类型**：Windows 桌面 AI Agent 应用
- **技术栈**：Python 3.13 + PyQt6 + QtWebEngine
- **入口文件**：`src/main.py`

---

## 目录结构

| 目录/文件 | 说明 |
|-----------|------|
| `src/main.py` | 程序入口，初始化 Qt 应用、预热面板 |
| `src/winapp_migrator/core/` | 核心逻辑（83个文件）：Agent引擎、工具、技能、安全、迁移等 |
| `src/winapp_migrator/ui/` | PyQt6 界面（主窗口、Agent面板、桌宠、歌词、语音面板等） |
| `src/winapp_migrator/office/` | 办公模块 |
| `src/winapp_migrator/plugins/` | 插件系统 |
| `src/winapp_migrator/utils/` | 工具函数 |
| `mcp_servers/local_mcp_server.py` | 本地 MCP 服务器 |
| `installer/` | Inno Setup 安装脚本 |
| `update-server/` | Spring Boot 更新服务器 |
| `website/` | 官网静态页面 |
| `scripts/` | 构建/打包/部署辅助脚本 |
| `account-book-backend/` | 独立 Node.js 后端（Express + SQLite） |
| `go-game/` | Go 语言游戏子项目 |
| `app/` | Android 语音笔记子项目（Kotlin） |

---

## 核心依赖

### Python 依赖

```
PyQt6>=6.7
PyQt6-WebEngine>=6.7
Pillow>=10.0
pywin32>=306
python-docx>=1.1
openpyxl>=3.1
python-pptx>=0.6
Cython>=3.0  # 构建期（可选）
yara-python>=4.5  # 安全引擎（可选）
```

### Node.js 依赖（记账本后端）

```json
{
  "express": "^4.18.2",
  "sequelize": "^6.35.0",
  "sqlite3": "^5.1.6",
  "bcryptjs": "^2.4.3",
  "jsonwebtoken": "^9.0.2",
  "cors": "^2.8.5",
  "dotenv": "^16.3.1"
}
```

---

## 功能亮点

1. **AI Agent 引擎**：集成 LLM 对话、工具调用、技能系统
2. **浏览器自动化**：独立 WebView 实例，不干扰用户浏览器
3. **TTS 语音合成**：支持音频播放与合成功能
4. **安全沙箱**：YARA 规则引擎、执行守卫、权限控制
5. **多工作流支持**：热插拔工作流切换
6. **自更新机制**：Spring Boot 更新服务器
7. **桌面组件**：桌宠、歌词显示、下载对话框

---

## 构建方式

### 源码运行

```bash
pip install -r requirements.txt
python src/main.py
```

### 打包编译

```powershell
# Cython 编译 + 代码签名 + PyInstaller
.\build_sign.ps1

# 生成安装包（Inno Setup）
installer\build_setup.bat
```

> **注意**：代码签名证书 `build/certs/zhutianliang.pfx` 为本地私钥文件，已被 `.gitignore` 排除，严禁提交到仓库。

---

## 开发约定

- **提交信息格式**：`type(scope): 描述`（feat / fix / style / perf / refactor / chore）
- **临时诊断脚本**：加 `_` 前缀（已被 `.gitignore` 排除），不要提交
- **构建产物**：`build/`、`dist/`、`src/build/`、TTS 音频输出（`tts_output/`）不入库

---

*生成时间：2025年*
