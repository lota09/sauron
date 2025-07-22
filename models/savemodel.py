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
    'cse':'https://cse.ssu.ac.kr/bbs/board.php?bo_table=notice', \
    'aix':'https://aix.ssu.ac.kr/notice.html',\
    'custom':'https://youth.seoul.go.kr/infoData/sprtInfo/list.do?key=2309130006'
    }

wanted_list = ["https://aix.ssu.ac.kr/notice_view.html?category=1&idx=1592"]

result = scraper.build(url['aix'], wanted_list,update=False,text_fuzz_ratio=1)
length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")

scraper.save('models/test.json')

