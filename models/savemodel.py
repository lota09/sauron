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

dept = DeptInfo.infocom
html = dept.build_htmlpage()

wanted_title = "2025학년도 기업분석 전략특강 & 강소기업 발굴 공모전( ~8/17(일))"
wanted_url = "/kor/notice/undergraduate.php?m=v&idx=2203&pNo=1&code=notice"

wanted_dict = {"title" : [wanted_title],
               "url": [wanted_url]}


#url을 소스로 하는경우
if html is None:
    source_args = {"url":dept.url}
#html을 소스로 하는경우
else:
    source_args = {"html":html}

result = scraper.build(**source_args, wanted_dict=wanted_dict, update=False, text_fuzz_ratio=0.9)

length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")

scraper.save('models/model_test.json')