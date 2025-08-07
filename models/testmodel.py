'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : testmodel.py
  - Description      : test custom autoscraper model
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

dept_id = dept.dept_id
source_args = dept.build_source(1)

# 그룹된 결과 가져오기
scraper.load(f"models/model_test.json")
result = scraper.get_result_similar(**source_args, group_by_alias=True, contain_sibling_leaves=False)

# url_prefix 가 포함된경우 넣기
result["url"] = [dept.etc.get("url_prefix","") + url for url in result["url"]]
result_list = result["title"] + result["url"]

length=len(result_list)

print(f"[{length}개 항목]")
for i,item in enumerate(result_list):
    print(f"{i} : {item}")