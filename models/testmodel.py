'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : testmodel.py
  - Description      : test custom autoscraper model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper

scraper = AutoScraper()

url={'usaint':'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword',   \
    'eco':'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1', \
    'disu':'https://www.disu.ac.kr/community/notice?cidx=42&page=1',  \
    'custom':'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&slug=%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1%EC%97%B0%EA%B5%AC%EC%84%BC%ED%84%B0-2024-2%ED%95%99%EA%B8%B0-%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1-%ED%98%81%EC%8B%A0%EC%88%98%EC%97%85%EB%AA%A8%ED%98%95engaged-4&keyword'}

scraper.load('models/test.json')

titles=scraper.get_result_similar(url['custom'])
#titles=scraper.get_result_exact(url['usaint'])

length=len(titles)

print(titles)
print(length)