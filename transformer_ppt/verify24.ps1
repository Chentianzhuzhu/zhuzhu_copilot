$ErrorActionPreference = "Continue"
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$file = (Get-ChildItem -Path $dir -Filter *.pptx | Where-Object { $_.Name -notlike "_*" } | Select-Object -First 1).FullName
$app = New-Object -ComObject PowerPoint.Application
try {
  $pres = $app.Presentations.Open($file,$true,$false,$false)
  Write-Output ("OPEN_OK slides=" + $pres.Slides.Count)
  $out = Join-Path $dir "preview"
  $t = Join-Path $out "slide24.png"
  $pres.Slides.Item(24).Export($t,"PNG",1600,900)
  $pres.Close()
  Write-Output "EXPORT_OK"
} catch {
  Write-Output ("FAIL: " + $_.Exception.Message)
}
try { $app.Quit() } catch {}
