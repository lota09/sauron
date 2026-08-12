# -*- coding: utf-8 -*-
"""
experiments/vision_probe/probe.py — 온디바이스 비전 판독력 실험 하니스.

목적: "같은 포스터를 어떤 전처리로 넣으면 2B가 작은 글자(신청기간 날짜 등)를 읽나?"를
      실제 LLM 서버(config.LLM_BASE_URL)에 붙여 '경험적으로' 찾는다. (오프라인 유닛테스트 tests/ 와 별개)

핵심 가설: **세로 타일링(수동 Pan-and-Scan)** — 긴 포스터를 N등분해 각 조각을 개별 이미지로 보내면
  조각당 인코더 해상도가 그대로라 작은 글자가 상대적으로 커져 판독력이 오른다. 다운스케일은 반대(글자 뭉갬).

실행(★ LLM 서버에 닿는 PC/기기에서):
  python experiments/vision_probe/probe.py --image poster.png
  python experiments/vision_probe/probe.py --url "https://scatch.ssu.ac.kr/...."          # 공지 URL에서 이미지 추출
  python experiments/vision_probe/probe.py --url "..." --dept scatch_haksa                # 파서 학과 강제
  python experiments/vision_probe/probe.py --image a.png --sizes 768,1536 --tiles 2,3,4 --gray

각 변형마다 [라벨 · 이미지수 · 페이로드KB · prompt_tokens · 지연 · 모델답변]을 출력 → 어떤 변형이 날짜를
맞히는지 눈으로 비교. 생성 변형은 out/ 에 저장(직접 열어 확인).

의존: Pillow, requests   (pip install pillow requests)
"""
import argparse
import base64
import io
import os
import sys
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import requests
import config

try:
    from PIL import Image, ImageOps
except Exception:
    print("Pillow 필요: pip install pillow")
    sys.exit(1)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
DEFAULT_PROMPT = ("다음 포스터 이미지를 보고 신청기간(정확한 날짜)·대상·주요 일정만 한국어 불릿으로 적어줘. "
                  "이미지에 보이는 숫자와 날짜를 그대로 옮길 것. 안 보이면 '안 보임'.")


def resolve_model(cli):
    if cli:
        return cli
    from summarize.llm import fetch_loaded_model, DEFAULT_MODEL
    m = config.LLM_MODEL
    if str(m).strip().lower() in ("", "auto"):
        return fetch_loaded_model(config.LLM_BASE_URL) or DEFAULT_MODEL
    return m


