'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchTitle.py
  - Description      : Fetch Title from url - each has unique fetch model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import unicodedata
from autoscraper import AutoScraper
import Update
import Content
import ClovaSummary
from Errors import FetchError, SummaryError

UPDATE_LIMIT = 5

try:
    import Hyperparms
    SUMMARY_EN = Hyperparms.SUMMARY_EN
except:
    SUMMARY_EN = True

#모델, 버퍼파일 이름 재설정

scraper = AutoScraper()

class NoticeData:
    def __init__(self,dept,title,url,summary =""):
        self.dept = dept
        self.title = title
        self.url = url
        self.summary = summary



def UpdateNotice(dept):
    dept_id = dept.dept_id

    # 그룹된 결과 가져오기
    model_id = dept_id.split('_')[0]
    scraper.load(f"models/model_{model_id}.json")

    # 페이지 1에 대한 소스 가져오기
    source_args = dept.build_source(1)
    scraped_dict = scraper.get_result_similar(**source_args, group_by_alias=True)
    titles = scraped_dict["title"]
    urls = scraped_dict["url"]

    # 페이지 2에 대한 소스 가져오기
    if "{{page}}" in dept.url:
        source_args_p2 = dept.build_source(2)
        scraped_dict_p2 = scraper.get_result_similar(**source_args_p2, group_by_alias=True)
        urls_p2 = scraped_dict_p2["url"]
    else:
        urls_p2 = []

    # 크롤링한 데이터가 없는경우
    if not titles or not urls:
        raise FetchError("Fetch Failed, There's nothing to fetch")

    # 신규 항목 인덱스 구하기 (차집합)
    new_indices = Update.UpdateState(dept_id, urls, urls_p2)
    updated_count = len(new_indices)

    # 신규 항목이 너무 많은 경우
    if updated_count > UPDATE_LIMIT:
        raise IndexError(f"Too many updated anouncement. Omitted {updated_count} new announcements.")

    # 새로운 항목이 없는경우
    if not new_indices:
        return None
    
    notice_list = []
    
    # 신규 항목들에 대해서 component 생성
    for new_idx in new_indices:

        title = unicodedata.normalize('NFC', titles[new_idx])
        url = dept.etc.get("url_prefix","") + urls[new_idx]

        #공지 내용 가져오기
        content = Content.FetchContent(dept.div_args,url)

        #클로바 요약
        if (not SUMMARY_EN):
            summary = "요약기능이 비활성화되었습니다."
        elif (content):
            try:
                summary = ClovaSummary.Summarize(f"제목:{title}\n내용:\n{content}")
            except SummaryError as e:
                summary = "요약을 실패하였습니다."
        else:
            summary = "요약할 내용이 없습니다."

        notice_list.append(NoticeData(dept, title, url, summary))

    return notice_list


if __name__ == '__main__':
    import DeptInfo
    result = UpdateNotice(DeptInfo.usaint)

    length = len(result)

    print(f"[{length}개 항목]")
    for i,item in enumerate(result):
        print(f"{i} : {item}")