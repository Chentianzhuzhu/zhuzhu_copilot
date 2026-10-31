package com.zhuzhu.copilot.agent

import com.zhuzhu.copilot.model.ToolSpec

class ToolRegistry(tools: Set<AgentTool>) {
    private val byName: Map<String, AgentTool> = tools.associateBy { it.spec.name }

    fun toolNamesFor(grantedPermissions: Set<String>): Set<String> =
        byName.values
            .filter { it.requiredPermission == null || it.requiredPermission in grantedPermissions }
            .map { it.spec.name }
            .toSet()

    fun specsFor(grantedPermissions: Set<String>): List<ToolSpec> =
        byName.values
            .filter { it.requiredPermission == null || it.requiredPermission in grantedPermissions }
            .map { it.spec }

    fun find(name: String): AgentTool? = byName[name]
}
