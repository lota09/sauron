# -*- coding: utf-8 -*-
"""
experiments/vision_probe/multi_probe.py — 다중 이미지 '전송 전략' 비교(실측).

여러 장짜리 공지에서 3장을 '어떻게' 넣어야 요약이 가장 정확한지 서버에 붙여 비교한다:
  together : N장을 한 요청에 각각(현재 파이프라인 방식)
  vstack   : 세로로 이어붙인 1장(각 장 폭을 --px로 통일 후 위→아래)
  hstack   : 가로로 이어붙인 1장(각 장 높이를 --px로 통일 후 좌→우)
  each     : 한 장씩 개별 요청(장별 정확도 참고용, 답 N개)

실행(★ LLM 서버에 닿는 PC/기기에서, Pillow 필요):
  python experiments/vision_probe/multi_probe.py --images "u1,u2,u3"
  python experiments/vision_probe/multi_probe.py --url "<공지 URL>"
  ... --px 1024 --strategies together,vstack,hstack,each

각 전략의 [답변 · KB · prompt_tokens · 지연]을 출력 → 어느 방식이 날짜(예: 8.10)를 정확히 잡는지 대조.
생성 이미지는 out_multi/ 에 저장.
"""
import argparse
import io
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 같은 폴더 probe.py
import probe                                                     # noqa: E402
import config                                                    # noqa: E402
import requests                                                  # noqa: E402
from PIL import Image                                            # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_multi")


def collect(args):
    """--images(쉼표 URL) 또는 --url(공지에서 전량 추출) → [raw bytes]."""
    if args.images:
        return [probe._load_bytes(u.strip()) for u in args.images.split(",") if u.strip()]
    from db.store import Store
    from crawl.fetcher import Fetcher
    store = Store(config.DB_PATH)
    dept = store.get_dept(args.dept) if args.dept else None
    if not dept:
        host = urlparse(args.url).netloc
        for d in store.active_depts():
            if urlparse(d.get("list_url") or "").netloc == host:
                dept = d
                break
    store.close()
    if not dept:
        print("학과 못 찾음 → --dept 지정")
        sys.exit(1)
    imgs = (Fetcher().fetch_content(dept, args.url).get("images") or [])
    print(f"[추출] {len(imgs)}장: " + ", ".join(os.path.basename(urlparse(i['url']).path) for i in imgs))
    out = []
    for i in imgs:
        r = requests.get(i["url"], timeout=60, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        out.append(r.content)
    return out


def _open(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def vstack(ims, w):
    ims = [(i if i.width == w else i.resize((w, round(i.height * w / i.width)))) for i in ims]
    canvas = Image.new("RGB", (w, sum(i.height for i in ims)), "white")
    y = 0
    for i in ims:
        canvas.paste(i, (0, y)); y += i.height
    return canvas


def hstack(ims, h):
    ims = [(i if i.height == h else i.resize((round(i.width * h / i.height), h))) for i in ims]
    canvas = Image.new("RGB", (sum(i.width for i in ims), h), "white")
    x = 0
    for i in ims:
        canvas.paste(i, (x, 0)); x += i.width
    return canvas


def cap(im, px):
    if max(im.size) > px:
        im = im.copy(); im.thumbnail((px, px))
    return im


def _emit(model, prompt, label, images, tag):
    for k, im in enumerate(images):
        im.convert("RGB").save(os.path.join(OUT, f"{tag}_{k}.jpg"), "JPEG", quality=90)
    try:
        ans, pt, kb, dt = probe.ask(config.LLM_BASE_URL, model, prompt, images)
    except Exception as e:
        print(f"### {label}\n  [오류] {e}\n"); return
    print(f"### {label}  ({kb}KB · prompt_tokens={pt} · {dt:.1f}s)")
    for ln in (ans or "").splitlines():
        print(f"    {ln}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url"); ap.add_argument("--images"); ap.add_argument("--dept")
    ap.add_argument("--px", type=int, default=1024)
    ap.add_argument("--strategies", default="together,vstack,hstack,each")
    ap.add_argument("--prompt", default=probe.DEFAULT_PROMPT)
    ap.add_argument("--model")
    a = ap.parse_args()
    if not (a.url or a.images):
        ap.error("--url 또는 --images 필요")
    os.makedirs(OUT, exist_ok=True)
    model = probe.resolve_model(a.model)
    print(f"[모델] {model} @ {config.LLM_BASE_URL}\n")
    ims = [_open(b) for b in collect(a)]
    if not ims:
        print("이미지 없음"); sys.exit(1)
    print(f"[원본] {len(ims)}장: " + ", ".join(f"{i.width}x{i.height}" for i in ims) + "\n")
    for s in [x.strip() for x in a.strategies.split(",") if x.strip()]:
        if s == "together":
            _emit(model, a.prompt, f"together({len(ims)}장·각 max{a.px})", [cap(i, a.px) for i in ims], "together")
        elif s == "vstack":
            v = vstack(ims, a.px)
            _emit(model, a.prompt, f"vstack(1장 {v.width}x{v.height})", [v], "vstack")
        elif s == "hstack":
            h = hstack(ims, a.px)
            _emit(model, a.prompt, f"hstack(1장 {h.width}x{h.height})", [h], "hstack")
        elif s == "each":
            for k, im in enumerate(ims):
                _emit(model, a.prompt, f"each[{k}] ({im.width}x{im.height})", [cap(im, a.px)], f"each{k}")
    print(f"생성 이미지: {OUT}")


if __name__ == "__main__":
    main()
