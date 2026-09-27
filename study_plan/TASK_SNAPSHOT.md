# 📋 九年级全科复习任务快照

## 状态：等待重启后恢复
- **创建时间**: 2026-07-16
- **当前进度**: 配置完成，等待子 Agent 工具生效

---

## ✅ 已完成配置

### 1. 工作流
- 名称: `g9_study_helper`
- 状态: 已激活
- 路径: `C:/Users/zhuzhu/.winapp_migrator/workflows/g9_study_helper/`

### 2. 子 Agent 注册表
七位学科老师已全部注册（shared_context 默认 true）：

| 子 Agent | 科目 | 描述 |
|---|---|---|
| sub_teacher_math | 数学 | 一元二次方程、二次函数、圆、相似等 |
| sub_teacher_chinese | 语文 | 现代文阅读、文言文、古诗词、名著与写作 |
| sub_teacher_english | 英语 | 语法体系、词汇、听说读写与中考题型 |
| sub_teacher_physics | 物理 | 热学、电路、欧姆定律、电功率等 |
| sub_teacher_chemistry | 化学 | 化学用语、方程式、溶液、酸碱盐等 |
| sub_teacher_history | 历史 | 世界史脉络、史料分析、大事年表 |
| sub_teacher_politics | 道德与法治 | 国情国策、国际视野、辨析题方法 |

### 3. 共享上下文空间
- 空间 ID: `ctx_a1ad3c46`
- 状态: 已激活
- 所有子 Agent 默认加入

---

## 🔄 下一步任务（重启后执行）

### 第一阶段：信息收集
- [ ] 收集各科第一章内容范围，明确考试重点
  - 调用方式: `sub_teacher_math(goal="...")` 等
  - 产出: 各科第一章知识点清单

### 第二阶段：计划制定
- [ ] 制定全科复习计划（日/周清单）
  - 输出: 复习日程表（Markdown 或 Excel）

### 第三阶段：材料生成
- [ ] 派发各学科老师生成复习要点与练习题
  - 调用方式: 并行派发7个子 Agent
  - 产出: 各科复习要点文档

### 第四阶段：汇总保存
- [ ] 汇总资料并保存为文件
  - 存放路径: `study_plan/` 目录
  - 文件格式: Markdown + Excel 计划表

### 第五阶段：确认完成
- [ ] 确认任务全部完成

---

## 💡 使用提示

### 子 Agent 调用方式
```python
# 主 Agent 调用
sub_teacher_math(
    goal="生成九年级数学上册第一章复习要点",
    context="学生基础薄弱，需要详细讲解",
    shared_context=True  # 加入共享上下文
)

# 用户输入框调用
@teacher_math 生成第一章重点
```

### 共享上下文使用
- 开启: `shared_context(op="open", seed="议题...")`
- 写入: `shared_context(op="append", source="...", text="...")`
- 读取: `shared_context(op="read")`

---

## 📁 相关文件
- 任务快照: `study_plan/TASK_SNAPSHOT.md`
- 配置状态: `study_plan/status.md`
- 工作流: `C:/Users/zhuzhu/.winapp_migrator/workflows/g9_study_helper/`
- 记忆文件: `memory.md`

---

## ⏭️ 恢复步骤
1. 重启应用或对话
2. 加载记忆: 我会自动读取 `memory.md`
3. 读取快照: 打开 `study_plan/TASK_SNAPSHOT.md`
4. 验证工具: 调用 `list_sub_agents` 确认7位子 Agent 已注册
5. 继续执行下一步任务
