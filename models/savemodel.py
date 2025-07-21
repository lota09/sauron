'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : savemodel.py
  - Description      : save custom autoscraper model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from autoscraper import AutoScraper
import DeptInfo

scraper = AutoScraper()

wanted_list = ["2025.06.16"]

result = scraper.build(DeptInfo.startup.url, wanted_list,update=False,text_fuzz_ratio=1)
length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")

scraper.save('models/test.json')

