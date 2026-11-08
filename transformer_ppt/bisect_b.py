# -*- coding: utf-8 -*-
"""测试B：python-pptx 只读+原样另存，验证保存链路是否损坏文件"""
from pptx import Presentation

SRC = r'transformer_ppt/Transformer架构详解.pptx'
p = Presentation(SRC)
p.save(r'transformer_ppt/testB_resave.pptx')
print('已另存 testB_resave.pptx')
