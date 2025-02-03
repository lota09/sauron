'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : savemodel.py
  - Description      : save custom autoscraper model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
scraper = AutoScraper()


url={'usaint':'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword',   \
    'eco':'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1', \
    'disu':'https://www.disu.ac.kr/community/notice?cidx=42&page=1',  \
    'custom':'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&slug=%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1%EC%97%B0%EA%B5%AC%EC%84%BC%ED%84%B0-2024-2%ED%95%99%EA%B8%B0-%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1-%ED%98%81%EC%8B%A0%EC%88%98%EC%97%85%EB%AA%A8%ED%98%95engaged-4&keyword'}

wanted_list = ["https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&amp;category=%ED%95%99%EC%82%AC&amp;paged=1&amp;slug=%EC%A6%9D%EB%AA%85%EC%84%9C-%EB%B0%9C%EA%B8%89-%EC%84%9C%EB%B9%84%EC%8A%A4-%EC%9D%BC%EC%8B%9C-%EC%A4%91%EB%8B%A8-%EC%95%88%EB%82%B4-3&amp;keyword"]

result = scraper.build(url['usaint'], wanted_list,update=False)
length=len(result)

print(result)
print(length)

scraper.save('models/test.json')
