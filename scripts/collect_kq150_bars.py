# -*- coding: utf-8 -*-
"""코스닥150 선물 1분봉 수집 — 레버리지 ETF 리밸런싱 연구용.

- 날짜마다 그날의 최근월물(3/6/9/12월, 만기 = 둘째 목요일)을 골라 정규장(09:00~15:45) 1분봉을 받는다.
- 오늘부터 과거로 내려가며 KIS가 어디까지 과거 분봉을 주는지 확인한다(연속 MAX_MISS 영업일 빈 응답이면 중단).
- 이미 받은 날짜는 건너뛰므로 매일 돌리면 하루씩 누적된다.

저장
  data/kq150/YYYYMMDD.csv   하루치 1분봉 (API 필드 원본 그대로 + code)
  data/kq150/_probe.json    날짜별 수신 결과, 실제 받아진 가장 과거 날짜, 사용한 종목코드

환경변수
  KQ_START   과거 탐색 시작 하한 (기본 20260101)
  KQ_MAX_MISS  연속 빈 응답 허용 영업일 수 (기본 8, 공휴일 감안)
"""
import os, re, csv, json, time, datetime as dt
import kis

CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
PRICE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
OUT_DIR = "data/kq150"
SESSION_OPEN, SESSION_CLOSE = "090000", "154500"
INTERVAL = "60"            # 1분. 15:30~15:45 구간이 15분뿐이라 5분봉(3개)으론 부족하다
MAX_PAGES = 6              # 1회 최대 ~102봉 → 정규장 405분은 4~5페이지
DKEY, TKEY = "stck_bsop_date", "stck_cntg_hour"
KQ_KIND = "3"              # 종목 마스터 상품종류: 3 = 코스닥150 선물 (2026.9.14 실측)
START = os.environ.get("KQ_START", "20260101")
MAX_MISS = int(os.environ.get("KQ_MAX_MISS", "8"))


def second_thursday(y, m):
    return [d for d in range(1, 22) if dt.date(y, m, d).weekday() == 3][1]


def front_quarter(day):
    """그날의 최근월물 (y, m). 만기일 당일까지는 해당 월물."""
    for y in (day.year, day.year + 1):
        for m in (3, 6, 9, 12):
            if day <= dt.date(y, m, second_thursday(y, m)):
                return y, m
    raise RuntimeError("월물 계산 실패")


_master_map = None


def master_codes():
    """마스터에서 코스닥150 선물 {YYYYMM: 단축코드}. 만기 지난 월물은 마스터에 없다."""
    global _master_map
    if _master_map is not None:
        return _master_map
    _master_map = {}
    try:
        import fo_master
        for r in fo_master.load():
            if r.get("종류") != KQ_KIND:
                continue
            m = re.search(r"(20\d{4})", r.get("종목명", ""))
            if m:
                _master_map.setdefault(m.group(1), r["단축코드"])
        print(f"[mst] 코스닥150 선물 {len(_master_map)}개 월물: "
              + ", ".join(f"{k}={v}" for k, v in sorted(_master_map.items())[:6]))
    except Exception as e:
        print(f"[mst] 마스터 실패 → 코드 패턴으로만 진행: {e}")
    return _master_map


# KRX 파생상품 연도 코드 (구 코드 체계). I·O·U 는 건너뛴다: 2024=V, 2025=W
YEAR_LETTER = {2019: "P", 2020: "Q", 2021: "R", 2022: "S", 2023: "T", 2024: "V", 2025: "W"}
MONTH_LETTER = {3: "3", 6: "6", 9: "9", 12: "C"}


def code_candidates(y, m):
    """한 월물의 후보 코드들. 2026년 마스터는 A06612 형식인데, 그 이전에 만기된 월물은
    구 코드 체계(106W12 / 106WC000)로만 조회될 수 있어서 둘 다 시도한다."""
    c = []
    mc = master_codes().get(f"{y}{m:02d}")
    if mc:
        c.append(mc)
    c.append(f"A06{y % 10}{m:02d}")
    L = YEAR_LETTER.get(y)
    if L:
        c += [f"106{L}{m:02d}", f"106{L}{MONTH_LETTER[m]}000"]
    return list(dict.fromkeys(c))


def next_quarter(y, m):
    return (y + 1, 3) if m == 12 else (y, m + 3)


def code_for(y, m):
    return code_candidates(y, m)[0]


