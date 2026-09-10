# kis-data — 카드뉴스용 시장데이터 수집

카드뉴스 세션이 도는 컨테이너는 조직 egress 정책 때문에 KIS·KRX·네이버금융 등으로
직접 못 나간다(전부 403). 게다가 KIS는 실전 `:9443` / 모의 `:29443`으로 **비 443 포트**라
프록시가 원천 미지원이다. 반면 `raw.githubusercontent.com`은 열려 있다.

그래서 **수집은 GitHub Actions 러너가 하고(egress 제한 없음), 카드 세션은 결과 JSON만 읽는다.**

```
Actions (15:50·16:05 / 22:00·22:20 UTC)  →  KIS API 호출  →  data/*.json 커밋
                                                                    ↓
카드뉴스 세션                          raw.githubusercontent.com 에서 1회 읽기
```

## 세팅 (한 번만)

1. **KIS 앱키 발급** — KIS Developers에서 실전투자 앱키/앱시크릿 발급.
   모의투자(vps)도 되지만 호출 제한이 낮고 시세가 제한되는 API가 있어 실전 권장.
2. 이 레포를 본인 GitHub에 push.
3. `Settings → Secrets and variables → Actions`
   - **Secrets**: `KIS_APP_KEY`, `KIS_APP_SECRET`
   - **Variables**(선택): `KR_FUT_CODE` — 비워두면 스크립트가 알아서 찾아 캐시한다.
4. Actions 탭에서 `collect-kr` / `collect-us` 를 **Run workflow** 로 한 번 수동 실행 →
   로그에서 어떤 심볼이 잡혔는지 확인.

## 수집 항목

### `data/kr/latest.json` — 국장
| 필드 | API | 내용 |
|---|---|---|
| `future_price` | `FHMIF10000000` | K200 선물 현재가·호가 |
| `future_chart` | `FHKIF03020200` | **1분봉** + 베이시스·KOSPI200지수·미결제약정·이론가·괴리율 |
| `option_board` | `FHPIF05030100` | 콜/풋 전광판 — 행사가별 **델타·감마·베가·세타·로우·IV·미결제약정** |

`option_board` 만으로 GEX가 그대로 계산된다 (Σ 감마 × OI × 승수 × S²/100 부호처리).

### `data/us/latest.json` — 미장
| 필드 | API | 내용 |
|---|---|---|
| `indices` | `FHKST03030200` | 지수 분봉, **`FID_HOUR_CLS_CODE=0` → 미국 정규장만** |
| `stocks` | `HHDFS76950200` | QQQ·NVDA·SPY 5분봉 |

지수 심볼은 `SPX`만 문서에 확정돼 있어서, 나스닥·다우·VIX는 후보를 순서대로
시도하고 성공한 것을 기록한다. 첫 실행 로그의 `[idx] NASDAQ <- XXXX` 줄에서
확정된 심볼을 보고 `INDEX_CANDIDATES` 를 그 값 하나로 줄이면 호출이 줄어든다.

## 카드 세션에서 읽는 법

```
https://raw.githubusercontent.com/<계정>/kis-data/main/data/kr/latest.json
https://raw.githubusercontent.com/<계정>/kis-data/main/data/us/latest.json
```

날짜 고정본은 `data/kr/20260909.json` 형태로 같이 쌓인다.

## 주의

- 실전 계좌 유량제한은 초당 20건. 지금 스크립트는 실행당 5~10건이라 여유롭다.
- 접근토큰은 24시간 유효 + 1분 1회 발급 제한 → 워크플로 실행당 1회만 발급한다.
- 어떤 항목이 실패해도 파이프라인은 계속 가고, 실패 사유는 JSON의 `errors` 배열에 남는다.
  카드 세션은 값이 없으면 그 카드를 빼거나 `—` 로 처리하면 된다.
