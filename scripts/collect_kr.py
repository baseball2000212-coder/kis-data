# -*- coding: utf-8 -*-
"""국장 마감 데이터 수집 — K200 선물 분봉/현재가 + 옵션 전광판(그릭스 포함).

선물옵션 분봉조회   FHKIF03020200  FID_HOUR_CLS_CODE = 초 단위 간격(60=1분, 300=5분)
                                   FID_INPUT_HOUR_1 을 앞으로 옮겨가며 과거 방향 페이징
선물옵션 현재가     FHMIF10000000
국내옵션 전광판 콜풋 FHPIF05030100  행사가별 델타·감마·베가·세타·IV·미결제약정
"""
import os, json, string, time, datetime as dt
import kis

CHART_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
PRICE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
BOARD_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-callput"
FUTBOARD_PATH = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
INV_TIME_PATH  = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
INV_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
PROG_PATH      = "/uapi/domestic-stock/v1/quotations/investor-program-trade-today"
IDXCHART_PATH  = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
COMPPROG_PATH  = "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"

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
    """옵션 주력 월물 YYYYMM. 만기일 당일부터는 다음 달."""
    y, m = today.year, today.month
    # 만기 당일에는 근월물 미결제가 이미 소멸한다. 그날부터 다음 월물을 본다.
    if today.day >= second_thursday(y, m):
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
        call, put, ncalls = option_board_all(exp)
        out["option_board"] = {"call": call, "put": put}
        ks = sorted(_f(r.get("acpr")) for r in call if r.get("acpr"))
        print(f"[opt] {exp} 콜 {len(call)}행 / 풋 {len(put)}행 (calls={ncalls}) "
              f"행사가 {ks[0] if ks else '-'}~{ks[-1] if ks else '-'}")
        for tag, rows in (("콜", call), ("풋", put)):
            for r in rows[:2]:
                print(f"      {tag} 행사가={r.get('acpr')} 현재가={r.get('optn_prpr')} "
                      f"미결제={r.get('hts_otst_stpl_qty')} IV={r.get('hts_ints_vltl')} "
                      f"ATM={r.get('atm_cls_name')} 기준가={r.get('nmix_sdpr')}")
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
    for name, iscd in (("코스피", "0001"), ("코스닥", "1001")):
        try:
            head, reg, n, tk = index_intraday(iscd)
            out[f"index_{name}"] = {"iscd": iscd, "summary": head, "bars": reg, "time_key": tk}
            rng = f"{reg[0][tk]}~{reg[-1][tk]}" if reg else "-"
            print(f"[idx] {name} 분봉 {len(reg)}봉 ({rng}) calls={n}")
        except Exception as e:
            out["errors"].append(f"{name} 분봉: {e}")
            print(f"[idx] {name} 분봉 실패: {str(e)[:80]}")

    for label, fn in (("수급 시간대별", lambda: investor_time("999", "S001")),
                      ("수급 일별", lambda: investor_daily(day)),
                      ("프로그램 코스피", lambda: comp_program("K")),
                      ("프로그램 코스닥", lambda: comp_program("Q"))):
        key = {"수급 시간대별": "investor_time", "수급 일별": "investor_daily",
               "프로그램 코스피": "program_kospi", "프로그램 코스닥": "program_kosdaq"}[label]
        try:
            rows = fn()
            out[key] = rows
            extra = ""
            if rows and label.startswith("프로그램"):
                r = rows[0]
                extra = ("  " + " ".join(f"{k}={r.get(k)}" for k in list(r)[:6]))[:150]
            print(f"[flow] {label} {len(rows)}행{extra}")
        except Exception as e:
            out["errors"].append(f"{label}: {e}")
            print(f"[flow] {label} 실패: {str(e)[:80]}")

    if out["errors"]:
        print(f"-- 실패 {len(out['errors'])}건")
    kis.save("kr", out)


