$ErrorActionPreference = 'Stop'
$dir = "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\transformer_ppt"
$app = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    foreach ($name in @("testB_resave.pptx")) {
        $src = Join-Path $dir $name
        $dst = Join-Path $env:TEMP "t_b.pptx"
        Copy-Item $src $dst -Force
        try {
            $pres = $app.Presentations.Open($dst, $true, $false, $false)
            Write-Output ($name + " -> OPEN_OK slides=" + $pres.Slides.Count)
            $pres.Close()
        } catch {
            Write-Output ($name + " -> FAIL: " + $_.Exception.Message)
        }
    }
} finally {
    if ($app) { try { $app.Quit() } catch {} }
}
