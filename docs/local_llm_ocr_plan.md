# 로컬 LLM + GPU OCR 요약 파이프라인 계획 (c1q)

작성: 2026-11-11 · 대상 기기: c1q (Galaxy Note20, SD865, RAM 7.7 GB, Debian 13)
측정 근거: `~/llm/llamacpp/RESULTS.md`, 원자료 `~/llm/llamacpp/exp/`

## 0. 결정 사항

1. **비전(멀티모달) 비활성화.** llama-server 에서 `--mmproj` 를 뺀다. 이미지가 든 공지는 OCR 텍스트로 요약한다.
   나중에 "모델이 도구처럼 필요할 때만 비전을 호출"하는 방식을 따로 검토한다(§7).
2. **OCR 은 GPU(Vulkan)로 돌린다.** 목적은 OCR 속도가 아니라 **OCR 중에도 CPU(큰 코어)를 LLM 에 비워 두는 것**.
   OCR 을 기다리지 않는다: 텍스트 공지는 바로 LLM 으로 보내고, 이미지 공지는 OCR 이 끝나는 즉시 LLM 큐에 들어간다.

## 1. 근거 요약 (실측)

| 항목 | 값 |
|---|---|
| LLM | Gemma 4 E4B Q4_0, llama.cpp CPU(dotprod 빌드), 큰 코어 cpu4–7 × 4스레드, 64k ctx, KV q8_0 |
| 단일 요청 tg | 6.9–7.4 t/s |
| 병렬 `-np 4 -kvu` | 동시 4건: 총 17.6 t/s, 건당 4.75 t/s (동시 2건은 총 7.7 로 이득 거의 없음, 동시 8건 18.6) |
| 비전(E4B mmproj) 판독 | 빽빽한 표 공지의 날짜를 이미지 토큰 상한 70–315 모두에서 지어냄 → **사용 불가** |
| OCR (RapidOCR: det PP-OCRv6 small + rec korean PP-OCRv5 mobile) | 표 공지 3장 날짜·시각·요일·전화·이메일 전부 정답 |
| OCR 속도 — CPU (MNN 4스레드) | 장당 3.3–3.6 s (글자 적은 장 0.7 s), RSS 475 MB |
| OCR 속도 — **GPU Vulkan (MNN)** | 장당 6.5–7.4 s (글자 적은 장 1.5 s), RSS 382 MB, 판독 결과 CPU 와 날짜 동일 |
| OCR — OpenCL (rusticl) | 동작 안 함(CL 오류) |

GPU 는 CPU 보다 2배 느리지만, LLM 이 쓰는 큰 코어를 덜 뺏는다는 점에서 채택한다. 이 전제는 §5-1 에서 반드시 검증한다.

## 2. 목표 동작

예: 텍스트 공지 3건 + 이미지 공지 1건(이미지 3장)이 한 번에 감지됨.

```
t=0   텍스트 A, B, C → LLM 큐 (llama-server 슬롯 4개 중 3개 즉시 사용)
t=0   이미지 공지 D → OCR 큐 → GPU OCR 시작 (3장 ≈ 15–20 s)
t≈18  D 의 OCR 텍스트 완성 → LLM 큐 → 남은 슬롯 1개로 바로 요약 시작
```

- LLM 동시 요청 상한 = llama-server 슬롯 수(4).
- OCR 은 GPU 하나를 쓰므로 직렬(동시 1개). OCR 큐가 LLM 을 막지 않는다.
- 이미지 공지라도 본문 텍스트가 충분하면, OCR 결과를 기다리는 동안 본문만으로 먼저 요약하는 안은 쓰지 않는다(요약 2번·디스코드 수정 2번 → 단순함 우선).

## 3. 구성 변경

### 3.1 llama-server (`~/.config/llama-server.env`, system 서비스 `llama-server`)

