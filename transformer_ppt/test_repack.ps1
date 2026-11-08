$ErrorActionPreference = 'Continue'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$app = New-Object -ComObject PowerPoint.Application
$src = Join-Path $dir "repack.pptx"
$tmp = Join-Path $env:TEMP "repack_test.pptx"
Copy-Item $src $tmp -Force
try {
    $pres = $app.Presentations.Open($tmp, $true, $false, $false)
    Write-Output ("REPACK -> OK slides=" + $pres.Slides.Count)
    $pres.Close()
} catch {
    Write-Output ("REPACK -> FAIL: " + $_.Exception.Message)
}
try { $app.Quit() } catch {}
