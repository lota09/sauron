# -*- coding: utf-8 -*-
"""core/plugin.py — 플러그인 틀: 종류별 기반 클래스 + 이름으로 불러오기 + 비밀 주입.

플러그인 = plugins/<이름>.py 파일 하나. 그 안에 기반 클래스를 상속한 클래스가 딱 하나 있다.
코어는 이름(파일명)으로 불러 쓴다 — 코어에 플러그인 이름을 적는 곳은 없다.

  종류        기반 클래스         코어가 부르는 곳                    이름을 적는 곳
  ─────────  ─────────────────  ───────────────────────────────  ─────────────────────────────
  수집        SourcePlugin       crawl/fetcher.py                 depts_seed.csv 의 fetch_type
  (종류는 KINDS에 추가한다 — 예: 나중의 NotifierPlugin)

비밀: 클래스에 SECRETS = ("키", …)를 선언하면 코어가 secrets/plugin_<이름>.json 에서 읽어
      self.secrets 로 넣어 준다. 파일·키가 없거나 비어 있으면 생성 단계에서 PluginError(무엇이 빠졌는지).
실패: 플러그인은 그냥 예외를 던진다. 코어의 오류 처리(core/errors.py)가 '어느 파일 몇 번째 줄'까지 보고한다.
무거운 의존성은 메서드 안에서 import 할 것 — 플러그인 파일을 불러오는 것만으로 실패하면 안 된다.

  python -m core.plugin list                      # 설치된 플러그인과 종류
  python -m core.plugin missing-secrets [CSV]     # CSV가 쓰는 플러그인 중 비밀이 빠진 것(JSON) — deploy.py가 사용
"""
import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(ROOT, "plugins")
SECRETS_DIR = os.path.join(ROOT, "secrets")


class PluginError(Exception):
    pass


class Plugin:
    SECRETS = ()            # 필요한 비밀 키. 비어 있으면 비밀 파일을 읽지 않는다.
    name = None             # 로더가 채움(= 파일명)

    def __init__(self, secrets=None, config=None):
        self.secrets = secrets or {}
        self.config = config or {}      # 수집 플러그인: depts.fetch_config


class SourcePlugin(Plugin):
    """공지 수집. 두 단계(목록 → 상세) — 목록은 10분마다, 상세는 새 공지에만 불린다.
      list(page)  → [{"title", "url"}, …]   (+선택 "content": 목록에 본문이 있으면 상세 요청을 건너뜀)
                    url이 곧 공지의 정체성(이미 본 것 판별 키) → 매번 같은 값이 나와야 한다.
      detail(url) → {"content": 본문 HTML}  이미지 추출·정리는 코어가 한다.
    인스턴스는 프로세스 동안 유지된다(로그인 세션을 재사용하려면 self.session에 두면 됨)."""
    PAGINATED = True        # list(page)가 2, 3… 페이지를 지원하는가(시딩·재크롤 페이지 수에 쓰임)

    def __init__(self, secrets=None, config=None, session=None, timeout=None):
        super().__init__(secrets, config)
        self.session = session          # UA 등이 설정된 이 플러그인 전용 requests.Session
        self.timeout = timeout

    def list(self, page):
        raise NotImplementedError

    def detail(self, url):
        raise NotImplementedError


KINDS = {"source": SourcePlugin}


def plugin_path(name):
    return os.path.join(PLUGIN_DIR, f"{name}.py")


def secrets_path(name):
    return os.path.join(SECRETS_DIR, f"plugin_{name}.json")


def exists(name):
    return bool(name) and os.path.isfile(plugin_path(name))


def load_class(name, kind=None):
    """plugins/<name>.py 에서 플러그인 클래스를 찾는다. kind를 주면 그 종류인지 확인."""
    if not exists(name):
        raise PluginError(f"플러그인 없음: plugins/{name}.py")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    mod = importlib.import_module(f"plugins.{name}")
    found = [v for v in vars(mod).values()
             if isinstance(v, type) and issubclass(v, Plugin) and v not in KINDS.values()
             and v is not Plugin and v.__module__ == mod.__name__]
    if len(found) != 1:
        raise PluginError(f"plugins/{name}.py 에 플러그인 클래스가 {len(found)}개 — 딱 하나여야 함")
    cls = found[0]
    if kind and not issubclass(cls, KINDS[kind]):
        have = next((k for k, b in KINDS.items() if issubclass(cls, b)), "?")
        raise PluginError(f"plugins/{name}.py 는 '{have}' 플러그인 — 여기엔 '{kind}' 플러그인이 필요")
    cls.name = name
    return cls


def missing_secrets(name, cls=None):
    """비어 있거나 없는 비밀 키 목록."""
    cls = cls or load_class(name)
    if not cls.SECRETS:
        return []
    try:
        with open(secrets_path(name), encoding="utf-8") as f:
            have = json.load(f)
    except FileNotFoundError:
        return list(cls.SECRETS)
    except ValueError as e:
        raise PluginError(f"secrets/plugin_{name}.json JSON 오류: {e}")
    return [k for k in cls.SECRETS if not str(have.get(k) or "").strip()]


def load_secrets(name, cls):
    miss = missing_secrets(name, cls)
    if miss:
        raise PluginError(f"secrets/plugin_{name}.json 에 {', '.join(miss)} 필요 "
                          f"(값을 채우거나 `python3 deploy.py`로 입력)")
    if not cls.SECRETS:
        return {}
    with open(secrets_path(name), encoding="utf-8") as f:
        return json.load(f)


def create(name, kind, **kwargs):
    """이름 → 비밀까지 주입된 인스턴스."""
    cls = load_class(name, kind)
    return cls(secrets=load_secrets(name, cls), **kwargs)


def installed():
    """plugins/ 의 플러그인 → 종류(불러오기에 실패하면 '오류: …')."""
    out = {}
    if not os.path.isdir(PLUGIN_DIR):
        return out
    for fn in sorted(os.listdir(PLUGIN_DIR)):
        if fn.endswith(".py") and not fn.startswith("_"):
            n = fn[:-3]
            try:
                cls = load_class(n)
                out[n] = next(k for k, b in KINDS.items() if issubclass(cls, b))
            except Exception as e:
                out[n] = f"오류: {type(e).__name__}: {e}"
    return out


def _csv_plugins(csv_path):
    """CSV의 fetch_type 중 내장(html·json_api)이 아닌 것 = 수집 플러그인 이름."""
    import csv
    with open(csv_path, encoding="utf-8", newline="") as f:
        return sorted({(r.get("fetch_type") or "html").strip() for r in csv.DictReader(f)}
                      - {"html", "json_api", ""})


def _main(argv):
    if len(argv) >= 1 and argv[0] == "list":
        for n, k in installed().items():
            print(f"{n:<16} {k}")
        return 0
    if len(argv) >= 1 and argv[0] == "missing-secrets":
        names = set(_csv_plugins(argv[1] if len(argv) > 1 else os.path.join(ROOT, "init", "depts_seed.csv")))
        out = {}
        for n in sorted(names):
            if not exists(n):
                out[n] = {"error": f"plugins/{n}.py 없음"}
                continue
            miss = missing_secrets(n)
            if miss:
                out[n] = {"missing": miss, "file": os.path.relpath(secrets_path(n), ROOT)}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    # `python -m core.plugin` 으로 실행하면 이 파일이 __main__ 으로 한 번 더 로드되어 기반 클래스가
    # 두 벌이 된다(플러그인은 core.plugin 쪽을 상속). 정식 모듈의 함수로 실행한다.
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from core import plugin as _canonical
    sys.exit(_canonical._main(sys.argv[1:]))
