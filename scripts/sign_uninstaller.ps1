# Sign Inno Setup generated uninstaller (unins000.exe) with the zhutianliang
# code-signing certificate so the UAC elevation prompt shows a trusted publisher.
# Invoked by the installer via: sign_uninstaller.ps1 -PfxPath ... -Password ... -Target ...
param(
    [Parameter(Mandatory=$true)][string]$PfxPath,
    [Parameter(Mandatory=$true)][string]$Password,
    [Parameter(Mandatory=$true)][string]$Target,
    [string]$TimestampServer = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $PfxPath)) {
    Write-Error "Signing key not found: $PfxPath"
    exit 1
}
if (-not (Test-Path $Target)) {
    Write-Error "Target file not found: $Target"
    exit 1
}

try {
    $pwdSec = ConvertTo-SecureString $Password -AsPlainText -Force
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($PfxPath, $pwdSec, 'Exportable')
} catch {
    Write-Error "Failed to load signing certificate: $($_.Exception.Message)"
    exit 1
}

try {
    # 优先带时间戳签名；离线/时间戳服务器不可达时回退为无时间戳签名（签名依然有效）
    try {
        Set-AuthenticodeSignature -FilePath $Target -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $TimestampServer | Out-Null
    } catch {
        Write-Host "Timestamp server unreachable, signing without timestamp..."
        Set-AuthenticodeSignature -FilePath $Target -Certificate $cert -HashAlgorithm SHA256 | Out-Null
    }
    $sig = Get-AuthenticodeSignature $Target
    Write-Host "Signed $Target , status: $($sig.Status)"
    if ($sig.Status -ne 'Valid') {
        exit 1
    }
} catch {
    Write-Error "Signing failed: $($_.Exception.Message)"
    exit 1
} finally {
    $cert.Dispose()
}
exit 0
