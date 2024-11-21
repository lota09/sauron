from autoscraper import AutoScraper

scraper = AutoScraper()
url = 'https://www.disu.ac.kr/community/notice?cidx=42&page=1'

scraper.load('models/test.json')

print(scraper.get_result_similar(url))
#print(scraper.get_result_exact(url))