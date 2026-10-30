---
name: skill-mgmt
description: 技能创建：create_skill 根据用户自然语言描述自动生成市场标准 SKILL.md 技能并立即加载生效
---

# skill-mgmt：创建新技能

当用户要求"创建一个技能 / 把某个能力做成 skill"时使用本技能。

## 1. 确认需求
用 ask_user 问清：技能要做什么（触发场景）、执行流程、注意事项。
信息不足禁止猜测。

## 2. 创建
create_skill(name=技能名, description=用途简介, instruction=执行流程正文)：
- name：仅字母/数字/下划线/连字符，≤50 字符（如 doc-gen）
- instruction：markdown 正文，写清触发条件、分步流程、注意事项
- 生成到 skills/<name>/SKILL.md，创建后立即生效

## 3. 验证与汇报
创建后说明：技能名、调用方式（/技能名 或自然语言描述）、是否已生效。
市场已有同名技能时，建议先询问用户是否仍要创建（避免覆盖）。