```
EXTRA_ARGS=--reasoning off -np 4 -kvu
```
- 비전 옵션(`--mmproj`, `--image-max-tokens`) 제거. MTP 는 끔(동시 요청에서 손해).
- `-kvu`: 64k KV 하나를 4 슬롯이 공유 → 메모리는 단일 슬롯과 같음. 긴 요청 하나가 컨텍스트를 많이 쓰면 다른 슬롯 여유가 줄어든다.
- 메모리 정책은 `INCIDENT-2026-11-11-OOM.md` 이후 설정 유지: mlock 없음, `MemoryHigh=6000M`, 하드 상한 없음, OOM 순위 300.

### 3.2 OCR 실행 환경 (`~/llm/ocr`)

- venv: `~/llm/ocr/.venv` (RapidOCR 3.10, MNN 3.6.2 를 Vulkan/OpenCL 포함으로 소스 빌드한 휠).
- 실행 조건:
  ```
  LD_LIBRARY_PATH=$HOME/llm/ocr/pydev/lib   # libvulkan.so 링크(개발 패키지 없이)
  BACKEND=VULKAN
  taskset -c 0-3                            # 작은 코어. 전처리·후처리 CPU 부분이 큰 코어를 건드리지 않게
  ```
- 참고 구현: `~/llm/ocr/ocr_mnn.py` (MNN 세션 백엔드 선택, 줄 재구성 로직 포함).
- **sauron venv 에 직접 설치한다(별도 상주 프로세스 불필요).** sauron venv 는 같은 Python 3.13 이고 pillow·requests 버전도 같아 충돌 없음.
  추가 패키지: `rapidocr`, `opencv-python-headless`, `numpy`, `omegaconf`, `pyclipper`, `shapely`, 그리고 로컬 휠
  `~/llm/ocr/MNN/pymnn/pip_package/dist/mnn_vulkan_opencl-3.6.2-cp313-cp313-linux_aarch64.whl` (onnxruntime 은 불필요).
- 호출은 `asyncio.to_thread(ocr_engine, image)` — MNN 추론 중 GIL 이 풀려 이벤트 루프를 막지 않는다.
- 서비스 유닛(sauron-crawler)에 `Environment=LD_LIBRARY_PATH=/home/n20/llm/ocr/pydev/lib` 추가 필요.
- 별도 프로세스와 비교한 손해: GPU 드라이버 오류로 세그폴트가 나면 크롤러 프로세스가 같이 죽는다(systemd 가 재시작). OCR 모델 ≈0.4 GB 가 크롤러에 상주(유휴 시 엔진 객체 해제로 회수 가능).

### 3.3 sauron 코드 변경

| 위치 | 변경 |
|---|---|
| `config.py` | `LLM_MAX_CONCURRENCY` 1 → **4**. `LLM_VISION` → **False**. 신규: `OCR_ENABLE=True`, `OCR_CMD`(OCR 워커 실행 명령), `OCR_TIMEOUT`(이미지 1장, 예: 60 s), `OCR_MIN_CHARS`(이보다 짧으면 "판독 실패"로 간주, 예: 20) |
| 신규 `summarize/ocr.py` | RapidOCR(MNN, `BACKEND=VULKAN`) 엔진을 지연 생성해 들고 있는 모듈. `ocr_images(data_urls) -> [text]`. 연속 실패 시 OCR 비활성 후 기존 경로(클라우드 비전)로 |
| `summarize/worker.py` | `summarize_one` 을 두 단계로 분리: ① 이미지 준비 + OCR(이미지 있을 때만) ② LLM 요약·DB·디스코드. 이미지 있는 공지는 OCR 큐로, 없는 공지는 바로 LLM 단계로 |
| `summarize/worker.py` `worker_loop` | 현재 워커 1개가 큐를 순차 처리 → **LLM 워커 `LLM_MAX_CONCURRENCY`개 + OCR 워커 1개**를 띄움. 큐 2개: `ocr_queue`, `llm_queue`. OCR 완료 시 결과(텍스트)를 notice 와 함께 `llm_queue` 에 넣음 |
| `summarize/llm.py` | 이미지 대신 OCR 텍스트를 프롬프트에 붙임: 본문 뒤에 `[첨부 이미지 1 OCR]\n...` 형식. `LLM_VISION_HINT` 대신 "OCR 텍스트는 줄·칸이 섞였을 수 있으니 날짜·대상·신청방법을 우선 추출" 류의 지시 |
| 폴백 | OCR 결과가 `OCR_MIN_CHARS` 미만(디자인 포스터 등) 또는 OCR 실패 → 그 공지는 클라우드 비전(gemini-flash-lite)으로 기존 방식 요약 |
| DB/로그 | `summary_engine` 에 `local:ocr×N` 표기. 로그: `[OCR] id=… N장 · s · 글자수`, `[OCR 폴백] 사유` |

