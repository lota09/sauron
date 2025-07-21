'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : DeptInfo.py
  - Description      : Integrate Every dept in a single module (Object-Oriented)
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2025.06.16 : Initial release
*******************************************************************'''

ICON_URL_SSU = "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg"

class dept_info:
    def __init__(self,dept_id,dept_ko,url,channel_id,icon_url,div_args=None,misc=[]):
        self.dept_id = dept_id
        self.dept_ko = dept_ko
        self.url = url
        self.channel_id = channel_id
        self.icon_url = icon_url
        self.div_args = div_args
        self.misc = misc            # 3 Type : binary, even, nonbin

usaint = \
    dept_info("usaint",
              "유세인트",
              'https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword',
              "1355604572353069200",
              ICON_URL_SSU,
              {'class_':'bg-white p-4 mb-5'})

disu_bold = \
    dept_info("disu_bold",
              "차세대반도체학과",
              'https://www.disu.ac.kr/community/notice?cidx=42&page=1',
              "1355609212016918608",
              ICON_URL_SSU,
              {'class_':'bbs_contents'},
              misc = ["binary"])

eco_bold = \
    dept_info("eco_bold",
              "경제학과",
              'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1',
              "1355609054629593289",
              ICON_URL_SSU,
              {'id':'bo_v_con'},
              misc = ["even"])

cse_bold = \
    dept_info("cse_bold",
              "컴퓨터학부",
              'https://cse.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1',
              "1358816727256793318",
              ICON_URL_SSU,
              {'id':'bo_v_con'},
              misc = ["even"])

aix_nonbin = \
    dept_info("aix_nonbin",
              "AI융합학부",
              'https://aix.ssu.ac.kr/notice.html?&page=1',
              "1360537451981967390",
              ICON_URL_SSU,
              {"class":"table-responsive"},
              misc = ["nonbin"])

disu_polaris = \
    dept_info("disu_polaris",
              "차세대반도체학과 POLARIS",
              "https://www.disu.ac.kr/community/notice?cidx=38&page=1",
              "1355609212016918608",
              ICON_URL_SSU,
              {'class_':'bbs_contents'},
              misc = ["binary"])

startup = \
    dept_info("startup",
              "숭실대학교 창업지원단",
              "https://startup.ssu.ac.kr/board/notice?boardEnName=notice&pageNum=1",
              "",
              ICON_URL_SSU)

DEPTS = [usaint,disu_bold,eco_bold,cse_bold,aix_nonbin,disu_polaris]