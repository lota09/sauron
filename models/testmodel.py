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

dept = DeptInfo.startup

dept_id = dept.dept_id
html = dept.html

#url을 소스로 하는경우
if html is None:
    source_args = {"url":dept.url}
#html을 소스로 하는경우
else:
    source_args = {"html":html}

# 그룹된 결과 가져오기
scraper.load(f"models/model_test.json")
result = scraper.get_result_similar(**source_args, group_by_alias=False, contain_sibling_leaves=False)

length=len(result)

print(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")





    #aix는 주요 공지사항 개념이 없고, 장기(고정) 공지사항이 있으며 둘을 구분하기 어려움
    #새로운 접근법을 사용해야할수도 : 날짜로 최신항목을 구분하는 방법이 있을듯 함 - 그런데 [공지]와 [공지]가 아닌것이 같은날짜에 올라오는경우 예외처리해야함