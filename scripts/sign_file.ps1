<#
.SYNOPSIS
    用代码签名证书（默认 zhutianliang）为指定文件做 Authenticode 签名。

.DESCRIPTION
    供 Inno Setup 的 SignTool 在【编译期】调用，用来签 Setup 与 unins???.exe；
    也可单独手工调用。

    私钥直接取自证书库 Cert:\CurrentUser\My —— 不落地为 .pfx，私钥因此
    既不会被写进磁盘，也不会随安装包分发（旧方案把 .pfx 打进安装包、在装完后
    由 PowerShell 补签卸载器，见下方「为什么必须编译期签」）。

⚠ 为什么必须编译期签：Inno 的卸载器【一旦带签名】，就会改为从外部
    unins???.msg 读取界面文本（签名的 exe 不能再塞入文本，否则签名失效），
    而该 .msg 只有在编译期启用 SignedUninstaller 时才会由 Setup 生成并落盘。
    若装完后才补签卸载器，卸载器会去找并不存在的 unins000.msg 并当场中止
    ——现象就是「卸载器签名无效 + 报 Messages file ... unins000.msg is missing」。

.PARAMETER Target
    待签名文件的绝对路径（Inno 的 SignTool 命令以 $f 传入）。

.PARAMETER CertSubject
    证书 Subject 的匹配子串，默认 zhutianliang。

.PARAMETER TimestampServer
    时间戳服务器。不可达时回退为无时间戳签名（签名仍有效，只是不抗证书过期）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sign_file.ps1 `
        -Target "C:\app\unins000.exe"

.OUTPUTS
    退出码 0 = 已签名且签名有效；1 = 失败（参数/证书/签名校验不通过）。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$CertSubject = 'zhutianliang',
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
    Write-Error "Target file not found: $Target"
    exit 1
}

$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -like "*$CertSubject*" -and $_.HasPrivateKey } |
    Select-Object -First 1
if (-not $cert) {
    Write-Error ("No code-signing certificate with private key found in " +
                 "Cert:\CurrentUser\My matching '$CertSubject'")
    exit 1
}

try {
    # 优先带时间戳签名；时间戳服务器不可达时回退为无时间戳签名（签名依然有效）
    try {
        Set-AuthenticodeSignature -FilePath $Target -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampServer | Out-Null
    } catch {
        Write-Warning "Timestamp server unreachable, signing without timestamp..."
        Set-AuthenticodeSignature -FilePath $Target -Certificate $cert `
            -HashAlgorithm SHA256 | Out-Null
    }
    $sig = Get-AuthenticodeSignature -FilePath $Target
    Write-Host "Signed $Target , status: $($sig.Status)"
    if ($sig.Status -ne 'Valid') { exit 1 }
} catch {
    Write-Error "Signing failed: $($_.Exception.Message)"
    exit 1
} finally {
    if ($cert) { $cert.Dispose() }
}
exit 0
