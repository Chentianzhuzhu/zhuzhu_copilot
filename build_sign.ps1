# zhuzhu Copilot 一键打包 + 签名（兼容入口）
#
# 说明：完整流程已重构到根目录 build_release.ps1（单一入口，含版本号同步、
#       体检、分阶段控制、产物自检、日志留档）。本脚本仅作向后兼容的薄壳，
#       所有参数原样透传，不再自行维护流程，避免两套脚本步骤不一致。
#
# 用法（与旧版一致）：
#   .\build_sign.ps1                       # 完整打包 + 签名
#   .\build_sign.ps1 -Version 5.1.2        # 同时把版本号升到 5.1.2
#   .\build_sign.ps1 -NoSign               # 不签名（本地验证）
#   .\build_sign.ps1 -DryRun               # 只体检环境，不构建
#   .\build_sign.ps1 -SkipRuntime -NoSign  # 复用运行时、不签名，快速出安装包
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]]$Args
)

$target = Join-Path $PSScriptRoot 'build_release.ps1'
if (-not (Test-Path $target)) {
    Write-Host "[FAIL] 找不到主流水线脚本: $target" -ForegroundColor Red
    exit 1
}

Write-Host "[提示] build_sign.ps1 已重构为薄壳，实际执行: build_release.ps1" -ForegroundColor DarkGray
& $target @Args
exit $LASTEXITCODE
