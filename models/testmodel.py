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
    'cse':'https://cse.ssu.ac.kr/bbs/board.php?bo_table=notice', \
    'aix':'https://aix.ssu.ac.kr/notice.html',\
    'custom':''
    }

def FetchSimilar(model,url):
    scraper.load(model)
    return scraper.get_result_similar(url,contain_sibling_leaves=False)


result = FetchSimilar("models/test.json",url['aix'])
length=len(result)

print(f"[{length}개 항목]")
for i,item in enumerate(result):
    print(f"{i} : {item}")


    #aix는 주요 공지사항 개념이 없고, 장기(고정) 공지사항이 있으며 둘을 구분하기 어려움
    #새로운 접근법을 사용해야할수도 : 날짜로 최신항목을 구분하는 방법이 있을듯 함 - 그런데 [공지]와 [공지]가 아닌것이 같은날짜에 올라오는경우 예외처리해야함