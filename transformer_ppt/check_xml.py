# -*- coding: utf-8 -*-
"""检查 pptx 包内所有 XML 是否 well-formed"""
import zipfile
import xml.etree.ElementTree as ET
import io

PATH = r'transformer_ppt/Transformer架构详解.pptx'
bad = []
out = io.StringIO()

with zipfile.ZipFile(PATH) as z:
    names = [n for n in z.namelist() if n.endswith('.xml') or n.endswith('.rels')]
    out.write(f'XML 文件数: {len(names)}\n')
    for n in names:
        try:
            ET.fromstring(z.read(n))
        except ET.ParseError as e:
            bad.append((n, str(e)))
    # 检查 presentation.xml 里引用的 slide 是否都存在
    try:
        pres = ET.fromstring(z.read('ppt/presentation.xml'))
        rels = ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))
        rid_map = {r.get('Id'): r.get('Target') for r in rels}
        ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
              'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
        ids = [s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
               for s in pres.findall('.//p:sldId', ns)]
        out.write(f'sldId 数: {len(ids)}\n')
        missing = []
        for rid in ids:
            t = rid_map.get(rid)
            if not t:
                missing.append(f'{rid} (无 rId 映射)')
                continue
            path = 'ppt/' + t.lstrip('/').replace('ppt/', '', 1) if t.startswith('ppt/') else 'ppt/' + t
            path = path.replace('ppt/slides/', 'ppt/slides/')
            if path not in z.namelist():
                missing.append(f'{rid} -> {t}')
        if missing:
            out.write(f'引用缺失: {missing}\n')
        else:
            out.write('所有 slide 引用正常\n')
    except Exception as e:
        out.write(f'presentation.xml 解析异常: {e}\n')

out.write(f'\n损坏 XML: {len(bad)}\n')
for n, e in bad:
    out.write(f'  {n}: {e}\n')

with open('transformer_ppt/xmlcheck.txt', 'w', encoding='utf-8') as f:
    f.write(out.getvalue())
print('done, bad =', len(bad))
