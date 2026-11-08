$ErrorActionPreference = 'Stop'
try {
    $dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
    $file = Get-ChildItem -Path $dir -Filter *.pptx | Select-Object -First 1
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($file.FullName, $true, $false, $false)
    $outDir = Join-Path $dir "preview"
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
    $total = $pres.Slides.Count
    Write-Output ("TOTAL=" + $total)
    for ($i = 1; $i -le $total; $i++) {
        $target = Join-Path $outDir ("slide" + $i + ".png")
        $pres.Slides.Item($i).Export($target, "PNG", 1600, 900)
    }
    $pres.Close()
    $app.Quit()
    Write-Output "ALL_OK"
} catch {
    Write-Output ("FAIL: " + $_.Exception.Message)
}
