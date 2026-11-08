$ErrorActionPreference = 'Stop'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$file = (Get-ChildItem -Path $dir -Filter *.pptx | Where-Object { $_.Name -notlike '_tmp*' } | Select-Object -First 1).FullName
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($file, $true, $false, $false)
$outDir = Join-Path $dir "preview"
foreach ($i in 20) {
    $target = Join-Path $outDir ("slide" + $i + ".png")
    $pres.Slides.Item($i).Export($target, "PNG", 1600, 900)
}
$pres.Close()
$app.Quit()
Write-Output "EXPORT_OK"
