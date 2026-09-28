本目录存放工作流专属插件。每个插件是一个子目录，含 plugin.json 元数据与
可选的 SKILL.md / server.py（本地 stdio MCP server）：

  plugins/<name>/
    plugin.json      插件元数据（name/description/kind/...）
    SKILL.md         标准技能文件（skill / combined 型插件）
    server.py        本地 stdio MCP server 脚本（mcp / combined 型插件）

插件在激活本工作流时并入插件加载路径（~/.zhuzhu_Copilot/plugins/ 之外的第二来源）。
