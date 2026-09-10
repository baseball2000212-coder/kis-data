# -*- coding: utf-8 -*-
"""KIS Open API 최소 클라이언트 — 토큰 발급 + 시세 조회(연속조회 포함)."""
import os, json, time, datetime as dt
import requests

PROD = "https://openapi.koreainvestment.com:9443"
VTS  = "https://openapivts.koreainvestment.com:29443"

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
BASE       = VTS if os.environ.get("KIS_ENV", "prod") == "vps" else PROD

KST = dt.timezone(dt.timedelta(hours=9))
_token = None


def now_kst():
    return dt.datetime.now(KST)


def token():
    """접근토큰 발급 (24시간 유효, 1분 1회 제한 — 실행당 1번만 부른다)."""
    global _token
    if _token:
        return _token
    r = requests.post(
        f"{BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET},
        headers={"content-type": "application/json"}, timeout=20,
    )
    r.raise_for_status()
    _token = r.json()["access_token"]
    return _token


def _call(path, tr_id, params, tr_cont=""):
    """단건 호출. (body, 응답헤더) 반환."""
    h = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token()}",
        "appkey": APP_KEY, "appsecret": APP_SECRET,
        "tr_id": tr_id, "tr_cont": tr_cont, "custtype": "P",
    }
    for attempt in range(3):
        r = requests.get(f"{BASE}{path}", headers=h, params=params, timeout=25)
        if r.status_code == 200:
            b = r.json()
            if str(b.get("rt_cd")) != "0":
                raise RuntimeError(f"{tr_id} rt_cd={b.get('rt_cd')} msg={b.get('msg1')}")
            return b, r.headers
        if r.status_code in (429, 500, 502, 503):
            time.sleep(1.5 * (attempt + 1)); continue
        raise RuntimeError(f"{tr_id} HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError(f"{tr_id} 재시도 실패")


def fetch(path, tr_id, params, tr_cont=""):
    return _call(path, tr_id, params, tr_cont)[0]


def fetch_all(path, tr_id, params, max_pages=8, pause=0.35):
    """연속조회로 output2를 끝까지 모은다.
    응답헤더 tr_cont 가 'M'/'F' 면 다음 페이지가 있고, 요청헤더에 'N'을 실어 이어받는다.
    반환: (output1, output2 전체 리스트, 실제 호출 횟수)"""
    head, rows, cont = None, [], ""
    calls = 0
    for _ in range(max_pages):
        b, h = _call(path, tr_id, params, cont)
        calls += 1
        if head is None:
            head = b.get("output1")
        out = b.get("output2") or []
        if isinstance(out, dict):
            out = [out]
        if not out:
            break
        rows.extend(out)
        if (h.get("tr_cont") or "").strip() not in ("M", "F"):
            break
        cont = "N"
        time.sleep(pause)
    return head, rows, calls


DATE_KEYS = ("stck_bsop_date", "xymd", "bsop_date", "kymd", "tymd")
TIME_KEYS = ("stck_cntg_hour", "xhms", "khms", "cntg_hour", "hms")


def timespan(rows):
    """봉 리스트의 시각 범위를 사람이 읽을 수 있게. 로그로 커버리지 확인용."""
    def stamp(r):
        d = next((r[k] for k in DATE_KEYS if r.get(k)), "")
        t = next((r[k] for k in TIME_KEYS if r.get(k)), "")
        return f"{d} {t}".strip() or "?"
    if not rows:
        return "없음"
    a, b = stamp(rows[0]), stamp(rows[-1])
    return f"{a} ~ {b}" if a != b else a


def try_fetch_all(path, tr_id, params, label="", **kw):
    """실패해도 파이프라인을 세우지 않는다."""
    try:
        return fetch_all(path, tr_id, params, **kw), None
    except Exception as e:
        return (None, [], 0), f"{label or tr_id}: {e}"


def save(kind, payload, outdir="data"):
    d = os.path.join(outdir, kind)
    os.makedirs(d, exist_ok=True)
    stamp = payload["asof_kst"][:10].replace("-", "")
    for name in (f"{stamp}.json", "latest.json"):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"saved {d}/{stamp}.json ({os.path.getsize(os.path.join(d, 'latest.json')):,} bytes)")
