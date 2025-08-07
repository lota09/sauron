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

dept = DeptInfo.usaint
source_args = dept.build_source(1)

wanted_title = "2025학년도 2학기 숭실사이버대학교 학점교류 과목 수강 안내"
wanted_url = "https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&paged=1&slug=2025%ED%95%99%EB%85%84%EB%8F%84-2%ED%95%99%EA%B8%B0-%EC%88%AD%EC%8B%A4%EC%82%AC%EC%9D%B4%EB%B2%84%EB%8C%80%ED%95%99%EA%B5%90-%ED%95%99%EC%A0%90%EA%B5%90%EB%A5%98-%EA%B3%BC%EB%AA%A9-%EC%88%98%EA%B0%95&keyword"

wanted_dict = {"title" : [wanted_title],
               "url": [wanted_url]}

result = scraper.build(**source_args, wanted_dict=wanted_dict, update=False, text_fuzz_ratio=1)

length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")

scraper.save('models/model_test.json')