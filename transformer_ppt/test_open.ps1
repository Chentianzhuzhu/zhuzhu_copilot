$ErrorActionPreference = 'Stop'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$src = (Get-ChildItem -Path $dir -Filter *.pptx | Select-Object -First 1).FullName
$dst = Join-Path $env:TEMP "t_test.pptx"
$app = $null
try {
    Copy-Item $src $dst -Force
    Write-Output ("COPY_OK size=" + (Get-Item $dst).Length)
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($dst, $true, $false, $false)
    Write-Output ("OPEN_OK slides=" + $pres.Slides.Count)
    $pres.Close()
} catch {
    Write-Output ("FAIL: " + $_.Exception.Message)
} finally {
    if ($app) { try { $app.Quit() } catch {} }
}
