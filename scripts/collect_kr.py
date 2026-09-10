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
FUTBOARD_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
INV_TIME_PATH  = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
INV_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
PROG_PATH      = "/uapi/domestic-stock/v1/quotations/investor-program-trade-today"

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


def list_futures():
    """선물 전광판(FHPIF05030200)으로 상장 선물 목록을 받는다. 코드 추측이 필요 없다."""
    b = kis.fetch(FUTBOARD_PATH, "FHPIF05030200", {
        "FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
        "FID_COND_MRKT_CLS_CODE": "MKI",
    })
    rows = b.get("output1") or b.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in rows if r.get("futs_shrn_iscd")]


def resolve_future_code(y, q):
    """1) 환경변수 2) 캐시 3) 전광판 목록 4) 코드 브루트포스 순으로 확정."""
    env = os.environ.get("KR_FUT_CODE", "").strip()
    if env:
        return env
    if os.path.exists(CACHE):
        c = json.load(open(CACHE))
        if c.get("year") == y and c.get("quarter") == q:
            return c["code"]

    # 3) 전광판에서 잔존일수가 가장 짧은(=최근월물) 종목을 고른다
    try:
        rows = list_futures()
        for r in rows[:12]:
            print(f"      {r.get('futs_shrn_iscd')}  {r.get('hts_kor_isnm')}  "
                  f"잔존 {r.get('hts_rmnn_dynu')}  현재가 {r.get('futs_prpr')}")
        cand = [r for r in rows if str(r.get("hts_rmnn_dynu") or "").strip().isdigit()]
        cand.sort(key=lambda r: int(r["hts_rmnn_dynu"]))
        if cand:
            code = cand[0]["futs_shrn_iscd"]
            os.makedirs("data/kr", exist_ok=True)
            json.dump({"year": y, "quarter": q, "code": code}, open(CACHE, "w"))
            print(f"[fut] 전광판에서 최근월물 확정: {code} ({cand[0].get('hts_kor_isnm')})")
            return code
        print("[fut] 전광판 응답에 종목이 없다")
    except Exception as e:
        print(f"[fut] 전광판 실패: {e}")

    # 4) 마지막 수단 — 코드 브루트포스. 실패 사유를 처음 한 번은 보여준다.
    first_err = None
    for ch in string.ascii_uppercase:
        code = f"101{ch}{q:02d}"
        try:
            b = kis.fetch(PRICE_PATH, "FHMIF10000000",
                          {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code})
        except Exception as e:
            if first_err is None:
                first_err = f"{code}: {e}"
            continue
        if (b.get("output1") or {}).get("futs_prpr"):
            os.makedirs("data/kr", exist_ok=True)
            json.dump({"year": y, "quarter": q, "code": code}, open(CACHE, "w"))
            print(f"[fut] 브루트포스로 확정: {code}")
            return code
    raise RuntimeError(f"{y}년 {q}월물 코드를 찾지 못했다. 첫 실패 사유 → {first_err}")


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
    # 야간 세션 탓에 전일 데이터가 섞여 온다. 가장 최근 영업일만 남긴다.
    day_max = max((r.get(DKEY, "") for r in uniq), default="")
    reg = [r for r in uniq
           if r.get(DKEY, "") == day_max and SESSION_OPEN <= r[TKEY] <= SESSION_CLOSE]
    return head, reg, calls, len(uniq)


def main():
    today = kis.now_kst().date()
    y, q = front_future_month(today)
    out = {"asof_kst": kis.now_kst().isoformat(timespec="seconds"), "session": "kr",
           "interval_sec": INTERVAL, "errors": []}

    try:
        print("[fut] 선물 종목 탐색")
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
        try:
            ds = derivatives_summary((out.get("future_price") or {}).get("output1"), call, put)
            out["derivatives"] = ds
            f = ds["future"]
            print(f"[drv] 베이시스 {f['basis']} 괴리 {f['disparity']} "
                  f"미결제 {f['open_interest']:,.0f}({f['oi_change']:+,.0f}) "
                  f"{f.get('position_read','')}")
            print(f"[drv] P/C OI {ds.get('put_call_oi_ratio')} · "
                  f"최대OI 콜 {ds['call']['max_oi_strike']} / 풋 {ds['put']['max_oi_strike']} · "
                  f"ATM IV 콜 {ds['call']['atm_iv']} 풋 {ds['put']['atm_iv']}")
        except Exception as e:
            out["errors"].append(f"파생 요약: {e}")
            print(f"[drv] 요약 실패: {e}")
    except Exception as e:
        out["errors"].append(f"옵션 전광판: {e}")
        print(f"[opt] 전광판 실패: {e}")

    day = today.strftime("%Y%m%d")
    for label, fn in (("수급 시간대별", lambda: investor_time("999", "S001")),
                      ("수급 일별", lambda: investor_daily(day)),
                      ("프로그램 코스피", lambda: program_trade("1")),
                      ("프로그램 코스닥", lambda: program_trade("2"))):
        key = {"수급 시간대별": "investor_time", "수급 일별": "investor_daily",
               "프로그램 코스피": "program_kospi", "프로그램 코스닥": "program_kosdaq"}[label]
        try:
            rows = fn()
            out[key] = rows
            print(f"[flow] {label} {len(rows)}행")
        except Exception as e:
            out["errors"].append(f"{label}: {e}")
            print(f"[flow] {label} 실패: {str(e)[:80]}")

    if out["errors"]:
        print(f"-- 실패 {len(out['errors'])}건")
    kis.save("kr", out)


