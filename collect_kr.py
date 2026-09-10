# -*- coding: utf-8 -*-
"""국장 마감 데이터 수집 — K200 선물 분봉/현재가 + 옵션 전광판(그릭스 포함).

선물옵션 분봉조회   FHKIF03020200  /uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice
  -> 베이시스, KOSPI200 지수, 미결제약정, 이론가, 괴리율 포함
선물옵션 현재가     FHMIF10000000  /uapi/domestic-futureoption/v1/quotations/inquire-price
국내옵션 전광판 콜풋 FHPIF05030100  /uapi/domestic-futureoption/v1/quotations/display-board-callput
  -> 행사가별 델타/감마/베가/세타/로우/내재변동성/미결제약정  (GEX 계산 가능)
"""
import os, json, string, datetime as dt
import kis

CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
PRICE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
BOARD_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-callput"

CACHE = "data/kr/_futcode.json"


def second_thursday(y, m):
    """해당 월의 둘째 목요일(=선물옵션 만기일) 일자."""
    return [d for d in range(1, 22) if dt.date(y, m, d).weekday() == 3][1]


def front_future_month(today):
    """K200 선물 최근월물(3/6/9/12월). 만기일이 지났으면 다음 분기."""
    for y in (today.year, today.year + 1):
        for q in (3, 6, 9, 12):
            last = dt.date(y, q, second_thursday(y, q))
            if today <= last:
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
    """선물 종목코드는 '101' + 연도문자 1자 + 월 2자리 (예: 101W09).
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
        b, err = kis.try_fetch(PRICE_PATH, "FHMIF10000000",
                               {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code})
        if b and (b.get("output1") or {}).get("futs_prpr"):
            os.makedirs("data/kr", exist_ok=True)
            json.dump({"year": y, "quarter": q, "code": code}, open(CACHE, "w"))
            print(f"[fut] 종목코드 확정: {code}")
            return code
    raise RuntimeError(f"{y}년 {q}월물 선물 종목코드를 찾지 못했다")


def main():
    today = kis.now_kst().date()
    y, q = front_future_month(today)
    code = resolve_future_code(y, q)
    exp = option_expiry_month(today)

    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"), "session": "kr",
           "future_code": code, "option_expiry": exp, "errors": []}

    b, err = kis.try_fetch(PRICE_PATH, "FHMIF10000000",
                           {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code},
                           label="선물 현재가")
    out["future_price"] = {k: b.get(k) for k in ("output1", "output2", "output3")} if b else None
    if err: out["errors"].append(err)

    b, err = kis.try_fetch(CHART_PATH, "FHKIF03020200", {
        "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code,
        "FID_HOUR_CLS_CODE": "60",            # 60초 = 1분봉
        "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N",
        "FID_INPUT_DATE_1": today.strftime("%Y%m%d"),
        "FID_INPUT_HOUR_1": "154500",         # 정규장 마감시각 기준 역순 조회
    }, label="선물 분봉")
    if b:
        out["future_chart"] = {"summary": b.get("output1"), "bars": b.get("output2")}
        print(f"[fut] 분봉 {len(b.get('output2') or [])}개")
    if err: out["errors"].append(err)

    b, err = kis.try_fetch(BOARD_PATH, "FHPIF05030100", {
        "FID_COND_MRKT_DIV_CODE": "O", "FID_COND_SCR_DIV_CODE": "20503",
        "FID_MRKT_CLS_CODE": "CO", "FID_MTRT_CNT": exp,
        "FID_MRKT_CLS_CODE1": "PO", "FID_COND_MRKT_CLS_CODE": "",
    }, label="옵션 전광판")
    if b:
        out["option_board"] = {"call": b.get("output1"), "put": b.get("output2")}
        print(f"[opt] {exp} 콜 {len(b.get('output1') or [])} / 풋 {len(b.get('output2') or [])}")
    if err: out["errors"].append(err)

    kis.save("kr", out)


if __name__ == "__main__":
    main()
