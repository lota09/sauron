'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : DeptInfo.py
  - Description      : Integrate Every dept in a single module (Object-Oriented)
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2025.06.16 : Initial release
*******************************************************************'''

ICON_URL_SSU = "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg"

class dept_info:
    def __init__(self,dept_id,dept_ko,url,channel_id,icon_url,div_args=None,etc={}):
        self.dept_id = dept_id
        self.dept_ko = dept_ko
        self.url = url
        self.channel_id = channel_id
        self.icon_url = icon_url
        self.div_args = div_args
        self.etc = etc

        self.css_sel = etc.get("css_sel",None)

    def build_htmlpage(self,url):

        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.firefox.service import Service
        from selenium.webdriver.firefox.options import Options
        
        try:
            import Hyperparms
            DEBUG_EN = Hyperparms.DEBUG_EN
        except:
            DEBUG_EN = False

        if DEBUG_EN:
            # Chrome 사용
            driver = webdriver.Chrome()
        else:
            # Firefox 옵션 설정 (headless 예시 포함)
            options = Options()
            options.add_argument('--headless')

            # Firefox 실행 파일 위치 지정
            options.binary_location = "/usr/bin/firefox"
            # geckodriver 서비스 설정
            service = Service(executable_path="/usr/local/bin/geckodriver")
            # 웹드라이버 객체 생성
            driver = webdriver.Firefox(service=service, options=options)

        driver.get(url)

        # 제목 링크가 로딩될 때까지 대기
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.css_sel))
            )
        finally:
            html = driver.page_source

            if DEBUG_EN:
                with open("dev/fetched_selenium.html", "w", encoding="utf-8") as f:
                    f.write(html)

            driver.quit()
        
        return html
    
    def build_source(self,page=1):
        url = self.url.replace("{{page}}", str(page))

        #url을 소스로 하는경우
        if self.css_sel is None:
            return {"url":url}
        #html을 소스로 하는경우
        else:
            return {"html":self.build_htmlpage(url)}

usaint = \
    dept_info("usaint",
              "유세인트",
              'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/page/{{page}}/?f&category=%ED%95%99%EC%82%AC&keyword',
              "1355604572353069200",
              ICON_URL_SSU,
              {'class_':'bg-white p-4 mb-5'})

disu = \
    dept_info("disu",
              "차세대반도체학과",
              'https://www.disu.ac.kr/community/notice?cidx=42&page={{page}}',
              "1355609212016918608",
              ICON_URL_SSU,
              {'class_':'bbs_contents'})

eco = \
    dept_info("eco",
              "경제학과",
              'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page={{page}}',
              "1355609054629593289",
              ICON_URL_SSU,
              {'id':'bo_v_con'})

cse = \
    dept_info("cse",
              "컴퓨터학부",
              'https://cse.ssu.ac.kr/bbs/board.php?bo_table=notice&page={{page}}',
              "1358816727256793318",
              ICON_URL_SSU,
              {'id':'bo_v_con'})

aix = \
    dept_info("aix",
              "AI융합학부",
              'https://aix.ssu.ac.kr/notice.html?&page={{page}}',
              "1360537451981967390",
              ICON_URL_SSU,
              {"class":"table-responsive"})

disu_polaris = \
    dept_info("disu_polaris",
              "차세대반도체학과 POLARIS",
              "https://www.disu.ac.kr/community/notice?cidx=38&page={{page}}",
              "1355609212016918608",
              ICON_URL_SSU,
              {'class_':'bbs_contents'})

startup = \
    dept_info("startup",
              "숭실대학교 창업지원단",
              "https://startup.ssu.ac.kr/board/notice?boardEnName=notice&pageNum={{page}}",
              "1397154831579484273",
              ICON_URL_SSU,
              etc={"css_sel":"[class^='Notice_title__'] a",
                   "url_prefix":"https://startup.ssu.ac.kr"})

infocom = \
    dept_info("infocom",
              "숭실대학교 전자정보공학부",
              "http://infocom.ssu.ac.kr/kor/notice/undergraduate.php?pNo={{page}}",
              "1398017032666222744",
              ICON_URL_SSU,
              etc={"url_prefix":"http://infocom.ssu.ac.kr"})

thinkgood = \
    dept_info("thinkgood",
              "씽굿",
              "https://www.thinkcontest.com/thinkgood/user/contest/index.do",
              "",
              None,
              etc={"css_sel":"[class^='list-thumb sub pick'] a",
                   "url_prefix":"https://www.thinkcontest.com/"})

DEPTS = [usaint,disu,eco,cse,aix,disu_polaris,startup,infocom]