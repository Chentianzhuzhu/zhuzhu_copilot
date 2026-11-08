# -*- coding: utf-8 -*-
"""最终修复：ZIP 层移除第20页不合规的 timing，覆盖正式文件（内容与视觉不变，仅去掉该页动画）"""
import zipfile
import os
import re
import subprocess

SRC = r'transformer_ppt/Transformer架构详解.pptx'
TMP = r'transformer_ppt/_fixed.pptx'

with zipfile.ZipFile(SRC) as z:
    names = z.namelist()
    data = {n: z.read(n) for n in names}

slide_path = 'ppt/slides/slide20.xml'
xml = data[slide_path].decode('utf-8')
new_xml = re.sub(r'<p:timing>.*?</p:timing>', '', xml, flags=re.S)
removed = len(xml) - len(new_xml)
data[slide_path] = new_xml.encode('utf-8')

with zipfile.ZipFile(TMP, 'w', zipfile.ZIP_DEFLATED) as zout:
    for n in names:
        zout.writestr(n, data[n])

os.replace(TMP, SRC)
print(f'已修复并覆盖（移除 {removed} 字符的 timing），大小 {os.path.getsize(SRC)} bytes')

# 立即验证 + 导出第 20 页
ps = r'transformer_ppt/verify20.ps1'
with open(ps, 'w', encoding='utf-8-sig') as f:
    f.write(f'''$ErrorActionPreference = "Continue"
$dir = "{os.path.abspath(r'transformer_ppt')}"
$file = (Get-ChildItem -Path $dir -Filter *.pptx | Where-Object {{ $_.Name -notlike "_*" }} | Select-Object -First 1).FullName
$app = New-Object -ComObject PowerPoint.Application
try {{
  $pres = $app.Presentations.Open($file,$true,$false,$false)
  Write-Output ("OPEN_OK slides=" + $pres.Slides.Count)
  $out = Join-Path $dir "preview"
  foreach ($i in 20) {{
    $t = Join-Path $out ("slide" + $i + ".png")
    $pres.Slides.Item($i).Export($t,"PNG",1600,900)
  }}
  $pres.Close()
  Write-Output "EXPORT_OK"
}} catch {{
  Write-Output ("FAIL: " + $_.Exception.Message)
}}
try {{ $app.Quit() }} catch {{}}
''')

r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
print(r.stdout.strip())
if r.stderr.strip():
    print('ERR:', r.stderr.strip()[:300])