def index_intraday(iscd="0001", interval="60"):
    """국내 업종·지수 분봉 (FHKUP03500200). 0001=코스피 1001=코스닥.
    FID_INPUT_HOUR_1 은 초 단위 간격(60=1분)."""
    head, rows, cont, n = None, [], "", 0
    for _ in range(6):
        b, h = kis._call(IDXCHART_PATH, "FHKUP03500200", {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0",
            "FID_INPUT_ISCD": iscd, "FID_INPUT_HOUR_1": interval,
            "FID_PW_DATA_INCU_YN": "Y",
        }, cont)
        n += 1
        if head is None:
            head = b.get("output1")
        page = b.get("output2") or []
        if isinstance(page, dict):
            page = [page]
        if not page:
            break
        rows += page
        if (h.get("tr_cont") or "").strip() not in ("M", "F"):
            break
        cont = "N"
        time.sleep(0.3)
    tk = next((k for k in ("stck_cntg_hour", "bsop_hour", "cntg_hour")
               if rows and k in rows[0]), "stck_cntg_hour")
    dk = next((k for k in ("stck_bsop_date", "bsop_date") if rows and k in rows[0]), None)
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: ((r.get(dk) or "") if dk else "", r.get(tk) or "")):
        key = ((r.get(dk) or "") if dk else "", r.get(tk) or "")
        if key not in seen:
            seen.add(key); uniq.append(r)
    if dk and uniq:
        day = max(r.get(dk) or "" for r in uniq)
        uniq = [r for r in uniq if (r.get(dk) or "") == day]
    reg = [r for r in uniq if SESSION_OPEN <= (r.get(tk) or "") <= "153000"]
    return head, reg, n, tk


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


def comp_program(mrkt_cls="K"):
    """프로그램매매 종합현황(시간) FHPPG04600101 — 차익/비차익.
    K=코스피 Q=코스닥. 파라미터가 단순해 이쪽을 우선 쓴다."""
    b = kis.fetch(COMPPROG_PATH, "FHPPG04600101", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_MRKT_CLS_CODE": mrkt_cls,
        "FID_SCTN_CLS_CODE": "", "FID_INPUT_ISCD": "",
    })
    rows = b.get("output") or b.get("output1") or []
    return [rows] if isinstance(rows, dict) else rows


