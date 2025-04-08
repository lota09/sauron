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
    'custom':''
    }

wanted_list = ["[스파르탄SW교육원] 제23회 TOPCIT 정기평가/설명회"]

result = scraper.build(url['cse'], wanted_list,update=False,text_fuzz_ratio=1)
length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")

scraper.save('models/test.json')