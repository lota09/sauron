#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/patch_api_depts.py — 깨진/막힌 사이트 5종을 depts_seed.csv에 일괄 복구(일회성, 멱등).

실측으로 확정한 최종값을 강제 기입한다(현재 CSV 상태와 무관하게 올바른 값으로). 다른 행·당신 편집분은 보존.
  · scatch_*(8): content_selector 정밀화(헤더 제외, 본문 div만)
  · lawyer     : 404 커스텀게시판 → WordPress로 이전 → html + 새 URL/셀렉터
  · sls        : 자유전공 신도메인 → html + 본문 셀렉터
  · media      : Next.js CSR(403) → json_api(sslip.io API, Lexical 본문)
  · startup    : React CSR → json_api(내부 API, HTML 본문)
JSON(fetch_config)은 콤마/따옴표가 있어 손편집이 위험 → csv 모듈로 안전 기록.

실행:  python init/patch_api_depts.py   →   python init/seed_db.py   (DB 반영: fetch_config ALTER + upsert)
※ 일회성. 실행 후 값은 CSV에 남으므로 삭제해도 됨(설정 기록용으로 둬도 무방).
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "depts_seed.csv")

SCATCH_SELECTOR = "div.col-xl-10 > div.p-4 > div:last-of-type"

# html 계열 정정(dept_id → 덮어쓸 필드)
HTML_FIXES = {
    "lawyer": {
        "fetch_type": "html",
        "list_url": "https://lawyer.ssu.ac.kr/%ed%95%99%ea%b3%bc-%ec%86%8c%ec%8b%9d/%ed%95%99%ea%b3%bc-%ea%b3%b5%ec%a7%80/",
        "link_selector": "tr > td.title > a",
        "content_selector": "div.td_box",
        "url_prefix": "",
    },
    "sls": {
        "fetch_type": "html",
        "list_url": "https://sls.ssu.ac.kr/%ed%95%99%eb%b6%80%ec%86%8c%ec%8b%9d/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/",
        "link_selector": "tr > td.title > a",
        "content_selector": "div.td_box",
        "url_prefix": "",
    },
}

# json_api 계열(dept_id → fetch_config + 참고용 list_url)
API_FIXES = {
    "media": {
        "list_url": "https://media.ssu.ac.kr/board/notices",
        "fetch_config": {
            "list_url": "https://3-37-127-112.sslip.io/v1/board/?page={page}&size=15&menuId=136",
            "list_path": "data.boards",
            "id_key": "id", "title_key": "title", "content_key": "content",
            "content_format": "lexical",
            "url_template": "https://media.ssu.ac.kr/board/notices/{id}",
            "page_base": 0,
            "headers": {"Origin": "https://media.ssu.ac.kr", "Referer": "https://media.ssu.ac.kr/"},
        },
    },
    "startup": {
        "list_url": "https://startup.ssu.ac.kr/board/notice",
        "fetch_config": {
            "list_url": "https://startup.ssu.ac.kr/api/board/content/list?boardEnName=notice&categoryCodeId&pageNum={page}&searchMonth",
            "list_path": "data.content.list",
            "id_key": "boardContentId", "title_key": "boardTitle", "content_key": "boardContent",
            "content_format": "html",
            "url_template": "https://startup.ssu.ac.kr/board/notice/{id}?boardEnName=notice",
            "page_base": 1,
        },
    },
}


def main():
    with open(CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys()) if rows else []
    if "fetch_config" not in fields:
        fields.append("fetch_config")
        for r in rows:
            r.setdefault("fetch_config", "")

    changed = {"scatch": 0, "html": [], "api": []}
    for r in rows:
        did = r.get("dept_id", "")
        if did in API_FIXES:
            spec = API_FIXES[did]
            r["fetch_type"] = "json_api"
            r["fetch_config"] = json.dumps(spec["fetch_config"], ensure_ascii=False)
            r["list_url"] = spec["list_url"]
            r["link_selector"] = ""
            r["content_selector"] = ""
            changed["api"].append(did)
        elif did in HTML_FIXES:
            r.update(HTML_FIXES[did])
            r.setdefault("fetch_config", "")
            r["fetch_config"] = ""       # html은 fetch_config 불필요
            changed["html"].append(did)
        elif did.startswith("scatch"):
            r["content_selector"] = SCATCH_SELECTOR
            changed["scatch"] += 1

    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[patch] scatch {changed['scatch']}행 셀렉터 / html 정정 {changed['html']} / json_api {changed['api']}")
    print("[patch] 다음: python init/seed_db.py")


if __name__ == "__main__":
    main()
