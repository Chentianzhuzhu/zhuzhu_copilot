# zhuzhu Copilot 项目概览

## 基本信息

- **项目名称**：zhuzhu Copilot
- **项目类型**：Windows 桌面 AI Agent 应用
- **技术栈**：Python 3.13 + PyQt6 + QtWebEngine
- **入口文件**：`src/main.py`

---

## 目录结构

| 目录/文件 | 说明 |
|-----------|------|
| `src/main.py` | 程序入口，初始化 Qt 应用、预热面板 |
| `src/zhuzhu_Copilot/core/` | 核心逻辑（83个文件）：Agent引擎、工具、技能、安全、迁移等 |
| `src/zhuzhu_Copilot/ui/` | PyQt6 界面（主窗口、Agent面板、桌宠、歌词、语音面板等） |
| `src/zhuzhu_Copilot/office/` | 办公模块 |
| `src/zhuzhu_Copilot/plugins/` | 插件系统 |
| `src/zhuzhu_Copilot/utils/` | 工具函数 |
| `mcp_servers/local_mcp_server.py` | 本地 MCP 服务器 |
| `installer/` | Inno Setup 安装脚本 |
| `update-server/` | Spring Boot 更新服务器 |
| `scripts/` | 构建/打包/部署辅助脚本 |
```

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
# 一条命令完成：体检 → 运行时/种子 → 打包 → 签名 → Inno Setup → 产物自检
.\build_release.ps1

# 只体检不构建
.\build_release.ps1 -DryRun

# 兼容入口（薄壳，等价于上面的命令）
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