### 3.4 타임아웃 재검토

- 동시 4건이면 건당 tg ≈ 4.75 t/s, 입력 처리(pp)도 슬롯 간 공유된다.
- OCR 텍스트가 붙은 공지(본문 + OCR ≈ 2–3천 자 ≈ 1–1.5k 토큰)는 혼잡 시 첫 토큰까지 60 s(`LLM_TIMEOUT`)를 넘을 수 있다.
- §5-2 측정 후 `LLM_TIMEOUT`/`LLM_WALL_TIMEOUT` 을 조정한다(예상: 120 s / 240 s).

## 4. OCR 실행 설계 (sauron 프로세스 내부)

- 엔진 1개, OCR 동시 1건(OCR 워커 코루틴 1개). 모델 로드 0.6 s, RSS ≈ 380 MB.
- CPU 부분(전·후처리)이 큰 코어를 쓰지 않게 OCR 스레드를 cpu0–3 에 고정: `os.sched_setaffinity(0, {0,1,2,3})` 를 OCR 스레드 안에서 호출(스레드 단위로 적용됨).
- 이미지 입력은 sauron 의 `to_data_url` 결과(긴 변 1024 px JPEG)를 그대로 사용. 2배 확대는 이득 없음(실측).

### OCR 병렬화는 하지 않는다 (실측, 2026-11-11, llama-server 유휴)

| 구성 (cpu0–3) | 처리량 (페이지/분) |
|---|---|
| Vulkan × 1 프로세스 | 7.5 |
| Vulkan × 2 | 10.9 |
| Vulkan × 3 | 13.7 |
| CPU 4스레드 × 1 | 4.9 |
| CPU 2 프로세스 | 5.6 |

(프로세스 시작·모델 로드·첫 실행 준비 포함, 3장 × 2회 기준)

- Vulkan 은 병렬 시 1.8배까지 늘지만(GPU 가 놀고 작은 코어 쪽 CPU 처리가 병목), 프로세스마다 ≈0.4 GB 가 든다.
- 전체 처리량의 병목은 LLM 이다: 공지 1건 요약 ≈ 200–300 토큰, 동시 4건 총 17.6 t/s → 분당 약 3–4건.
  OCR 1개(7.5 페이지/분 ≈ 이미지 3장 공지 2.5건/분)로 LLM 을 따라간다. 공지 유입은 시간당 수 건 수준.
- 따라서 OCR 병렬은 메모리만 쓰고 체감 이득이 없다. 이미지 공지가 한꺼번에 몰리는 경우가 잦으면 그때 2개로.

## 5. 검증 계획 (구현 전·후)

1. **GPU OCR 이 정말 LLM 을 덜 방해하는지 (채택 전제, 최우선)**
   - llama-server 에 동시 4건 요청을 넣고 총 처리량을 잰다. 세 조건 비교:
     (a) OCR 없음 (b) OCR CPU, cpu0–3 (c) OCR Vulkan, cpu0–3
   - GPU 와 CPU 는 같은 메모리 대역폭을 쓰므로 (c) 에서도 tg 가 떨어질 수 있다. (c) 가 (b) 보다 나쁘면 OCR 은 CPU(작은 코어)로 전환.
