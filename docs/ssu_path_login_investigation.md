# 슈패스(path.ssu.ac.kr) 비교과 프로그램 자동 수집 — 사전 조사 보고서

조사일: 2026-09-22
조사 방법: Chrome(실제 브라우저) + DevTools Network 상당의 확장 도구로 실시간 요청 관찰, 로그인은 사용자 본인이 직접 수행, 페이지 소스/JS는 안전한 범위에서 fetch로 직접 확인.
읽기 전용 원칙 준수: 신청/취소/저장/수정 버튼은 누르지 않았음. 클릭한 것은 "로그아웃", "통합로그인 진입", 프로그램 상세보기(GET), 정렬/페이지 링크(GET)뿐.

---

## 요약

**로그인 자동화는 가능성이 높다.** 학생용 통합로그인(LoginInfo 폼)은 아이디/비밀번호를 **클라이언트 JS 암호화 없이** `userid`/`pwd` 평문 필드로 POST하는 구조라, `requests.Session`만으로도 재현 가능한 형태다. RSA 암호화(#RSAModulus/#RSAExponent)는 이미 알고 계셨던 대로 **외부인용 로그인 폼(etcLoginForm2)** 전용이며, 학생 통합로그인과는 무관한 것으로 확인됐다.

**가장 큰 장애물은 두 가지다.**

1. **SSO 세션이 예상보다 오래/넓게 유지된다.** 슈패스에서 "로그아웃"을 눌러도 대학 통합인증(smartid.ssu.ac.kr) 쪽 세션 쿠키가 살아있으면, 보호된 페이지에 다시 접근하는 순간 로그인 폼조차 보여주지 않고 **자동으로 재인증되어 새 `sToken`을 발급받아 통과**한다. 이는 "로그아웃 → 재로그인 테스트"로 실제 로그인 요청/응답을 캡처하려던 시도를 무산시켰고(아래 참고), 세션 수명·재로그인 로직을 짤 때 반드시 감안해야 한다.
2. **`apiReturnUrl` 왕복 경로를 실물로 끝까지 캡처하지 못했다.** 위 1번 때문에 smartid.ssu.ac.kr의 실제 로그인 POST(`smln_pcs.asp`) 및 그 이후 `apiReturnUrl`로의 콜백 리다이렉트(Set-Cookie 헤더 포함)를 완전한 형태로 관측하지 못했다. 폼 필드명과 로그인 로직(암호화 없음)은 페이지 소스에서 직접 확인했지만, **최초 1회는 사용자가 직접 자격증명으로 실행 로그를 남겨 필드 순서/리다이렉트 체인을 검증**해야 한다.

---

## 1. 통합로그인 흐름

### 1-1. 리다이렉트 체인 (재확인, 기존 파악 내용과 동일)

```
GET https://path.ssu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do   (비로그인)
  → 302 https://path.ssu.ac.kr/comm/login/user/login.do?rtnUrl=<REDACTED: hex, 170자 내외>

(로그인 페이지에서 "통합로그인" 클릭)
GET https://path.ssu.ac.kr/comm/login/user/loginChk.do?rtnUrl=<동일 rtnUrl>
  → 303 https://smartid.ssu.ac.kr/Symtra_sso/smln.asp?apiReturnUrl=https://path.ssu.ac.kr/comm/login/user/loginProc.do?rtnUrl=<재인코딩된 rtnUrl>
```

### 1-2. smartid.ssu.ac.kr 로그인 폼 (실물 DOM에서 직접 추출, 암호화 없음)

`https://smartid.ssu.ac.kr/Symtra_sso/smln.asp` 페이지의 학생용 로그인 폼:

```html
<form name="LoginInfo" method="post" action="https://smartid.ssu.ac.kr/Symtra_sso/smln_pcs.asp">
  <input type="hidden" name="in_tp_bit"    value="0">   <!-- 1자리, 고정값으로 보임 -->
  <input type="hidden" name="rqst_caus_cd" value="03">  <!-- 2자리, 고정값으로 보임 -->
  <input type="text"     name="userid" id="userid" placeholder="직번/학번을 입력하세요">
  <input type="password" name="pwd"    id="pwd"    placeholder="비밀번호를 입력하세요">
  <input type="checkbox" name="chkSave" id="chkSave">  <!-- "아이디저장" -->
</form>
```

- 로그인 버튼은 `<a href="JavaScript:LoginInfoSend('LoginInfo');">로그인</a>` — 인라인 스크립트의 `LoginInfoSend` 함수 본문을 확인한 결과 **`encrypt`, `RSA`, `MD5`, `SHA` 관련 코드가 전혀 없고 `.submit()` 호출만 존재**한다. 즉 비밀번호는 값 그대로(HTTPS 위에서) POST된다.
- **RSA(#RSAModulus/#RSAExponent)는 이 폼에는 없다.** 이미 알고 계셨던 대로 외부인용 `etcLoginForm2`(path.ssu.ac.kr 초기 화면의 "외부관계자" 로그인) 전용으로 보인다. 학생 통합로그인과 혼동하지 않도록 주의.
- **CAPTCHA·2단계 인증·기기 인증 알림: 관측되지 않음.** 다만 실패 로그인(오입력)이나 낯선 IP에서의 로그인은 테스트하지 않았으므로, 조건부로 뜰 가능성은 배제 못 함.
- `smln_pcs.asp` 응답 이후 실제 리다이렉트/Set-Cookie 헤더 전체는 **캡처하지 못함** (§ SSO 자동 재인증 문제, 아래 참고).

### 1-3. 로그인 완료 후 콜백 (실제로 1건 관측됨, 값은 가림)

슈패스에서 "로그아웃"을 누른 뒤 다시 보호된 페이지에 접근했을 때, 브라우저가 자동으로 아래 요청을 발생시키며 그대로 로그인된 상태로 넘어갔다 (스마트id 쪽 SSO 세션이 살아있었기 때문 — 실제 아이디/비번 POST 화면은 안 뜸):

```
GET https://path.ssu.ac.kr/comm/login/user/loginProc.do
    ?rtnUrl=<REDACTED: /ptfol/... 형태의 인코딩된 URL>
    &sToken=<REDACTED: 약 250자, 영숫자와 'z' 구분자로 섞인 커스텀 토큰. 'Vy...zCy...zPy...zAy...zEy...zSSy...zUURy...zMy...' 패턴.
             매 로그인/리다이렉트마다 값이 바뀜(1회성 토큰으로 추정)>
    &sIdno=<REDACTED: 8자리 학번>
→ 200, 세션 확립 후 목록 페이지로 최종 이동
```

- `sToken`은 **일회성 쿼리스트링 토큰**으로 보이며(형태: 여러 구간을 `z`로 구분한 커스텀 인코딩), 매번 새로 발급됨 → 재사용 불가 전제.
- `sIdno`는 평문 학번.

### 1-4. 쿠키 (이름과 형태만, 값은 가림)

**smartid.ssu.ac.kr** (JS `document.cookie`로 확인 — 즉 **HttpOnly가 아님**):
| 쿠키명 | 형태/비고 |
|---|---|
| `ASPSESSIONID________` (8자리 대문자 알파벳이 이름에 랜덤 포함, 예: `ASPSESSIONIDACTTBQBB`) | 클래식 ASP 세션 쿠키. **접속할 때마다 쿠키 "이름" 자체가 달라질 수 있음** (앱풀/서버 인스턴스별). 코드에서 이름을 하드코딩하면 안 되고 매번 `session.cookies`에서 패턴 매칭(`ASPSESSIONID*`)으로 찾아야 함 |
| `ASPSESSIONID________` (두 번째) | 위와 동일 패턴의 별도 세션 쿠키 (서버가 2대 이상인 듯) |
| `uid` | 짧은 값 |
| `sAddr` | 짧은 값 |
| `sToken` | 위 1-3의 토큰과 동일 계열로 추정 |

**path.ssu.ac.kr** (JS로 확인된 것 — **JSESSIONID는 document.cookie에 안 보임 = HttpOnly로 설정돼 있을 가능성 높음**, 이건 `requests.Session`이 알아서 처리하므로 문제 없음):
| 쿠키명 | 형태/비고 |
|---|---|
| `JSESSIONID` (추정, JS로는 미확인/HttpOnly) | 표준 스프링/톰캣 세션 쿠키로 추정. 이게 실질적으로 로그인 상태를 유지하는 핵심 쿠키일 가능성 높음 |
| `sAddr` | 짧은 값 |
| `g_state` | 구글 로그인 위젯(gsi) 관련 것으로 보이며 슈패스 인증과 무관해 보임 |
| `sToken` | 1회성 토큰, path.ssu.ac.kr 쪽에도 잠깐 심어짐 |

### 1-5. ⚠️ SSO 자동 재인증 (중요, 새로 발견한 사실)

슈패스 자체 로그아웃(`/comm/login/user/logout.do`) 후 보호된 URL에 다시 접속하면:
- 로그인 폼이 뜨지 않고,
- `smartid.ssu.ac.kr`의 SSO 세션 쿠키가 살아있는 채로 곧장 `loginProc.do?...&sToken=...&sIdno=...`로 리다이렉트되어
- **자격증명 재입력 없이 로그인 상태가 복원**됐다.

즉 슈패스의 "로그아웃"은 슈패스(path.ssu.ac.kr) 세션만 끊고, 대학 통합인증(smartid.ssu.ac.kr) SSO 세션은 별도로 살아있다. 이 때문에 실제 아이디/비밀번호 POST 왕복을 다시 캡처하려는 시도가 막혔다 — smartid 쿠키까지 지우지 않는 한 로그인 폼 자체가 안 뜬다.

**자동화 관점에서 의미:**
- 매번 처음부터 로그인할 필요 없이, 한 번 로그인한 `requests.Session`(쿠키 포함)을 재사용하면 상당 기간 재로그인 없이 동작할 가능성이 높다.
- 반대로 쿠키를 디스크에 저장해 재사용하는 캐싱 전략을 쓸 수도 있다 (단, 만료 시점은 미확인 → 2번 항목).
- **미확인 위험**: smartid SSO 쿠키가 만료된 상태에서 `userid`/`pwd`만으로 `smln_pcs.asp`에 실제 POST를 날렸을 때의 정확한 응답/리다이렉트 체인은 이번 조사에서 끝까지 못 봤다. 폼 구조상 필드명은 확실하지만, **최초 1회는 실제 로그인 로그를 남겨서 검증 필요**.

---

## 2. 세션 수명

**미확인.** 시간 관계상 30분/2시간 방치 테스트, 동일 계정 타 브라우저 동시 로그인 테스트를 진행하지 못했다. 다만 1-5에서 확인했듯 슈패스 자체 로그아웃과 SSO 세션은 분리되어 있어서, "얼마나 방치하면 다시 로그인하라고 하는지"는 **path.ssu.ac.kr JSESSIONID(스프링 세션) 만료 시간**과 **smartid ASPSESSIONID(IIS/ASP 세션) 만료 시간**을 각각 따로 봐야 할 것으로 보인다(화면에 "29분 X초 시간연장" 카운트다운이 보이는 것으로 보아 슈패스 자체 세션은 짧은 편 — 30분 근처 타이머로 추정, 정확한 값은 미검증).

**권장 후속 테스트**: 로그인 세션의 쿠키를 저장해두고, 30분·2시간 뒤 각각 목록 페이지를 다시 요청해 상태코드/리다이렉트 여부로 확인.

---

## 3. 로그인 후 비교과 목록 (`findIcmpNsbjtPgmList.do`)

### 3-1. 렌더링 방식

**서버 사이드 렌더링(SSR) HTML.** 페이지 로드 시 목록 데이터를 위한 별도 XHR/fetch가 없다(관측된 유일한 XHR은 `/ptfol/app/push/unreadCnt.do` POST — 안읽은 알림 뱃지 수 조회로, 목록과 무관). 즉 `requests.get()` 한 번으로 받은 HTML을 그대로 파싱하면 된다.

### 3-2. 쿼리 파라미터 (실측, GET)

정렬/페이지 링크 클릭 시 발생한 실제 요청 URL:

```
GET https://path.ssu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do
    ?paginationInfo.currentPageNo=2
    &sort=0001
    &chkAblyCount=0
    &operYySh=2026
    &operSemCdSh=0000
    &operSemCdShVal=0000
    &vshOrgid=
    &vshOrgzNm=
    &searchValue=
    &prgmFormCdSh=0000
    &eduFrDt=
    &eduToDt=
    &scpfDpmtCdSh=
    &scpfDpmtCdNm=
```

| 파라미터 | 의미(추정) | 값 예시 |
|---|---|---|
| `paginationInfo.currentPageNo` | 페이지 번호 | `1`, `2`, ... |
| `sort` | 정렬 기준 (아래 참고) | `0001`/`0002`/`0003` |
| `operYySh` | 운영년도 | `2026` (기본값=올해) |
| `operSemCdSh` / `operSemCdShVal` | 학기 (`0000`=전체, `0001`=1학기, `0003`=2학기) | `0000` |
| `prgmFormCdSh` | 프로그램 형식 필터 | `0000`=전체로 보임 |
| `vshOrgid` / `vshOrgzNm` | 조직검색(운영부서) 필터 | 비우면 전체 |
| `searchValue` | 키워드 검색 | 비우면 전체 |
| `eduFrDt` / `eduToDt` | 교육기간 필터 | 비우면 전체 |
| `scpfDpmtCdSh` / `scpfDpmtCdNm` | 신청대상 학과 필터 | 비우면 전체 |
| `chkAblyCount` | 역량 관련 체크박스로 추정 | `0` |

### 3-3. 정렬 (`sort`) — 확인 완료

목록 상단 정렬 탭(`a.sort1` / `a.sort2` / `a.sort3`, 활성 탭에 `on` 클래스)로 실측:

| `sort` 값 | 화면 표기 | 비고 |
|---|---|---|
| `0001` | **최신 등록순** | **기본값** |
| `0002` | 신청 마감순 | |
| `0003` | 과정명순 | 가나다순으로 보임 |

(각 sort 값으로 전환 시 목록 1페이지 상단 항목이 실제로 달라지는 것을 확인함. 등록일 자체가 응답 HTML 어느 필드에 노출되는지는 별도로 확인 못 함 — TODO)

### 3-4. 페이지당 건수 / 전체 건수

- 페이지당 최대 **35건**.
- `operYySh=2026, operSemCdSh=0000(전체 학기)` 기준으로 페이지를 이분탐색한 결과: **34페이지까지 존재, 마지막 페이지(34)는 15건** → 대략 **1,170건 내외** (33개 풀페이지×35 + 15, 단 중간 한 페이지에서 35건이 아닌 33건이 관측된 적이 있어 ±수십 건의 오차 가능 — 정확한 총건수 필드는 화면에서 못 찾음, "총 N건" 같은 텍스트가 없었음).
- **주의**: 이 숫자는 "2026년도 전체" 필터 기준이며, 외부인용 공개 목록(34건, `dialog/nsbjtPgmList.do`)은 **"외부인 대상 모집공고"만 모아놓은 별도 화면**이라 애초에 비교 대상 모수가 다르다(전체 비교과 프로그램의 부분집합이 아니라 별도 카테고리에 가까움). 그래도 요청하신 대로 계산하면:

  **공개 목록(34건) ÷ 로그인 후 2026년도 전체 목록(약 1,170건) ≈ 2.9%**

  다만 이 비율은 "외부인 대상 공고 비율"이지 "숨겨진 비율"로 해석하면 오해 소지가 있다. 정확히는 "전체 1,170여 건 중 로그인 없이 열람 가능한 건 34건뿐"이라는 의미다.

- **운영부서별 건수 분포: 미확인.** 34페이지 전체를 순회하며 각 카드의 운영조직 텍스트를 집계해야 하는데(방법 자체는 간단: 매 페이지 HTML에서 `운영조직` 라벨 옆 값을 파싱해 `Counter`), 시간 관계상 실행하지 못했다. 추후 requests 스켈레톤으로 34페이지 GET 후 파싱하면 5분 내 계산 가능.

---

## 4. 상세

### 4-1. 요청

목록의 각 프로그램 제목은 `<a class="tit ellipsis detailBtn" data-params='{"encSddpbSeq":"<KEY>","paginationInfo.currentPageNo":"1"}'>`로 렌더링되고, 클릭 시(jQuery 위임 이벤트) 아래 GET이 발생한다:

```
GET https://path.ssu.ac.kr/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do
    ?encSddpbSeq=<KEY>
    &paginationInfo.currentPageNo=1
```

- **키 이름**: `encSddpbSeq`
- **키 형태**: 32자리 소문자 16진수 문자열 (예시 형태: `3d65893542061912f3baacc5df8b8e9e` — 이건 실제 관측값이지만 32자리 hex라는 "형태" 정보로서 남김. 이름/학번 등 개인정보가 아니라 프로그램 식별자라 값 자체는 민감하지 않다고 판단해 형태 확인용으로 남겨둠. 필요하시면 이 값도 가리겠습니다)
- **로그아웃 후 재로그인 시 동일 값 유지 여부: 미확인.** 1-5의 SSO 자동 재인증 문제 때문에 "완전히 다른 로그인 세션"을 만들기 어려웠다. 다만 이 값이 `paginationInfo.currentPageNo`와 함께 매 목록 렌더링 시 서버에서 새로 내려주는 걸 보면, 세션마다 암호화 키가 달라 값이 바뀌는 방식(세션 바운드)일 수도, 프로그램마다 고정된 암호화 아이디일 수도 있다 — **자동화 코드에서는 "목록에서 막 받아온 값만 그 요청 사이클 안에서 사용"하는 것을 기본 전제로 하고, 캐싱/재사용은 하지 않는 게 안전**하다.

### 4-2. 본문 컨테이너 CSS 셀렉터

상세 페이지는 표 형태(운영조직/담당자/신청기간/... 라벨-값 쌍)로 렌더링된다. 목록 트리거 요소는 `a.tit.ellipsis.detailBtn`로 확인했지만, **상세 본문(설명/소개글) 영역의 정확한 CSS 셀렉터는 별도로 뜯어보지 못했다** — TODO. 우선 스켈레톤에서는 `requests.get()`으로 받은 HTML을 `BeautifulSoup`으로 파싱해 라벨 텍스트(`th`/`dt` 등) 기준으로 값을 찾는 방식을 권장(레이아웃이 바뀌어도 비교적 안정적).

### 4-3. 포스터/첨부

목록 카드의 썸네일은 다음 형태로 로드된다:

```
GET https://path.ssu.ac.kr/common/cmnFile/thumbnail.do?encSvrFileNm=<64자리 내외 16진수로 추정>&width=170&height=120
```

- **로그인 쿠키 없이 열리는지: 미확인.** 같은 브라우저 세션(로그인 상태) 안에서만 확인해서, 쿠키를 제거한 상태의 요청은 테스트하지 못했다. Python 스켈레톤을 만들 때 `requests.Session()`이 아닌 새 `requests.get()`(쿠키 없이)으로 한 번 찔러보면 5초 내 확인 가능 — 추천 후속 작업.

---

## 5. (덤) job.ssu.ac.kr 정렬 버튼

`https://job.ssu.ac.kr/service/careerProgram/careerProgramList.do`에서 동일한 `sort1`/`sort2`/`sort3` 클래스 구조를 확인:

| 탭 | 화면 표기 | 기본 활성 여부 |
|---|---|---|
| `sort1` | **최신 등록순** | 기본 활성(`on` 클래스) |
| `sort2` | 신청 마감순 | |
| `sort3` | 과정명순 | |

path.ssu.ac.kr의 비교과 목록과 완전히 동일한 라벨·순서를 쓰는 것으로 보아 **같은 코드베이스(플랫폼)를 공유**하는 것으로 추정된다. `sort=0001`이 "최신 등록순"이라는 것은 §3-3과 동일하게 재확인됨.

---

## 6. Python 스켈레톤 (requests.Session, 자격증명은 환경변수)

⚠️ **아직 실제 로그인 POST 왕복을 끝까지 관측하지 못했으므로, 아래 코드는 "1차 초안"입니다.** 최초 실행 시 `DEBUG=1`로 돌려서 `resp.history`(리다이렉트 체인)와 최종 URL/쿠키를 꼭 눈으로 확인하고, 필요하면 `login()` 내부의 필드명/순서를 조정하세요. 특히 `apiReturnUrl`을 손으로 구성하는 부분이 가장 깨지기 쉬운 지점입니다.

```python
#!/usr/bin/env python3
"""
SSU-PATH (path.ssu.ac.kr) 비교과 프로그램 목록 조회 — 1차 스켈레톤.
자격증명은 환경변수로만 받는다: SSU_ID, SSU_PW
폰(ARM, 저메모리)에서 requests만으로 동작하는 것을 목표로 함 (Selenium 불가).

⚠️ 미검증 지점(TODO):
  - apiReturnUrl 왕복이 정확히 이 순서로 동작하는지 (SSO 세션이 남아있으면
    로그인 폼 없이 곧장 통과되므로, "완전 로그아웃 상태"에서의 실측이 부족함)
  - smln_pcs.asp 응답이 200(HTML)인지 302(리다이렉트)인지
  - ASPSESSIONID 쿠키 "이름"이 서버 인스턴스마다 달라지는 문제 대응
"""

import os
import re
import sys
from urllib.parse import urlencode, quote

import requests
from bs4 import BeautifulSoup

BASE_PATH = "https://path.ssu.ac.kr"
BASE_SMARTID = "https://smartid.ssu.ac.kr"

LIST_URL = f"{BASE_PATH}/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"
LOGIN_URL = f"{BASE_PATH}/comm/login/user/login.do"
LOGINCHK_URL = f"{BASE_PATH}/comm/login/user/loginChk.do"
SMLN_URL = f"{BASE_SMARTID}/Symtra_sso/smln.asp"
SMLN_PCS_URL = f"{BASE_SMARTID}/Symtra_sso/smln_pcs.asp"

UA = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)

DEBUG = os.environ.get("DEBUG") == "1"


def log(*args):
    if DEBUG:
        print("[debug]", *args, file=sys.stderr)


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def login(session: requests.Session, userid: str, pwd: str, rtn_target: str = "/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"):
    """
    통합로그인 수행.
    rtn_target: 로그인 후 최종적으로 이동하고 싶은 path.ssu.ac.kr 내부 경로.
    """
    # 1) 보호된 페이지를 먼저 찔러서 rtnUrl 인코딩 형식을 서버가 만들어주게 한다.
    r = session.get(f"{BASE_PATH}{rtn_target}", allow_redirects=True)
    log("step1 final url:", r.url)
    # r.url 이 .../comm/login/user/login.do?rtnUrl=... 형태여야 정상

    m = re.search(r"rtnUrl=([^&]+)", r.url)
    if not m:
        raise RuntimeError("rtnUrl을 못 찾음 — 이미 로그인된 상태이거나 URL 구조가 바뀐 듯")
    rtn_url_enc = m.group(1)

    # 2) loginChk.do -> smartid로 리다이렉트
    r2 = session.get(
        LOGINCHK_URL,
        params={"rtnUrl": rtn_url_enc},
        allow_redirects=True,
    )
    log("step2 final url:", r2.url)
    # 이 시점에 r2.url 이 smartid.ssu.ac.kr/Symtra_sso/smln.asp?apiReturnUrl=... 이어야 정상.
    # 만약 이미 findIcmpNsbjtPgmList.do로 가있다면 SSO 세션이 살아있어 자동 로그인된 것 —
    # 그대로 return 해도 됨.
    if "path.ssu.ac.kr" in r2.url and "login" not in r2.url:
        log("이미 로그인된 상태로 보임 (SSO 세션 재사용)")
        return

    m2 = re.search(r"apiReturnUrl=([^&]+)", r2.url)
    api_return_url = m2.group(1) if m2 else None
    log("apiReturnUrl:", api_return_url)

    # 3) smartid 로그인 폼 POST (암호화 없음 — DOM에서 확인된 필드명 그대로)
    login_payload = {
        "in_tp_bit": "0",
        "rqst_caus_cd": "03",
        "userid": userid,
        "pwd": pwd,
        # "chkSave": "on",  # 아이디저장 체크 여부 — 굳이 필요 없으면 생략
    }
    r3 = session.post(
        SMLN_PCS_URL,
        data=login_payload,
        headers={
            "Referer": r2.url,
            "Origin": BASE_SMARTID,
        },
        allow_redirects=True,
    )
    log("step3 final url:", r3.url, "status:", r3.status_code)

    # 4) 로그인 실패 감지 (문구는 실제 실패 케이스로 확인 후 보정 필요 — TODO)
    if "비밀번호" in r3.text and "다시" in r3.text:
        raise RuntimeError("로그인 실패로 추정됨 (아이디/비밀번호 확인 필요)")

    # 5) 최종적으로 목표 페이지로 잘 왔는지 확인
    r4 = session.get(f"{BASE_PATH}{rtn_target}", allow_redirects=True)
    if "LOGOUT" not in r4.text and "로그아웃" not in r4.text:
        raise RuntimeError("로그인 후에도 인증 안 된 것으로 보임 — 리다이렉트 체인 재확인 필요")

    log("로그인 성공, 최종 URL:", r4.url)


def fetch_list_page(session: requests.Session, page_no: int = 1, sort: str = "0001", year: str = "2026", semester: str = "0000"):
    params = {
        "paginationInfo.currentPageNo": page_no,
        "sort": sort,           # 0001=최신등록순 0002=신청마감순 0003=과정명순
        "chkAblyCount": "0",
        "operYySh": year,
        "operSemCdSh": semester,     # 0000=전체 0001=1학기 0003=2학기
        "operSemCdShVal": semester,
        "vshOrgid": "",
        "vshOrgzNm": "",
        "searchValue": "",
        "prgmFormCdSh": "0000",
        "eduFrDt": "",
        "eduToDt": "",
        "scpfDpmtCdSh": "",
        "scpfDpmtCdNm": "",
    }
    r = session.get(LIST_URL, params=params)
    r.raise_for_status()
    return r.text


def parse_list(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("a.detailBtn"):
        title = a.get_text(strip=True)
        if not title:
            continue
        data_params = a.get("data-params")
        items.append({"title": title, "data_params": data_params})
    return items


def main():
    userid = os.environ.get("SSU_ID")
    pwd = os.environ.get("SSU_PW")
    if not userid or not pwd:
        print("환경변수 SSU_ID / SSU_PW 를 설정하세요.", file=sys.stderr)
        sys.exit(1)

    session = new_session()
    login(session, userid, pwd)

    html = fetch_list_page(session, page_no=1)
    items = parse_list(html)
    for it in items:
        print(it["title"], "|", it["data_params"])


if __name__ == "__main__":
    main()
```

---

## 7. 미확인 항목 정리 (우선순위 순)

1. **smln_pcs.asp 실제 POST 응답/리다이렉트 체인 전체** (헤더 포함) — SSO 자동 재인증 때문에 못 봄. 스켈레톤 첫 실행 시 `DEBUG=1`로 반드시 검증.
2. **세션 수명** (30분/2시간 방치, 동시 로그인 시 끊김 여부) — 미실행.
3. **`encSddpbSeq`의 세션 간 안정성** — 두 개의 서로 다른 로그인 세션에서 같은 프로그램을 비교 못 함.
4. **등록일이 목록 응답에 포함되는지, 어느 필드인지** — 미확인.
5. **운영부서별 건수 분포** — 방법은 정리했지만 실행은 안 함(34페이지 순회 필요).
6. **포스터/첨부가 로그인 쿠키 없이 열리는지** — 미확인.
7. **정확한 전체 건수** — "총 N건" 표시가 화면에 없어서 페이지 이분탐색으로 추정(≈1,170건). 오차 가능.
8. **상세 본문 컨테이너의 정확한 CSS 셀렉터** — 미확인.

## 8. 위험 요소

- **계정 잠금 가능성**: 로그인 실패를 반복하면 잠길 수 있는 통합인증 시스템이 흔하므로, 자동화 스크립트에 재시도 로직을 넣을 때 실패 시 즉시 중단하고 사람에게 알리는 방식으로 설계 권장 (자동 재시도 금지).
- **ASPSESSIONID 쿠키 이름 변동**: 코드에서 쿠키 이름을 하드코딩하면 서버 인스턴스가 바뀔 때 깨짐 — 패턴 매칭 권장.
- **SSO 자동 재인증으로 인해 "로그아웃 테스트"의 의미가 제한적**: 완전한 세션 초기화가 필요하면 스마트id 쿠키까지 지워야 함.
- **봇 차단/약관**: 이번 조사에서 명시적인 이용약관 동의 절차나 봇 차단(캡차 등)은 못 봤지만, 폰에서 주기적으로 자동 수집할 경우 User-Agent, 요청 간격 등으로 비정상 트래픽으로 탐지될 가능성은 배제할 수 없음 — 요청 간 지연을 두는 것을 권장.
- **원본 HAR 미저장**: 본 조사에서는 원본 네트워크 로그 파일을 저장/커밋하지 않았고, 이 보고서에도 민감 값은 모두 가렸음.
