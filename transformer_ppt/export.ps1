$ErrorActionPreference = 'Stop'
try {
    $dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
    $file = Get-ChildItem -Path $dir -Filter *.pptx | Select-Object -First 1
    Write-Output ("FILE=" + $file.FullName)
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($file.FullName, $true, $false, $false)
    $outDir = Join-Path $dir "preview"
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
    foreach ($i in 1,2,3,4,11,15,20,23) {
        $slide = $pres.Slides.Item($i)
        $target = Join-Path $outDir ("slide" + $i + ".png")
        $slide.Export($target, "PNG", 1280, 720)
    }
    $pres.Close()
    $app.Quit()
    Write-Output "EXPORT_OK"
} catch {
    Write-Output ("FAIL: " + $_.Exception.Message)
}