2. **혼잡 시 첫 토큰 지연**: 위 (c) 조건에서 OCR 텍스트가 붙은 요청의 첫 토큰 시간 → 타임아웃 값 결정.
3. **판독 품질 회귀**: 최근 이미지 공지 20건 이상을 OCR → 요약하고 날짜·대상·신청방법을 원문과 대조. 디자인 포스터 비율과 폴백 빈도 확인.
4. **메모리·발열**: 위 부하를 10분 이상 유지하며 `systemctl show llama-server -p MemoryCurrent -p MemoryPeak`, `free -m`, 스왑 사용량, 최고 온도(`thermal_zone*`) 기록. SSH 응답 유지 확인.

통과 기준(초안): (c) 총 처리량이 (a) 대비 −15% 이내, 날짜 오판독 0건, 스왑 증가 < 500 MB, SSH 무중단.

### 5-1 결과 (2026-11-11, 실서비스 llama-server 8080 `-np 4 -kvu`, 동시 4건 × 200토큰, 조건마다 50 °C까지 식힌 뒤 2회)

| 조건 | 총 t/s (1회 / 2회) | (a) 대비 | 같은 시간 OCR 장수 | LLM 손실 토큰/OCR 1장 |
|---|---|---|---|---|
| (a) OCR 없음 | 19.8 / 18.8 | — | — | — |
| (b) OCR CPU, cpu0–3 | 17.2 / 16.6 | −12.5 % | 7장 / 51 s | ≈ 17 |
| (c) OCR Vulkan, cpu0–3 | 16.8 / 16.3 | −14.5 % | 12장 / 50 s | ≈ 11 |

- 순간 처리량은 (b)≈(c)(차이 2 %, 측정 잡음 수준). 그러나 (c)는 같은 시간에 1.7배 많은 장을 읽어 **장당 LLM 방해가 약 1/3 적고 OCR도 빨리 끝난다** → Vulkan 유지.
- 기준(−15 % 이내)은 (c)가 간신히 통과. 매 회 끝 온도 77–80 °C(스로틀 경계) — 처리량 일부는 발열 영향.
- LLM 부하 중에는 CPU OCR 이 장당 ≈7 s 로, 유휴 시 실측(3.3–3.6 s)보다 2배 느리다(작은 코어도 대역폭·발열 공유).

### 5-2 ~ 5-4 결과 (2026-11-11, 최근 이미지 포함 공지 20건, 실제 경로: 내려받기 → Vulkan OCR 직렬 → 로컬 LLM 동시 4, DB·디스코드 쓰기 없음)

| 항목 | 1차 (timeout 60 / wall 120) | 2차 (150 / 300 + 신뢰도 게이트) |
|---|---|---|
| 총 소요 | 923 s | **404 s** |
| 벽시계 절단 / 읽기 시간초과 / 재시도 | 44 / 6 / 14 | **0 / 0 / 0** |
| 첫 토큰 최대 | 118 s | **38 s** |
| 클라우드 폴백 | 4건(로컬 포기) | 1건(OCR 판독 실패 포스터 → 클라우드 비전, 의도대로) |
| OCR (Vulkan) | 공지당 2.3–11.9 s | 동일 |

- 1차가 느렸던 주원인은 시간초과 → 재시도가 부하를 더 쌓는 악순환. 2차에선 건당 39–118 s.
- 1차에선 벽시계 절단된 요약이 첫 줄만 남은 채 '성공'으로 저장됐다(예: 강의평가 공지의 날짜 누락). 2차에선 전부 완결.
- **판독 품질(5-3)**: 로컬 요약 19건의 날짜 37개를 본문+OCR 원문과 대조 → **원문에 없는 날짜 0건**.
- **OCR 글자수만으로는 디자인 포스터를 못 거른다**: 깨진 글자 182자가 `OCR_MIN_CHARS`(20)를 통과해 엉터리 요약("모집 분야는 체험자레 2 + IP4WN/X")이 나왔다.
  RapidOCR 박스 신뢰도(글자수 가중)로 분리됨: 정상 0.88–0.98, 포스터 0.71 → `OCR_MIN_SCORE=0.80` 추가(미만이면 로컬 생략).
