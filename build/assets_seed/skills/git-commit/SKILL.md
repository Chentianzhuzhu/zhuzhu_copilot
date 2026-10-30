---
name: git-commit
description: 按 Conventional Commits 标准提交仓库、推送、拉取；规范提交信息格式、检查状态、处理拉取冲突
---

# Git 提交规范（Conventional Commits）

## 一、提交信息格式

单行提交信息格式：

```
<type>(<scope>): <description>
```

- **type（必填）**：`feat` 新功能 / `fix` 修复 / `docs` 文档 / `style` 格式 / `refactor` 重构 / `perf` 性能 / `test` 测试 / `build` 构建 / `ci` 持续集成 / `chore` 杂务 / `revert` 回滚
- **scope（可选）**：影响范围，写在 type 后的圆括号里，如 `feat(agent):`、`fix(ui):`
- **description**：简洁描述"做了什么"，≤50 字符，不加句号；中英文均可
- **复杂提交**：正文用空行与标题分隔，说明原因（why）与要点，用 `- ` 列出；破坏性变更在 footer 写 `BREAKING CHANGE: <说明>`

示例：

```
feat(agent): 子 Agent 支持编辑/创建/删除文件
fix(dnd): 管理员窗口拖放对全部子窗口放行
docs(readme): 补充安装说明
```

## 二、提交流程（正确提交仓库）

1. 先 `git status` 查看改动，`git diff --stat` 确认影响范围；不要盲目 `git add .`
2. 按改动内容挑选 type 与 scope，只暂存本次相关的文件：

   ```
   git add <文件1> <文件2>
   ```

3. 用标准格式生成提交信息（提交信息必须遵循上面的规范，AI 必须根据实际改动推断 type/scope）：

   ```
   git commit -m "<type>(<scope>): <description>"
   ```

4. 提交前用 ask_user 向用户确认提交信息与包含的变更清单，确认后再执行 commit
5. 提交前如发现忘记暂存或有误，先 `git status` 复核；提交失败时按输出修正后重试

## 三、推送流程

1. 提交成功后 `git push` 推送到远程
2. push 被拒绝（远程有新提交）时：**先 `git pull --rebase` 再 push**，不要 force push
3. 分支无上游时用 `git push -u origin <分支名>` 建立跟踪

## 四、拉取/更新流程

1. 拉取前如有本地未提交改动，先确认这些改动是否要保留：
   - 保留：`git stash` 暂存 → `git pull --rebase` → `git stash pop` 恢复
   - 不保留：先与用户确认，不得擅自丢弃
2. `git pull --rebase` 拉取并变基；发生冲突时：
   - `git status` 查看冲突文件，用 read_file 阅读内容判断保留哪边
   - 手工修改解决后 `git add <文件>`，再 `git rebase --continue`
   - 无法自行判断时，说明冲突文件与内容并 ask_user 询问用户，绝不随意放弃任何一方的修改
3. 拉取完成后再 `git status` 确认工作区干净、本地与远程同步

## 五、禁止操作（沙盒会拒绝，也不得尝试绕过）

- 丢弃改动：`git reset --hard`、`git clean -f/-fd`
- 强制覆盖远程：`git push --force` / `-f`
- 强制删除：`git branch -D`、`git push --delete`
- 丢弃工作区修改：`git checkout -- <文件>`、`git restore .`

## 六、操作纪律

- 每次操作前后用 `git status` 确认仓库状态，防止误操作
- 多命令串联（`;`、`&&`）会被沙盒视为 risky 需要确认，尽量单条执行
- 所有提交/推送/拉取动作完成后，向用户简要汇报结果（提交哈希、分支、是否同步）
