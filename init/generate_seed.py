#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/generate_seed.py  —  1회성 빌드 도구
ICT-project의 notificationList.csv(cp949)를 sauron_reborn의 depts 시드(UTF-8)로 변환.
- dept_id 슬러그 부여(서브도메인 기반 + 충돌 시 major로 구분)
- fetch_type 자동 분류(ICT fetch_tool.py 특수 케이스 반영)
- sauron DeptInfo.py의 channel_id / icon_url 병합
- sauron 전용(창업지원단 등) 추가 학과 append
출력: init/depts_seed.csv
"""
import csv, io, os, re
from urllib.parse import urlparse, parse_qs, unquote

SRC = os.environ.get("ICT_CSV", "/mnt/user-data/uploads/ICT-project/init/notificationList.csv")
OUT = os.path.join(os.path.dirname(__file__), "depts_seed.csv")

ICON_SSU = "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg"

# scatch 포털 카테고리(1~8) 고정 슬러그
PORTAL_SLUG = {
    "학사": "usaint",          # sauron usaint 채널 계승
    "장학": "portal_janghak",
    "국제교류": "portal_gukje",
    "외국인유학생": "portal_foreign",
    "채용": "portal_chaeyong",
    "비교과·행사": "portal_event",
    "봉사": "portal_bongsa",
    "기타": "portal_etc",
}

# sauron DeptInfo.py에서 확인된 실제 채널ID (host 또는 host+category로 매칭)
SAURON_CHANNELS = {
    "usaint":  "1355604572353069200",   # scatch 학사
    "eco":     "1355609054629593289",
    "cse":     "1358816727256793318",
    "aix":     "1360537451981967390",
    "disu":    "1355609212016918608",
    "infocom": "1398017032666222744",
}

def classify_fetch_type(url: str) -> str:
    if url.startswith("http://ssfilm.ssu.ac.kr/notice/notice_list"):
        return "json_ssfilm"
    if url.startswith("https://api.mediamba.ssu.ac.kr/v1/board"):
        return "json_mediamba"
    if url.startswith("http://media.ssu.ac.kr/sub.php"):
        return "onclick_media"
    if url.startswith("https://lawyer.ssu.ac.kr/web/05/notice_list.do"):
        return "post_lawyer"
    if url.startswith("https://materials.ssu.ac.kr/bbs/board.php?tbl=bbs51"):
        return "dom_materials"
    return "html"

def host_slug(url: str) -> str:
    host = urlparse(url).netloc
    labels = host.split(".")
    # www / api 접두 제거 후 첫 라벨
    while labels and labels[0] in ("www", "api"):
        labels = labels[1:]
    return labels[0] if labels else "dept"

def portal_category(url: str):
    q = parse_qs(urlparse(url).query)
    cat = q.get("category", [None])[0]
    return unquote(cat) if cat else None

def main():
    raw = open(SRC, "rb").read().decode("cp949")
    rows = list(csv.DictReader(io.StringIO(raw)))

    out_rows = []
    used_ids = {}
    for r in rows:
        url = r["url"].strip()
        host = urlparse(url).netloc
        # dept_id 결정
        if host == "scatch.ssu.ac.kr":
            cat = portal_category(url)
            dept_id = PORTAL_SLUG.get(cat, "portal_" + (cat or "x"))
        else:
            dept_id = host_slug(url)
        # 충돌 시 major/일련번호로 구분 (infocom 두 전공 등)
        if dept_id in used_ids:
            suffix = (r.get("major") or r.get("department") or "").strip()
            suffix = re.sub(r"[^0-9A-Za-z가-힣]", "", suffix)[:6] or str(used_ids[dept_id] + 1)
            dept_id = f"{dept_id}_{suffix}"
        used_ids[dept_id] = used_ids.get(dept_id, 0) + 1

        ftype = classify_fetch_type(url)
        out_rows.append({
            "dept_id": dept_id,
            "name_ko": (r["title"] or "").strip(),
            "college": (r.get("college") or "").strip(),
            "department": (r.get("department") or "").strip(),
            "major": (r.get("major") or "").strip(),
            "list_url": url,
            "link_selector": (r.get("link_selector") or "").strip(),
            "content_selector": (r.get("content_selector") or "").strip(),
            "url_prefix": "",
            "fetch_type": ftype,
            "login": int(r.get("login") or 0),
            "seed_pages": 3,
            "discord_channel_id": SAURON_CHANNELS.get(dept_id, ""),
            "discord_role_id": "",
            "icon_url": ICON_SSU,
            "active": 1,
            "note": ("셀렉터 미정" if ftype == "html" and not (r.get("link_selector") or "").strip() else ""),
        })

    # sauron 전용 추가 학과 (ICT CSV에 없음)
    extra = [
        dict(dept_id="startup", name_ko="숭실대학교 창업지원단", college="", department="창업지원단", major="",
             list_url="https://startup.ssu.ac.kr/board/notice?boardEnName=notice&pageNum={{page}}",
             link_selector="[class^='Notice_title__'] a", content_selector="",
             url_prefix="https://startup.ssu.ac.kr", fetch_type="html", login=0, seed_pages=3,
             discord_channel_id="1397154831579484273", discord_role_id="", icon_url=ICON_SSU, active=1,
             note="sauron 계승. JS렌더 가능성-크롤 확인 필요"),
        dict(dept_id="disu_polaris", name_ko="차세대반도체학과 POLARIS", college="", department="차세대반도체학과", major="POLARIS",
             list_url="https://www.disu.ac.kr/community/notice?cidx=38&page={{page}}",
             link_selector="tbody > tr > td.title.noti-tit > a", content_selector="#printbody > div",
             url_prefix="", fetch_type="html", login=0, seed_pages=3,
             discord_channel_id="1355609212016918608", discord_role_id="", icon_url=ICON_SSU, active=1,
             note="sauron 계승(disu 채널 공유)"),
    ]
    out_rows.extend(extra)

    cols = ["dept_id","name_ko","college","department","major","list_url","link_selector",
            "content_selector","url_prefix","fetch_type","login","seed_pages",
            "discord_channel_id","discord_role_id","icon_url","active","note"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)

    # 요약 출력
    from collections import Counter
    print(f"총 {len(out_rows)}개 학과 → {OUT}")
    print("fetch_type 분포:", dict(Counter(r["fetch_type"] for r in out_rows)))
    print("채널ID 보유:", sum(1 for r in out_rows if r["discord_channel_id"]))
    print("셀렉터 미정:", sum(1 for r in out_rows if r["note"] == "셀렉터 미정"))
    dup = [k for k, v in Counter(r["dept_id"] for r in out_rows).items() if v > 1]
    print("dept_id 중복:", dup or "없음")

if __name__ == "__main__":
    main()
