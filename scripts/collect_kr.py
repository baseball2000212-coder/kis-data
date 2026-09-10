# -*- coding: utf-8 -*-
"""국장 마감 데이터 수집 — K200 선물 분봉/현재가 + 옵션 전광판(그릭스 포함).

선물옵션 분봉조회   FHKIF03020200  FID_HOUR_CLS_CODE = 초 단위 간격(60=1분, 300=5분)
                                   FID_INPUT_HOUR_1 을 앞으로 옮겨가며 과거 방향 페이징
선물옵션 현재가     FHMIF10000000
국내옵션 전광판 콜풋 FHPIF05030100  행사가별 델타·감마·베가·세타·IV·미결제약정
"""
import os, json, string, datetime as dt
import kis

CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
PRICE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
BOARD_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-callput"

CACHE = "data/kr/_futcode.json"
SESSION_OPEN, SESSION_CLOSE = "090000", "154500"   # K200 선물 정규장
INTERVAL = "60"        # 60=1분
MAX_PAGES = 6
DKEY, TKEY = "stck_bsop_date", "stck_cntg_hour"


def second_thursday(y, m):
    """해당 월의 둘째 목요일(=선물옵션 만기일) 일자."""
    return [d for d in range(1, 22) if dt.date(y, m, d).weekday() == 3][1]


def front_future_month(today):
    """K200 선물 최근월물(3/6/9/12월). 만기일이 지났으면 다음 분기."""
    for y in (today.year, today.year + 1):
        for q in (3, 6, 9, 12):
            if today <= dt.date(y, q, second_thursday(y, q)):
                return y, q
    raise RuntimeError("최근월물 계산 실패")


def option_expiry_month(today):
    """옵션 최근월물 YYYYMM. 이번 달 만기일이 지났으면 다음 달."""
    y, m = today.year, today.month
    if today.day > second_thursday(y, m):
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return f"{y}{m:02d}"


def resolve_future_code(y, q):
    """선물 종목코드 '101' + 연도문자 1자 + 월 2자리 (예: 101W09).
    연도문자 매핑이 공개돼 있지 않아 한 번만 탐색하고 캐시한다."""
    env = os.environ.get("KR_FUT_CODE", "").strip()
    if env:
        return env
    if os.path.exists(CACHE):
        c = json.load(open(CACHE))
        if c.get("year") == y and c.get("quarter") == q:
            return c["code"]
    for ch in string.ascii_uppercase:
        code = f"101{ch}{q:02d}"
        try:
            b = kis.fetch(PRICE_PATH, "FHMIF10000000",
                          {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code})
        except Exception:
            continue
        if (b.get("output1") or {}).get("futs_prpr"):
            os.makedirs("data/kr", exist_ok=True)
            json.dump({"year": y, "quarter": q, "code": code}, open(CACHE, "w"))
            print(f"[fut] 종목코드 확정: {code}")
            return code
    raise RuntimeError(f"{y}년 {q}월물 선물 종목코드를 찾지 못했다")


def futures_intraday(code, day, interval=INTERVAL, max_pages=MAX_PAGES):
    """FID_INPUT_HOUR_1 을 과거로 옮기며 정규장 전체를 긁는다."""
    head, rows, hour, calls = None, [], SESSION_CLOSE, 0
    for _ in range(max_pages):
        b = kis.fetch(CHART_PATH, "FHKIF03020200", {
            "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code,
            "FID_HOUR_CLS_CODE": interval, "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N",
            "FID_INPUT_DATE_1": day, "FID_INPUT_HOUR_1": hour,
        })
        calls += 1
        if head is None:
            head = b.get("output1")
        page = b.get("output2") or []
        if isinstance(page, dict):
            page = [page]
        page = [r for r in page if r.get(TKEY)]
        if not page:
            break
        rows.extend(page)
        oldest = min(r[TKEY] for r in page)
        if oldest <= SESSION_OPEN:
            break
        t = dt.datetime.strptime(oldest, "%H%M%S") - dt.timedelta(minutes=1)
        hour = t.strftime("%H%M%S")

    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: (r.get(DKEY, ""), r[TKEY])):
        k = (r.get(DKEY, ""), r[TKEY])
        if k not in seen:
            seen.add(k); uniq.append(r)
    reg = [r for r in uniq if SESSION_OPEN <= r[TKEY] <= SESSION_CLOSE]
    return head, reg, calls, len(uniq)


def main():
    today = kis.now_kst().date()
    y, q = front_future_month(today)
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"), "session": "kr",
           "interval_sec": INTERVAL, "errors": []}

    try:
        code = resolve_future_code(y, q)
    except Exception as e:
        out["errors"].append(f"선물 종목코드: {e}")
        print(f"[fut] 종목코드 실패: {e}")
        kis.save("kr", out)
        return

    exp = option_expiry_month(today)
    out["future_code"], out["option_expiry"] = code, exp

    try:
        b = kis.fetch(PRICE_PATH, "FHMIF10000000",
                      {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code})
        out["future_price"] = {k: b.get(k) for k in ("output1", "output2", "output3")}
        o1 = b.get("output1") or {}
        print(f"[fut] {code} 현재가 {o1.get('futs_prpr')} "
              f"베이시스 {o1.get('basis')} 미결제 {o1.get('hts_otst_stpl_qty')}")
    except Exception as e:
        out["errors"].append(f"선물 현재가: {e}")
        print(f"[fut] 현재가 실패: {e}")

    try:
        head, reg, calls, total = futures_intraday(code, today.strftime("%Y%m%d"))
        out["future_chart"] = {"summary": head, "bars": reg}
        rng = f"{reg[0][TKEY]}~{reg[-1][TKEY]}" if reg else "-"
        print(f"[fut] 분봉 정규장 {len(reg)}봉 ({rng})  수집 {total}건 / calls={calls}")
    except Exception as e:
        out["errors"].append(f"선물 분봉: {e}")
        print(f"[fut] 분봉 실패: {e}")

    try:
        b = kis.fetch(BOARD_PATH, "FHPIF05030100", {
            "FID_COND_MRKT_DIV_CODE": "O", "FID_COND_SCR_DIV_CODE": "20503",
            "FID_MRKT_CLS_CODE": "CO", "FID_MTRT_CNT": exp,
            "FID_MRKT_CLS_CODE1": "PO", "FID_COND_MRKT_CLS_CODE": "",
        })
        call, put = b.get("output1") or [], b.get("output2") or []
        out["option_board"] = {"call": call, "put": put}
        print(f"[opt] {exp} 콜 {len(call)}행 / 풋 {len(put)}행")
    except Exception as e:
        out["errors"].append(f"옵션 전광판: {e}")
        print(f"[opt] 전광판 실패: {e}")

    if out["errors"]:
        print(f"-- 실패 {len(out['errors'])}건")
    kis.save("kr", out)


if __name__ == "__main__":
    main()