def _load_bytes(src):
    """로컬 경로 또는 http(s) 이미지 URL을 바이트로."""
    if src.startswith("http://") or src.startswith("https://"):
        r = requests.get(src, timeout=60, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        return r.content
    if not os.path.exists(src):
        print(f"파일 없음: {src}\n  → 절대경로(예: C:\\...\\poster.png) 또는 이미지 URL을 주세요.")
        sys.exit(1)
    with open(src, "rb") as f:
        return f.read()


def source_image(args):
    """--image면 파일/URL 바이트, --url이면 공지 파싱 후 이미지 다운로드."""
    if args.image:
        return _load_bytes(args.image)   # 로컬 경로 또는 이미지 URL 모두 허용
    from db.store import Store
    from crawl.fetcher import Fetcher
    store = Store(config.DB_PATH)
    dept = store.get_dept(args.dept) if args.dept else None
    if not dept:
        host = urlparse(args.url).netloc
        for d in store.active_depts():          # URL 호스트 == 학과 list_url 호스트로 파서 선택
            if urlparse(d.get("list_url") or "").netloc == host:
                dept = d
                break
    store.close()
    if not dept:
        print(f"URL 호스트({urlparse(args.url).netloc})에 맞는 학과 못 찾음 → --dept 로 지정")
        sys.exit(1)
    print(f"[파서] dept={dept['dept_id']} content_selector={dept.get('content_selector')}")
    detail = Fetcher().fetch_content(dept, args.url)
    imgs = detail.get("images") or []
    print(f"[추출] 이미지 {len(imgs)}장: "
          + ", ".join(os.path.basename(urlparse(i["url"]).path) for i in imgs))
    if not imgs:
        print("이미지 0장 → content_selector 확인 필요")
        sys.exit(1)
    idx = min(args.img_index, len(imgs) - 1)
    print(f"[소스] {idx}번 이미지 사용(다른 장은 --img-index)")
    r = requests.get(imgs[idx]["url"], timeout=60, headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    return r.content


def to_data_url(im, quality=90):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    b = buf.getvalue()
    return f"data:image/jpeg;base64,{base64.b64encode(b).decode()}", len(b)


def build_variants(raw, sizes, tiles, gray):
    """(label, [PIL 이미지들]) 목록."""
    base = Image.open(io.BytesIO(raw))
    W, H = base.size
    out = [(f"원본_{W}x{H}", [base])]
    for px in sizes:
        im = base.copy()
        if max(im.size) > px:
            im.thumbnail((px, px))
        out.append((f"리사이즈_max{px}_{im.size[0]}x{im.size[1]}", [im]))
    for n in tiles:                              # 세로 n등분 = 수동 Pan-and-Scan
        step = H // n
        parts = [base.crop((0, i * step, W, (H if i == n - 1 else (i + 1) * step))) for i in range(n)]
        out.append((f"세로{n}타일", parts))
    if gray:
        out.append(("그레이_오토컨트라스트",
                    [ImageOps.autocontrast(base.convert("L")).convert("RGB")]))
    return out


def ask(base_url, model, prompt, images):
    content = [{"type": "text", "text": prompt}]
    kb = 0
    for im in images:
        du, n = to_data_url(im)
        kb += n // 1024
        content.append({"type": "image_url", "image_url": {"url": du}})
    body = {"model": model, "stream": False, "max_tokens": 512,
            "messages": [{"role": "user", "content": content}]}
    t0 = time.time()
    r = requests.post(f"{base_url}/chat/completions", json=body, timeout=300)
    dt = time.time() - t0
    j = r.json()
    ans = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
    pt = (j.get("usage") or {}).get("prompt_tokens")
    return ans, pt, kb, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--url")
    ap.add_argument("--dept")
    ap.add_argument("--img-index", type=int, default=0, dest="img_index")
    ap.add_argument("--sizes", default="512,768,1024,1536")
    ap.add_argument("--tiles", default="2,3")
    ap.add_argument("--gray", action="store_true")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--model")
    a = ap.parse_args()
    if not (a.image or a.url):
        ap.error("--image 또는 --url 필요")
    os.makedirs(OUT, exist_ok=True)
    model = resolve_model(a.model)
    print(f"[모델] {model} @ {config.LLM_BASE_URL}\n")
    raw = source_image(a)
    sizes = [int(x) for x in a.sizes.split(",") if x.strip()]
    tiles = [int(x) for x in a.tiles.split(",") if x.strip()]
    rows = []
    for label, imgs in build_variants(raw, sizes, tiles, a.gray):
        for k, im in enumerate(imgs):           # 변형 저장(눈으로 확인)
            im.convert("RGB").save(os.path.join(OUT, f"{label}_{k}.jpg"), "JPEG", quality=90)
        try:
            ans, pt, kb, dt = ask(config.LLM_BASE_URL, model, a.prompt, imgs)
        except Exception as e:
            print(f"### {label}\n  [오류] {e}\n")
            continue
        rows.append((label, len(imgs), kb, pt, dt))
        print(f"### {label}  (이미지 {len(imgs)}장 · {kb}KB · prompt_tokens={pt} · {dt:.1f}s)")
        for ln in (ans or "").splitlines():
            print(f"    {ln}")
        print()
    print("=== 요약(어떤 변형이 날짜를 맞혔는지 위 답변과 대조) ===")
    for label, n, kb, pt, dt in rows:
        print(f"  {label:26} img={n} {kb:5}KB ptok={pt} {dt:5.1f}s")
    print(f"\n생성 변형 이미지: {OUT}")


if __name__ == "__main__":
    main()