def program_trade(mrkt="1", exch="1"):
    """프로그램매매 투자자별 — 차익(arbt) vs 비차익(nabt) 구분이 핵심.
    예제에는 MRKT_DIV_CLS_CODE만 있으나 실제로는 EXCH_DIV_CLS_CODE도 요구한다."""
    b = kis.fetch(PROG_PATH, "HHPPG046600C1",
                  {"MRKT_DIV_CLS_CODE": mrkt, "EXCH_DIV_CLS_CODE": exch})
    rows = b.get("output1") or b.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def option_board_all(exp, max_pages=6):
    """전광판은 한 번에 100행(가장 높은 행사가부터)만 준다.
    tr_cont 연속조회로 ATM 근처까지 내려간다. 반환: (콜, 풋, 호출수)"""
    calls, puts, cont, n = [], [], "", 0
    for _ in range(max_pages):
        b, h = kis._call(BOARD_PATH, "FHPIF05030100", {
            "FID_COND_MRKT_DIV_CODE": "O", "FID_COND_SCR_DIV_CODE": "20503",
            "FID_MRKT_CLS_CODE": "CO", "FID_MTRT_CNT": exp,
            "FID_MRKT_CLS_CODE1": "PO", "FID_COND_MRKT_CLS_CODE": "",
        }, cont)
        n += 1
        c, p = b.get("output1") or [], b.get("output2") or []
        if isinstance(c, dict): c = [c]
        if isinstance(p, dict): p = [p]
        if not c and not p:
            break
        calls += c; puts += p
        if (h.get("tr_cont") or "").strip() not in ("M", "F"):
            break
        cont = "N"
        time.sleep(0.3)

    def dedup(rows):
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: _f(r.get("acpr")), reverse=True):
            k = r.get("acpr")
            if k and k not in seen:
                seen.add(k); out.append(r)
        return out
    return dedup(calls), dedup(puts), n


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

    # nmix_sdpr 는 지수가 아니다(실측). 선물 가격을 기준가로 쓴다.
    spot = d["future"]["price"] or d["future"]["kospi200"]

    def side(rows, tag):
        out = {"oi_total": 0.0, "vol_total": 0.0, "max_oi_strike": None,
               "max_oi": 0.0, "gamma_oi": 0.0, "atm_iv": None, "n": len(rows),
               "strike_min": None, "strike_max": None}
        near, near_gap = None, 1e18
        for r in rows:
            k, o = _f(r.get("acpr")), _f(r.get("hts_otst_stpl_qty"))
            out["oi_total"] += o
            out["vol_total"] += _f(r.get("acml_vol"))
            out["gamma_oi"] += _f(r.get("gama")) * o
            if o > out["max_oi"]:
                out["max_oi"], out["max_oi_strike"] = o, k
            if k:
                out["strike_min"] = k if out["strike_min"] is None else min(out["strike_min"], k)
                out["strike_max"] = k if out["strike_max"] is None else max(out["strike_max"], k)
                if spot and abs(k - spot) < near_gap:
                    near_gap, near = abs(k - spot), r
            if str(r.get("atm_cls_name") or "").strip() == "ATM":
                out["atm_iv"] = _f(r.get("hts_ints_vltl"))
        # atm_cls_name 이 비어 있으면 기준가에 가장 가까운 행사가로 대체
        if out["atm_iv"] is None and near is not None:
            out["atm_iv"] = _f(near.get("hts_ints_vltl"))
            out["atm_strike"] = _f(near.get("acpr"))
        return out

    d["spot_ref"] = spot
    c, p = side(call_rows, "call"), side(put_rows, "put")
    d["call"], d["put"] = c, p
    if c["oi_total"]:
        d["put_call_oi_ratio"] = round(p["oi_total"] / c["oi_total"], 3)
    if c["vol_total"]:
        d["put_call_vol_ratio"] = round(p["vol_total"] / c["vol_total"], 3)
    # 딜러 포지션 가정은 사람마다 다르므로 부호를 붙이지 않고 양쪽을 따로 남긴다
    d["gamma_oi_note"] = "call/put 각각 Σ(감마×미결제). 딜러 부호 가정은 사용하는 쪽에서 적용할 것"
    return d


