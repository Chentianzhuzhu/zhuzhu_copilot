# -*- coding: utf-8 -*-
import os
base = r'C:\Users\zhuzhu\Desktop\my first android app'
site = os.path.join(base, 'website')
for d in ['home','features','download','assets']:
    os.makedirs(os.path.join(site, d), exist_ok=True)
print('dirs ok')
