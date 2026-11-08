# -*- coding: utf-8 -*-
"""重打包 ZIP（python-pptx 保存后需要此步，否则 PowerPoint 可能拒绝打开）
   然后导出指定页为 PNG
"""
import zipfile
import os
import shutil
import subprocess
import sys

DIR = r'transformer_ppt'
MAIN = os.path.join(DIR, 'Transformer架构详解.pptx')
TMP = os.path.join(DIR, '_tmp_repack.pptx')

# 1) 重打包
with zipfile.ZipFile(MAIN) as zin:
    with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, zin.read(info.filename))

os.replace(TMP, MAIN)
print(f'已重打包: {os.path.getsize(MAIN)} bytes')

# 2) 调 PowerShell 导出指定页
pages = sys.argv[1] if len(sys.argv) > 1 else '20'
ps1 = os.path.join(DIR, 'export_pages.ps1')
with open(ps1, 'w', encoding='utf-8-sig') as f:
    f.write(f'''$ErrorActionPreference = 'Stop'
$dir = "{os.path.abspath(DIR)}"
$file = (Get-ChildItem -Path $dir -Filter *.pptx | Where-Object {{ $_.Name -notlike '_tmp*' }} | Select-Object -First 1).FullName
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($file, $true, $false, $false)
$outDir = Join-Path $dir "preview"
foreach ($i in {pages}) {{
    $target = Join-Path $outDir ("slide" + $i + ".png")
    $pres.Slides.Item($i).Export($target, "PNG", 1600, 900)
}}
$pres.Close()
$app.Quit()
Write-Output "EXPORT_OK"
''')

r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps1],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
print('STDOUT:', r.stdout.strip())
if r.stderr.strip():
    print('STDERR:', r.stderr.strip()[:500])
