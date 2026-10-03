# zhuzhu Copilot

一个基于 PyQt6 的 Windows 桌面 AI Agent 应用，集成 LLM 对话、浏览器自动化、TTS、屏幕操作、沙箱执行、27 个内置技能与 MCP 服务器支持，并配套自更新（Spring Boot 更新服务器）、Inno Setup 安装器与官网。

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| `src/main.py` | 程序入口 |
| `src/zhuzhu_Copilot/core/` | Agent 引擎、工具、技能、安全、迁移等核心逻辑 |
| `src/zhuzhu_Copilot/ui/` | PyQt6 界面（主窗口、Agent 面板、桌宠等） |
| `src/zhuzhu_Copilot/skills/` | 内置技能 |
| `mcp_servers/` | 本地 MCP 服务器 |
| `installer/` | Inno Setup 安装脚本 |
| `update-server/` | Spring Boot 更新服务器（详见其内 README） |
| `scripts/` | 构建 / 打包 / 部署辅助脚本 |

## 环境搭建

```bash
# Windows，Python 3.13
pip install -r requirements.txt
python src/main.py
```

## 构建与打包

```powershell
# 一条命令完成：体检 → 运行时/种子 → 打包 → 签名 → Inno Setup → 产物自检
.\build_release.ps1
# 只体检不构建
.\build_release.ps1 -DryRun
# 兼容入口（薄壳，等价于上面的命令）
installer\build_setup.bat
```

注意：代码签名证书 `build/certs/zhutianliang.pfx` 为本地私钥文件，已被 `.gitignore` 排除，**严禁提交到仓库**。

## 开发约定

- 提交信息格式：`type(scope): 描述`（feat / fix / style / perf / refactor / chore）
- 临时诊断脚本请加 `_` 前缀（已被 `.gitignore` 排除），不要提交
- 构建产物（`build/`、`dist/`、`src/build/`）、TTS 音频输出（`tts_output/`）不入库
