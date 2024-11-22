'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : savemodel.py
  - Description      : save custom autoscraper model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
scraper = AutoScraper()

url = 'https://www.disu.ac.kr/community/notice?cidx=42&page=1'
wanted_list = ["https://www.disu.ac.kr/community/notice?md=v&bbsidx=7978"]
 
result = scraper.build(url, wanted_list)
print(result)

scraper.save('models/test.json')
