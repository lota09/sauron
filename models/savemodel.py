<<<<<<< HEAD
from autoscraper import AutoScraper
scraper = AutoScraper()

url = 'https://www.disu.ac.kr/community/notice?cidx=42&page=1'
wanted_list = ["https://www.disu.ac.kr/community/notice?md=v&bbsidx=7978"]
 
result = scraper.build(url, wanted_list)
print(result)

scraper.save('models/test.json')
=======
from autoscraper import AutoScraper
scraper = AutoScraper()

url = 'https://www.disu.ac.kr/community/notice?cidx=42&page=1'
wanted_list = ["https://www.disu.ac.kr/community/notice?md=v&bbsidx=7978"]
 
result = scraper.build(url, wanted_list)
print(result)

scraper.save('models/test.json')
>>>>>>> 9b1d5d0d630f361f579ab418372976a4468ba1c8
