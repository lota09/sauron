# vision_probe — 비전 판독력 실험

같은 포스터를 여러 전처리(해상도·타일·대비)로 실제 LLM 서버에 넣어, **어떤 형태여야 2B가 작은 글자(날짜 등)를 읽는지** 경험적으로 찾는 도구. 오프라인 유닛테스트(`tests/`)와 별개로, **서버에 닿는 PC/기기에서** 돌린다.

## 왜 필요한가
`군e러닝` 요약이 8/10을 놓친 원인은 이미지 미입력이 아니라(비전은 작동함, `추가학기`가 증거) **작은 글자 판독 실패**. 다운스케일은 오히려 글자를 뭉갠다. 그래서 판독력을 높이는 후보를 실측한다:
- **세로 타일링(수동 Pan-and-Scan)** — 긴 포스터를 N등분해 각 조각을 개별 이미지로 전송 → 조각당 인코더 해상도가 유지돼 글자가 커짐. (가장 유력)
- 원본/여러 해상도 비교 — 어느 크기부터 읽히는지.
- 그레이+오토컨트라스트 — 저대비 포스터 보정.

## 사용
```bash
pip install pillow requests

# 로컬 이미지로
python experiments/vision_probe/probe.py --image poster.png

# 공지 URL에서 이미지 자동 추출(스캐치 등 html 사이트)
python experiments/vision_probe/probe.py --url "https://scatch.ssu.ac.kr/공지사항/?...&slug=..."
python experiments/vision_probe/probe.py --url "..." --dept scatch_haksa   # 파서 학과 강제

# 변형 세트 조정
python experiments/vision_probe/probe.py --image a.png --sizes 768,1536,2048 --tiles 2,3,4 --gray
```
옵션: `--sizes`(최대변 px 목록) · `--tiles`(세로 N등분 세트) · `--gray`(대비강화) · `--img-index`(URL에서 몇 번째 이미지) · `--prompt` · `--model`.

## 읽는 법
각 변형마다 `이미지수 · 페이로드KB · prompt_tokens · 지연 · 모델답변`이 찍힌다. **답변에 실제 날짜(예: 8.10)가 정확히 나오는 변형**이 승자. 생성된 변형 이미지는 `out/` 에 저장되니 직접 열어 글자 크기를 확인.

## 다음
타일링이 이기면 → 그 로직을 `summarize/vision.py`(전처리)와 `summarize/worker.py`(첨부 구성)에 정식 옵션으로 넣는다(예: `LLM_VISION_TILE=n`). 실측 전엔 파이프라인 본체는 안 건드림.
