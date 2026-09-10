# -*- coding: utf-8 -*-
"""KIS Open API 최소 클라이언트 — 토큰 발급 + GET 시세 조회."""
import os, json, time, datetime as dt
import requests

PROD = "https://openapi.koreainvestment.com:9443"
VTS  = "https://openapivts.koreainvestment.com:29443"

APP_KEY    = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]
BASE       = VTS if os.environ.get("KIS_ENV", "prod") == "vps" else PROD

KST = dt.timezone(dt.timedelta(hours=9))


def now_kst():
    return dt.datetime.now(KST)


_token = None


def token():
    """접근토큰 발급 (24시간 유효, 1분 1회 제한 — 워크플로 1회 실행당 1번만 부른다)."""
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


def fetch(path, tr_id, params, tr_cont=""):
    """시세 조회 GET. 성공하면 body(dict), 실패하면 예외."""
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
            return b
        if r.status_code in (429, 500, 502, 503):
            time.sleep(1.5 * (attempt + 1)); continue
        raise RuntimeError(f"{tr_id} HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError(f"{tr_id} 재시도 실패")


def try_fetch(path, tr_id, params, label=""):
    """실패해도 파이프라인을 세우지 않는다. (None, 에러문자열) 반환."""
    try:
        return fetch(path, tr_id, params), None
    except Exception as e:
        return None, f"{label or tr_id}: {e}"


def save(kind, payload, outdir="data"):
    """data/<kind>/YYYYMMDD.json 과 latest.json 두 벌로 저장."""
    d = os.path.join(outdir, kind)
    os.makedirs(d, exist_ok=True)
    stamp = payload["asof_kst"][:10].replace("-", "")
    for name in (f"{stamp}.json", "latest.json"):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"saved {d}/{stamp}.json ({len(json.dumps(payload))} bytes)")
