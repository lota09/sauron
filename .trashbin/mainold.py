"""""
from autoscraper import AutoScraper

scraper = AutoScraper()
"""
from Update import UpdateFetch

url={'usaint':'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword',   \
    'eco':'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1', \
    'disu':'https://www.disu.ac.kr/community/notice?cidx=42&page=1'}

"""
link_model
title_model
date_model


usaint_url='https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/page/3/?f&category=%ED%95%99%EC%82%AC&keyword'

usaint_path='./usaint-title.txt'
title_model='./usaint-latest-title'

"""


print(UpdateFetch('usaint-title',url['usaint'],'usaint-update.txt'))

#업데이트
#요약
#카톡전송""