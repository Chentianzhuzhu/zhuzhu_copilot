---
name: download-skill
description: 技能下载安装：在 GitHub 上搜索市场标准 SKILL.md 技能，下载并导入本地配置生效
---

# download-skill：GitHub 技能搜索、下载与导入

当用户要求"下载 / 安装 / 寻找某个 skill（技能）"时，按以下流程执行。

## 1. 确认需求
用 ask_user 与用户确认：技能名称/关键词、用途、是否有已知 GitHub 仓库地址。
信息不足时禁止猜测，先问清楚。

## 2. 在 GitHub 上定位技能
候选仓库判断标准：仓库根目录或 skills/ 子目录存在 SKILL.md（含 --- name / description --- frontmatter）。
- 用户提供仓库地址：先 run_command 执行 `git ls-remote <仓库地址>` 验证仓库可访问；
- 用户只给名称/关键词：依次探测常用市场仓库（git ls-remote 验证存在后浅克隆）：
  - https://github.com/anthropics/skills
  - https://github.com/anthropics/claude-code
  - 其他含 SKILL.md 的 skills 聚合仓库
  找不到时用 ask_user 请用户提供具体仓库地址，不要编造 URL。
- 用 `git clone --depth 1 <仓库> <临时目录>` 浅克隆后，用 list_directory / read_file
  在仓库内查找 SKILL.md，读取 frontmatter 确认技能 name 与 description 是否匹配用户需求。

## 3. 下载前确认（强制）
用 ask_user 向用户展示以下信息并征得同意：
- 仓库地址
- 技能名与 description
- SKILL.md 内容摘要（前若干行）
用户确认后才下载；用户拒绝则停止并说明。

## 4. 下载
- 整仓浅克隆：git clone --depth 1 <仓库> <临时目录>（临时目录建议 %TEMP% 下）
- 有 zip 或 raw 文件直链：用 fast_download 工具下载到本地
- 单文件：下载 SKILL.md 的 raw 内容

## 5. 导入本地并配置
- 目标目录：~/.winapp_migrator/agent/skills/<技能名>/SKILL.md
- 用 import_skill_file 导入（支持 SKILL.md 单文件或含 SKILL.md 的 zip），
  或把下载到的 SKILL.md 复制到目标目录
- 验证：read_file 确认 SKILL.md 已写入目标位置；技能系统实时扫描，导入后立即可用

## 6. 汇报
输出总结：技能名、来源仓库、安装路径、调用方式（/技能名 或自然语言描述）。