- **메모리·발열(5-4)**, 2차 7분 부하: llama-server 최대 4.8 GB(MemoryPeak 5.34 GB, 재시작 0), MemAvailable 최저 1.2 GB,
  스왑 930 → 1000 MB(+70 MB), 최고 85 °C(스로틀 구간), SSH 무중단.

## 9. 구현 메모 (계획과 달라진 점)

- `LD_LIBRARY_PATH`: 서비스 유닛 대신 `sauron.sh`가 `.venv/vklib`(libvulkan.so 심볼릭 링크)를 넣는다(sudo 불필요).
  sauron.sh 밖에서 직접 실행하는 경로(`python main.py …`, 재요약 스크립트)는 `core/vkpath.ensure()`가 자신을 한 번 재실행해 맞춘다.
  못 찾으면 MNN 이 조용히 CPU로 내려가므로 첫 OCR 뒤 실제 백엔드를 로그로 확인한다.
- `OCR_CMD` 는 없앴다(프로세스 내부 실행). 신규 키: `OCR_BACKEND`, `OCR_CPUS`, `OCR_MIN_SCORE`, `OCR_BOX_MIN_SCORE`, `OCR_MAX_CHARS`,
  `OCR_MAX_CONSEC_FAIL`, `OCR_IDLE_RELEASE_SEC`, `LLM_OCR_HINT`.
- `summary_engine` 표기: `ocr:<모델>×N`(로컬+OCR), `cloud:vision:<모델>×N`(폴백 비전). OCR 텍스트는 DB `ocr_text` 에 저장.
- `LLM_TIMEOUT` 150 / `LLM_WALL_TIMEOUT` 300 (§3.4 예상 120/240 보다 크게 — 실측 기준).
- 의존성: `requirements-ocr.txt` + Vulkan MNN 휠(`deploy.py` [2b], `MNN_WHEEL` 경로).

## 6. 메모리 예산 (RAM 7.7 GB)

| 항목 | 크기 |
|---|---|
| llama-server (모델 mmap + 64k KV + 버퍼) | 5.3–6.0 GB (`MemoryHigh=6000M`) |
| OCR 워커 (Vulkan) | ≈ 0.4 GB |
| sauron 3개 서비스 | ≈ 0.15 GB |
| 시스템·SSH | ≈ 0.5 GB |
| VS Code / Claude 세션 (사용 시) | 0.5–1.2 GB |

여유가 거의 없다. VS Code 를 함께 쓰는 동안 부족하면 llama-server 가 자기 상자 안에서 느려진다(설계상 의도). OCR 워커는 필요 시 유휴 종료로 0.4 GB 를 돌려준다.

## 7. 나중 과제: 비전을 "도구"로

- OCR 이 비었거나(포스터), 요약 모델이 "이미지 확인 필요"를 판단했을 때만 비전을 호출하는 구조.
- 로컬 E4B 비전은 글자 판독이 불가 수준이므로, 도구 호출 대상은 클라우드 비전 또는 별도 소형 VLM 실측 후 결정.
- llama-server 에 상시 `--mmproj` 를 올리지 않는다(0.7 GB, 이미지 1장에 1–13분).

## 8. 운영 메모

- 서비스: `sudo systemctl {start|stop|restart} llama-server`, 상태 `systemctl show llama-server -p MemoryCurrent -p MemoryPeak -p NRestarts`.
- 설정: `~/.config/llama-server.env` (수정 후 restart).
- API: `http://localhost:8080/v1` (모델명 `gemma-4-e4b`), 슬롯 상태 `GET /slots`.
- GPU OCR 은 Mesa Turnip(25.0) 기준. Mesa 업데이트 시 §5-1 재측정.
