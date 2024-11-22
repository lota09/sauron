'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Update.py
  - Description      : Fetch and tell apart the new announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
import os

scraper = AutoScraper()

def FetchSimilar(model,url):
    scraper.load(model)
    return scraper.get_result_similar(url)

#새로운 공지인지 확인
def UpdateFetch(model,url,file_path):

    #fetch
    new_title=FetchSimilar(model,url)[0]

    # 파일이 존재하지 않으면 파일을 만듭니다.
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(new_title + '\n')
            file.close()
        return new_title
    
    # 파일의 기존 첫 줄을 읽어옵니다.
    with open(file_path, 'r', encoding='utf-8') as file:
        previous_title = file.readline().strip()  # 첫 줄을 읽고 양쪽 공백을 제거합니다.
    # 비교하여 같으면 아무 동작도 하지 않고, 다르면 갱신
    if previous_title != new_title:
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(new_title + '\n')
            file.close()
        return new_title
    
    return