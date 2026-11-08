$ErrorActionPreference = 'Stop'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$src = Join-Path $dir "testA_noarrow.pptx"
$dst = Join-Path $env:TEMP "t_a.pptx"
$app = $null
try {
    Copy-Item $src $dst -Force
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($dst, $true, $false, $false)
    Write-Output ("A_OPEN_OK slides=" + $pres.Slides.Count)
    $pres.Close()
} catch {
    Write-Output ("A_FAIL: " + $_.Exception.Message)
} finally {
    if ($app) { try { $app.Quit() } catch {} }
}
