# -*- coding: utf-8 -*-
"""
crawl/diff.py — 신규 공지 감지 (URL 차집합) + 최초 시딩 + UPDATE_LIMIT 가드.

sauron Overview.py/Update.py 계승:
  [새 크롤 URL] − [기억된 notices URL] = [신규]  (URL 키 → 고정공지/순서꼬임 자동 흡수)

detect_new():
  - 미시딩 학과: seed_pages 페이지 URL 전량을 seen에 등록(무알림) 후 [] 반환.
  - 시딩된 학과: 신규가 UPDATE_LIMIT 초과면 사이트깨짐 의심 → seen만 갱신하고 TooManyNew 발생(알림 차단).
  - 정상: 신규를 오래된→최신 순서로 반환(발송 순서), seen 갱신.
"""
import config


class FetchEmpty(Exception):
    """목록이 비어있음(사이트 오류/구조변경 의심)."""


class TooManyNew(Exception):
    def __init__(self, count):
        self.count = count
        super().__init__(f"신규 {count}건 > UPDATE_LIMIT({config.UPDATE_LIMIT}) — 대량알림 차단")


def _scrape_pages(fetcher, dept, pages: int):
    """1..pages 페이지를 긁어 순서 보존 병합(중복 url 제거)."""
    seen_url = set()
    merged = []
    has_template = "{{page}}" in (dept.get("list_url") or "")
    n = pages if has_template else 1
    for p in range(1, n + 1):
        items = fetcher.scrape_list(dept, page=p)
        for it in items:
            if it["url"] not in seen_url:
                seen_url.add(it["url"])
                merged.append(it)
    return merged


def detect_new(store, fetcher, dept):
    dept_id = dept["dept_id"]
    seeded = store.is_seeded(dept_id)

    pages = int(dept.get("seed_pages") or config.SEED_PAGES) if not seeded else 2
    scraped = _scrape_pages(fetcher, dept, pages)

    if not scraped:
        raise FetchEmpty(f"{dept_id}: 가져올 공지가 없음")

    # 최초 시딩: 전량 기억('seeded'), 무알림
    if not seeded:
        store.seed_rows(dept_id, scraped)
        store.set_seeded(dept_id)
        return []

    seen = store.seen_urls(dept_id)
    # 오래된→최신 순서로 신규 추출 (목록은 보통 최신이 위 → 뒤집기)
    new_items = [it for it in reversed(scraped) if it["url"] not in seen]

    if len(new_items) > config.UPDATE_LIMIT:
        store.seed_rows(dept_id, scraped)    # 전량 'seeded'로 전진시켜 다음 런 반복 방지
        raise TooManyNew(len(new_items))

    if new_items:
        store.seed_rows(dept_id, new_items)  # 신규를 'seeded'로 기억 → _process_new_item이 승격
    return new_items