def investor_time(iscd, iscd2):
    """시장별 투자자매매동향(시간대별) — HTS [0403] 상단표. 개인·외국인·기관 매도/매수/순매수."""
    b = kis.fetch(INV_TIME_PATH, "FHPTJ04030000",
                  {"FID_INPUT_ISCD": iscd, "FID_INPUT_ISCD_2": iscd2})
    rows = b.get("output1") or b.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def investor_daily(day, iscd="0001", iscd1="KSP"):
    """시장별 투자자매매동향(일별) — 기관 세부(증권·투신·사모·은행·보험·기금)까지 쪼개짐."""
    b = kis.fetch(INV_DAILY_PATH, "FHPTJ04040000", {
        "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd,
        "FID_INPUT_DATE_1": day, "FID_INPUT_ISCD_1": iscd1,
        "FID_INPUT_DATE_2": day, "FID_INPUT_ISCD_2": iscd,
    })
    rows = b.get("output1") or b.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def program_trade(mrkt="1", exch="1"):
    """프로그램매매 투자자별 — 차익(arbt) vs 비차익(nabt) 구분이 핵심.
    예제에는 MRKT_DIV_CLS_CODE만 있으나 실제로는 EXCH_DIV_CLS_CODE도 요구한다."""
    b = kis.fetch(PROG_PATH, "HHPPG046600C1",
                  {"MRKT_DIV_CLS_CODE": mrkt, "EXCH_DIV_CLS_CODE": exch})
    rows = b.get("output1") or b.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def _f(x, d=0.0):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return d


def derivatives_summary(fut_o1, call_rows, put_rows):
    """카드 세션이 200행을 다시 훑지 않도록 파생 핵심 지표를 미리 계산해 둔다."""
    d = {}
    o1 = fut_o1 or {}
    oi, oi_chg = _f(o1.get("hts_otst_stpl_qty")), _f(o1.get("otst_stpl_qty_icdc"))
    chg = _f(o1.get("futs_prdy_vrss"))
    d["future"] = {
        "price": _f(o1.get("futs_prpr")), "change": chg,
        "basis": _f(o1.get("basis")), "theoretical": _f(o1.get("hts_thpr")),
        "disparity": _f(o1.get("dprt")), "kospi200": _f(o1.get("kospi200_nmix")),
        "open_interest": oi, "oi_change": oi_chg,
    }
    # 가격 방향 x 미결제 증감 → 신규/청산 판별
    if chg and oi_chg:
        d["future"]["position_read"] = (
            "신규 매수" if chg > 0 and oi_chg > 0 else
            "숏커버"   if chg > 0 and oi_chg < 0 else
            "신규 매도" if chg < 0 and oi_chg > 0 else "롱청산")

    def side(rows, tag):
        out = {"oi_total": 0.0, "vol_total": 0.0, "max_oi_strike": None,
               "max_oi": 0.0, "gamma_oi": 0.0, "atm_iv": None}
        for r in rows:
            k, o = _f(r.get("acpr")), _f(r.get("hts_otst_stpl_qty"))
            out["oi_total"] += o
            out["vol_total"] += _f(r.get("acml_vol"))
            out["gamma_oi"] += _f(r.get("gama")) * o
            if o > out["max_oi"]:
                out["max_oi"], out["max_oi_strike"] = o, k
            if str(r.get("atm_cls_name") or "").strip() == "ATM":
                out["atm_iv"] = _f(r.get("hts_ints_vltl"))
        return out

    c, p = side(call_rows, "call"), side(put_rows, "put")
    d["call"], d["put"] = c, p
    if c["oi_total"]:
        d["put_call_oi_ratio"] = round(p["oi_total"] / c["oi_total"], 3)
    if c["vol_total"]:
        d["put_call_vol_ratio"] = round(p["vol_total"] / c["vol_total"], 3)
    # 딜러 포지션 가정은 사람마다 다르므로 부호를 붙이지 않고 양쪽을 따로 남긴다
    d["gamma_oi_note"] = "call/put 각각 Σ(감마×미결제). 딜러 부호 가정은 사용하는 쪽에서 적용할 것"
    return d


def probe_investor():
    """시장구분 코드가 어디까지 먹는지 탐색 — 선물·옵션 수급이 되는지 확인용.
    결과 보고 이 함수는 지운다."""
    print("--- 투자자 수급 시장구분 탐색 ---")
    for iscd in ("999", "0001", "1001", "2001", "3001", "4001", "0000"):
        for iscd2 in ("S001", "0001"):
            try:
                rows = investor_time(iscd, iscd2)
                h = rows[0] if rows else {}
                print(f"  iscd={iscd:5s} iscd2={iscd2:5s} rows={len(rows):3d}  "
                      f"외인순매수={h.get('frgn_ntby_qty')} 개인={h.get('prsn_ntby_qty')} "
                      f"기관={h.get('orgn_ntby_qty')}")
            except Exception as e:
                print(f"  iscd={iscd:5s} iscd2={iscd2:5s} 실패: {str(e)[:60]}")
    print("--- 탐색 끝 ---")


if __name__ == "__main__":
    main()
    probe_investor()