def bars_for_day(code, day):
    """해당 일자의 정규장 1분봉. 응답에 그 날짜가 없으면 [] (KIS가 과거를 안 준다는 뜻)."""
    ymd = day.strftime("%Y%m%d")
    rows, hour, calls = [], SESSION_CLOSE, 0
    for _ in range(MAX_PAGES):
        b = kis.fetch(CHART_PATH, "FHKIF03020200", {
            "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code,
            "FID_HOUR_CLS_CODE": INTERVAL, "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N",
            "FID_INPUT_DATE_1": ymd, "FID_INPUT_HOUR_1": hour,
        })
        calls += 1
        page = b.get("output2") or []
        if isinstance(page, dict):
            page = [page]
        page = [r for r in page if r.get(TKEY) and r.get(DKEY) == ymd]
        if not page:
            break
        rows.extend(page)
        oldest = min(r[TKEY] for r in page)
        if oldest <= SESSION_OPEN:
            break
        hour = (dt.datetime.strptime(oldest, "%H%M%S") - dt.timedelta(minutes=1)).strftime("%H%M%S")
        time.sleep(0.12)
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: r[TKEY]):
        if r[TKEY] not in seen and SESSION_OPEN <= r[TKEY] <= SESSION_CLOSE:
            seen.add(r[TKEY]); uniq.append(r)
    return uniq, calls


def save(day, code, rows):
    path = f"{OUT_DIR}/{day:%Y%m%d}.csv"
    keys = ["code"] + sorted({k for r in rows for k in r})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({"code": code, **r})
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    probe_path = f"{OUT_DIR}/_probe.json"
    probe = json.load(open(probe_path)) if os.path.exists(probe_path) else {"days": {}}

    today = kis.now_kst().date()
    if kis.now_kst().strftime("%H%M") < "1550":      # 장중 실행이면 오늘은 미완성 → 어제부터
        today -= dt.timedelta(days=1)
    lo = dt.datetime.strptime(START, "%Y%m%d").date()

    day, miss, got, total_calls = today, 0, 0, 0
    while day >= lo and miss < MAX_MISS:
        if day.weekday() >= 5:
            day -= dt.timedelta(days=1); continue
        ymd = day.strftime("%Y%m%d")
        if os.path.exists(f"{OUT_DIR}/{ymd}.csv"):
            miss = 0; day -= dt.timedelta(days=1); continue
        y, m = front_quarter(day)
        rows, code, which, tried = [], code_for(y, m), "front", []
        # 최근월물 후보 → 안 되면 차근월물 후보 순으로. 어떤 코드가 먹혔는지 probe 에 남긴다.
        for tag, (yy, mm) in (("front", (y, m)), ("next", next_quarter(y, m))):
            for cand in code_candidates(yy, mm):
                try:
                    r_, calls = bars_for_day(cand, day)
                    total_calls += calls
                except Exception as e:
                    r_ = []
                    tried.append(f"{cand}:ERR {str(e)[:60]}")
                    continue
                tried.append(f"{cand}:{len(r_)}")
                if r_:
                    rows, code, which = r_, cand, tag
                    break
                time.sleep(0.12)
            if rows:
                break
        if rows:
            save(day, code, rows)
            last = rows[-1]
            probe["days"][ymd] = {"code": code, "contract": which, "bars": len(rows),
                                  "first": rows[0][TKEY], "last": last[TKEY]}
            print(f"[kq] {ymd} {code}({which}) {len(rows)}봉 {rows[0][TKEY]}~{last[TKEY]}")
            miss, got = 0, got + 1
        else:
            probe["days"][ymd] = {"code": None, "bars": 0, "tried": tried}
            print(f"[kq] {ymd} 빈 응답 — 시도 {', '.join(tried)}")
            miss += 1
        day -= dt.timedelta(days=1)
        time.sleep(0.12)

    served = sorted(k for k, v in probe["days"].items() if v.get("bars"))
    probe.update({
        "asof_kst": kis.now_kst().isoformat(timespec="seconds"),
        "earliest_served": served[0] if served else None,
        "latest_served": served[-1] if served else None,
        "n_days": len(served),
        "stopped_at": day.strftime("%Y%m%d"),
        "stop_reason": "start 하한" if day < lo else f"연속 {MAX_MISS}영업일 빈 응답",
    })
    json.dump(probe, open(probe_path, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[kq] 이번 실행 신규 {got}일 / calls={total_calls} / 누적 {len(served)}일 "
          f"({probe['earliest_served']} ~ {probe['latest_served']}) / 중단: {probe['stop_reason']}")


if __name__ == "__main__":
    main()
