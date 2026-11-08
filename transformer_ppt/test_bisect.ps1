$ErrorActionPreference = 'Continue'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt\bisect"
$app = New-Object -ComObject PowerPoint.Application
foreach ($n in 1,4,7,10,13,16,19,22,24) {
    $src = Join-Path $dir ("keep" + $n + ".pptx")
    $tmp = Join-Path $env:TEMP ("k" + $n + ".pptx")
    Copy-Item $src $tmp -Force
    try {
        $pres = $app.Presentations.Open($tmp, $true, $false, $false)
        Write-Output ("keep" + $n + " -> OK")
        $pres.Close()
    } catch {
        Write-Output ("keep" + $n + " -> FAIL")
    }
}
try { $app.Quit() } catch {}
