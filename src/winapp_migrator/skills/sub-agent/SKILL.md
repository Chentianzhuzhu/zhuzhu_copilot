---
name: sub-agent
description: 子任务调度：dispatch_sub_agents 并行派发互不依赖的子任务、explore_project 快速了解新项目、search_large 大规模跨目录搜索
---

# sub-agent：子任务并行调度与大规模搜索

当任务包含多个互不依赖的子任务、需要快速了解项目、或搜索范围很大时使用本技能。

## 1. 并行派发
dispatch_sub_agents(tasks=[{title: 标题, goal: 目标与要求, context: 上下文}]):
- 适合：大规模读取/搜索/探索、多文件并行处理
- 子任务数量不限（一次派发多少就跑多少），每个 goal 写清"要做什么、输出什么"
- ★每个子任务必须带 context：把主 Agent 已读取的文件内容、关键代码片段、
  搜索结果、约束要求等完成任务所必需的信息随任务传给子 Agent——子 Agent 是
  独立上下文，看不到主对话与工具结果；缺 context 会盲猜或重复读取，降低质量。
- 派发前对尚未读取而子任务需要的内容，先用 read_file 等工具读取后再打包进 context
- 子 Agent 可读写文件但不能执行命令；执行类工作留在主 Agent

## 2. 了解新项目
explore_project(directory=项目目录, context=可选上下文, shared_context=可选, space=可选)：
生成目录结构、读 README 与关键入口，输出项目概览（用途/技术栈/模块结构/入口/构建方式）。
接手新项目先用它。

## 3. 大规模搜索
search_large(query=关键词, directories=目录列表可选, max_results=条数,
context=可选上下文, shared_context=可选, space=可选)：
跨目录多轮搜索并汇总命中，适合范围大、文件多的场景；
小范围/单文件查找用 search_files 更轻量。

## 4. 注册式子 Agent（sub_<name>）与「共享上下文 / 传参上下文」分配
用户要求「创建一个子 agent / 新增一个能干活的下属 agent」时，一律用 register_sub_agent
注册进当前工作流（不要用 create_agent——那只是切换主 Agent 人格，主 Agent 调不到它）。
注册式子 Agent 的三种用法：① 用户 @<名> 直接调用；② 主 Agent 调用 sub_<name>；③ 参与共享上下文。
主 Agent 对每个子 Agent（注册式与临时子任务均可）逐次分配：
- shared_context=true/false：本次是否加入共同上下文空间（先 shared_context 工具 op=open 开空间；
  注册默认值可被逐次覆盖）；
- context=<上下文>：把已读到的文件内容 / 已得结论交给该子 Agent，免其重复读取搜索；
- space=<空间 id>：指定目标空间（缺省用活跃空间）。

注册时可选权限（团队协作场景按需开启，缺省关闭）：
- allow_chat=true/false：是否允许该子 Agent 参与 Agent 间聊天（chat_with 可发给它并接收）；
- share_context=true/false：是否允许领导者（如产品经理）用 look_context 查看其上下文轨迹
  （消息/命令/文件/工具/skill/mcp/plugin）。

## 5. 工作团（Agent Team）与监督
默认团队：领导者 product_manager（产品经理）+ 成员 zhuzhu_copilot（默认工作流）、
frontend_design、product_dev、backend_dev、product_debug。用户 @product_manager 即激活团队模式。
产品经理/领导者可：
- look_context(agent=<成员名>)：查看成员上下文轨迹（开启 share_context 的子 Agent 可见）；
- chat_with(to=<成员名>, text=...)：与成员讨论（开启 allow_chat 的子 Agent 可接收）；
- pause_agent(agent=<成员名>) / resume_agent(agent=<成员名>)：暂停/恢复成员执行；
- warn_agent(agent=<成员名>, text=...)：向成员发警告提醒（下一检查点注入其上下文）。
跨工作流派发：dispatch_sub_agents(tasks=[{agent: <成员工作流名>, goal, context, shared_context}])
把任务交给成员工作流的主 Agent 接管，原主 Agent 监督；@工作流 切换继承原工作流的共享空间。

## 6. 汇报
汇总各子任务结果、项目概览或搜索命中清单。