# ─────────────────────────────────────────────────────────────
# 진단: 모르는 코드값을 한 번에 훑어 data/kr/_diag.json 에 남긴다.
# 레포가 public이라 결과를 원격에서 바로 읽을 수 있다. 확정되면 이 블록은 지운다.
def diagnose():
    import traceback
    D = {"asof_kst": kis.now_kst().isoformat(timespec="seconds")}

    def rows_of(b):
        r = b.get("output1") or b.get("output") or []
        return [r] if isinstance(r, dict) else r

    # 1) 선물 전광판 시장구분 — 정규 K200 선물(101…)이 나오는 코드를 찾는다
    D["futures_board"] = {}
    for cls in ("MKI", "KI", "MKM", "SPI", "F", "FUT", "K200", "KSP", "", "0", "1"):
        try:
            b = kis.fetch(FUTBOARD_PATH, "FHPIF05030200", {
                "FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
                "FID_COND_MRKT_CLS_CODE": cls})
            rs = rows_of(b)
            D["futures_board"][cls] = [
                {"code": r.get("futs_shrn_iscd"), "name": r.get("hts_kor_isnm"),
                 "price": r.get("futs_prpr"), "oi": r.get("hts_otst_stpl_qty"),
                 "days": r.get("hts_rmnn_dynu")} for r in rs[:6]]
        except Exception as e:
            D["futures_board"][cls] = f"실패: {str(e)[:120]}"

    # 2) 프로그램매매 EXCH_DIV_CLS_CODE
    D["program"] = {}
    for exch in ("1", "2", "3", "01", "02", "K", "Q", "KSP", "KSQ", "UN", "A", ""):
        try:
            b = kis.fetch(PROG_PATH, "HHPPG046600C1",
                          {"MRKT_DIV_CLS_CODE": "1", "EXCH_DIV_CLS_CODE": exch})
            rs = rows_of(b)
            D["program"][exch or "(빈값)"] = {
                "n": len(rs),
                "sample": [{"투자자": r.get("invr_cls_name"),
                            "전체순매수": r.get("all_ntby_amt"),
                            "차익순매수": r.get("arbt_ntby_amt"),
                            "비차익순매수": r.get("nabt_ntby_amt")} for r in rs[:3]]}
        except Exception as e:
            D["program"][exch or "(빈값)"] = f"실패: {str(e)[:120]}"

    # 3) 옵션 전광판 — 월물별·시장구분별 행사가 분포
    D["option_board"] = {}
    today = kis.now_kst().date()
    for exp in (option_expiry_month(today),
                f"{today.year}{today.month:02d}"):
        for cls in ("", "MKI", "KI"):
            key = f"{exp}/{cls or '기본'}"
            try:
                b = kis.fetch(BOARD_PATH, "FHPIF05030100", {
                    "FID_COND_MRKT_DIV_CODE": "O", "FID_COND_SCR_DIV_CODE": "20503",
                    "FID_MRKT_CLS_CODE": "CO", "FID_MTRT_CNT": exp,
                    "FID_MRKT_CLS_CODE1": "PO", "FID_COND_MRKT_CLS_CODE": cls})
                call = b.get("output1") or []
                put = b.get("output2") or []
                if isinstance(call, dict): call = [call]
                if isinstance(put, dict): put = [put]
                ks = sorted({_f(r.get("acpr")) for r in call if r.get("acpr")})
                D["option_board"][key] = {
                    "n_call": len(call), "n_put": len(put),
                    "strike_min": ks[0] if ks else None,
                    "strike_max": ks[-1] if ks else None,
                    "strike_step": round(ks[1] - ks[0], 3) if len(ks) > 1 else None,
                    "sample_call": call[:2], "sample_put": put[:1]}
            except Exception as e:
                D["option_board"][key] = f"실패: {str(e)[:120]}"

    # 3b) 프로그램매매 종합현황
    D["comp_program"] = {}
    for mc in ("K", "Q", "1", "2", ""):
        try:
            rs = comp_program(mc)
            D["comp_program"][mc or "(빈값)"] = {"n": len(rs), "sample": rs[:2]}
        except Exception as e:
            D["comp_program"][mc or "(빈값)"] = f"실패: {str(e)[:120]}"

    # 4) 수급 일별 — 코스피/코스닥 분리가 되는지
    D["investor_daily"] = {}
    day = today.strftime("%Y%m%d")
    for iscd, iscd1 in (("0001", "KSP"), ("1001", "KSQ"), ("2001", "KSP"), ("0001", "KSQ")):
        try:
            rs = investor_daily(day, iscd=iscd, iscd1=iscd1)
            D["investor_daily"][f"{iscd}/{iscd1}"] = {
                "n": len(rs),
                "sample": [{k: r.get(k) for k in
                            ("stck_bsop_date", "bstp_nmix_prpr", "frgn_ntby_qty",
                             "prsn_ntby_qty", "orgn_ntby_qty", "frgn_ntby_tr_pbmn")}
                           for r in rs[:2]]}
        except Exception as e:
            D["investor_daily"][f"{iscd}/{iscd1}"] = f"실패: {str(e)[:120]}"

    os.makedirs("data/kr", exist_ok=True)
    with open("data/kr/_diag.json", "w", encoding="utf-8") as f:
        json.dump(D, f, ensure_ascii=False, indent=1)
    print("진단 저장: data/kr/_diag.json")


if __name__ == "__main__":
    main()
    diagnose()
