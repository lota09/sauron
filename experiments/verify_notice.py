#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
experiments/verify_notice.py — 공지 이미지가 LLM에 제대로 들어가고 '읽히는지' 검증.

핵심: LLM에게 **힌트를 전혀 주지 않는다**(제목·본문·'공지라는 사실'도 X). 오직 이미지 + "구체적으로
설명하라"만 던져, 모델이 그 이미지에서 실제로 무엇을 읽어내는지 순수 확인한다. 이미지를 한 장씩 보낸다.

파이프라인과 동일 경로로 이미지를 추출(html은 fetch_content의 images, json_api는 API 본문 파싱).
json_api(media·startup)는 그 URL을 찾을 때까지 목록을 여러 페이지 훑는다.

실행(★ LLM·사이트에 닿는 PC/venv):
  python experiments/verify_notice.py <dept_id> "<notice_url>"
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from db.store import Store
from crawl.fetcher import Fetcher
from crawl import apiparse
from summarize.vision import to_data_url, HAVE_PIL

# 힌트 없는 순수 이미지 설명 프롬프트(제목/본문/공지 언급 없음)
DESCRIBE = ("이 이미지를 최대한 구체적으로 설명해줘. 이미지 안에 보이는 모든 텍스트"
            "(제목·날짜·기간·숫자·표·연락처 등)를 빠짐없이 그대로 옮기고, 그림/레이아웃도 한국어로 묘사해줘.")


def resolve(dept, url, f, store):
    """(title, images[list]) 반환 — 파이프라인과 동일 추출 경로."""
    ftype = dept.get("fetch_type", "html")
    if ftype == "json_api":
        cfg = json.loads(dept["fetch_config"])
        base = cfg.get("page_base", 1)
        target = url.split("?")[0]
        for p in range(base, base + 8):
            data = f._get_json(cfg["list_url"].format(page=p), cfg.get("headers"))
            for it in (apiparse.dig(data, cfg["list_path"]) or []):
                if cfg["url_template"].format(id=it.get(cfg["id_key"])).split("?")[0] == target:
                    raw = apiparse.to_html(cfg.get("content_format", "html"), it.get(cfg["content_key"]) or "")
                    return (it.get(cfg["title_key"]) or "(제목없음)"), f._extract_images(raw, url)
        return None, None
    # html: fetch_content가 이미 '정제 전' 원본에서 이미지를 뽑아 반환 → 그대로 사용
    detail = f.fetch_content(dept, url)
    row = store._con.execute("SELECT title FROM notices WHERE url=?", (url,)).fetchone()
    title = row["title"] if row else "(제목미상)"
    return title, detail.get("images") or []


def main():
    if len(sys.argv) < 3:
        print('사용법: python experiments/verify_notice.py <dept_id> "<url>"')
        sys.exit(1)
    dept_id, url = sys.argv[1], sys.argv[2]
    store = Store(config.DB_PATH)
    dept = store.get_dept(dept_id)
    if not dept:
        print(f"dept 없음: {dept_id}"); store.close(); sys.exit(1)
    f = Fetcher()
    title, images = resolve(dept, url, f, store)
    store.close()
    if images is None:
        print("URL을 최근 목록에서 못 찾음(json_api). 더 오래된 공지거나 URL 불일치."); sys.exit(1)

    print(f"[dept] {dept_id} ({dept.get('fetch_type')})   [제목] {title}")
    print(f"[추출] 이미지 {len(images)}장: " + ", ".join(i['filename'][:34] for i in images[:6]))
    if not images:
        print("→ 추출 0장(이미지 없는 공지이거나 셀렉터/파싱 문제)"); return

    from summarize.llm import default_summarizer
    summ = default_summarizer()
    summ.ensure_model()
    for i, img in enumerate(images[:config.LLM_VISION_MAX_IMAGES], 1):
        du = to_data_url(img.get("url", ""), config.LLM_VISION_MAX_PX)
        if not du:
            print(f"\n[이미지 {i}] {img['filename'][:40]} → 로드/디코딩 실패(전송 불가)"); continue
        kb = len(du) * 3 // 4 // 1024
        content = [{"type": "text", "text": DESCRIBE},
                   {"type": "image_url", "image_url": {"url": du}}]
        try:
            text = summ._call(summ.model, [{"role": "user", "content": content}])
        except Exception as e:
            print(f"\n[이미지 {i}] LLM 오류: {e}"); continue
        print(f"\n[이미지 {i}/{min(len(images),config.LLM_VISION_MAX_IMAGES)}] {img['filename'][:40]} "
              f"· {kb}KB · Pillow={HAVE_PIL}")
        for ln in (text or "").splitlines():
            print("   " + ln)


if __name__ == "__main__":
    main()
