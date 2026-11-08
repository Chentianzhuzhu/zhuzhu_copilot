# -*- coding: utf-8 -*-
"""测试A：删除第20页整个 timing 元素后，看 PowerPoint 能否打开"""
import zipfile
import os
import re
import subprocess
import shutil

SRC = r'transformer_ppt/Transformer架构详解.pptx'
TMPDIR = r'transformer_ppt/trial'
os.makedirs(TMPDIR, exist_ok=True)

# 第20页对应 slide21.xml（第1页是slide1.xml，通常一一对应，但需确认rel）
with zipfile.ZipFile(SRC) as z:
    names = z.namelist()
    data = {n: z.read(n) for n in names}

# 找出第20个slide在包内的路径
rels = data['ppt/_rels/presentation.xml.rels'].decode('utf-8')
pres = data['ppt/presentation.xml'].decode('utf-8')
m = re.search(r'<p:sldIdLst>(.*?)</p:sldIdLst>', pres, re.S)
ids = re.findall(r'<p:sldId[^>]*r:id="(rId\d+)"', m.group(1))
target_rid = ids[19]
target = re.search(rf'Id="{target_rid}"[^>]*Target="([^"]+)"', rels).group(1)
slide_path = 'ppt/' + target.lstrip('/').replace('ppt/', '')
print('第20页路径:', slide_path)

# 删除该页的 <p:timing>...</p:timing>
xml = data[slide_path].decode('utf-8')
new_xml = re.sub(r'<p:timing>.*?</p:timing>', '', xml, flags=re.S)
print('timing 移除:', len(xml) - len(new_xml), 'chars')
data[slide_path] = new_xml.encode('utf-8')

dst = os.path.join(TMPDIR, 'no_timing.pptx')
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
    for n in names:
        zout.writestr(n, data[n])

ps = os.path.join(TMPDIR, 't.ps1')
with open(ps, 'w', encoding='utf-8-sig') as f:
    f.write(f'''$ErrorActionPreference = "Continue"
$app = New-Object -ComObject PowerPoint.Application
$p = Join-Path $env:TEMP "notiming.pptx"
Copy-Item "{os.path.abspath(dst)}" $p -Force
try {{
  $pr = $app.Presentations.Open($p,$true,$false,$false)
  Write-Output "NO_TIMING -> OK"
  $pr.Close()
}} catch {{
  Write-Output ("NO_TIMING -> FAIL: " + $_.Exception.Message)
}}
try {{ $app.Quit() }} catch {{}}
''')

r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
print(r.stdout.strip())
