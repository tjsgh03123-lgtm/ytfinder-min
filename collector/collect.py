#!/usr/bin/env python3
"""YouTube Data API v3 수집기 (표준 라이브러리만 사용).

할당량 설계 (일 10,000 units 기본):
  search.list            별도 버킷(일 100회) -> 채널 '발굴'에만 사용, 회당 searches_per_run 개로 제한
                         (구 문서는 100 units로 안내했으므로 config의 예산은 보수적으로 잡음)
  channels.list          1 unit/50개 -> 풀 전체 스냅샷 (싸다)
  playlistItems.list     1 unit/채널 -> 최근 영상 목록
  videos.list            1 unit/50개
  videos.list(mostPopular) 1 unit/페이지 -> 지역별 인기 영상 (덤)
"""
import json, os, sys, time, re, datetime as dt, urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
API = "https://www.googleapis.com/youtube/v3/"
SEARCH_BUCKET = True  # 2026-06 이후 공식 문서: search.list는 별도로 일 100회, 나머지는 일 10,000 units


class QuotaStop(Exception):
    pass


def now_ts():
    return int(time.time())


def load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


class Quota:
    """태평양 시간(PST, UTC-8) 날짜 기준 근사치 — YouTube 할당량은 태평양 자정에 리셋된다."""

    def __init__(self):
        q = load("quota.json", {})
        today = dt.datetime.utcfromtimestamp(time.time() - 8 * 3600).strftime("%Y-%m-%d")
        self.date = today
        same = q.get("date") == today
        self.used = q.get("used", 0) if same else 0
        self.searches = q.get("searches", 0) if same else 0

    def charge(self, endpoint, n=1):
        if endpoint == "search":  # 별도 버킷: 일 검색 호출 횟수
            if self.searches + n > CFG["daily_search_call_budget"]:
                raise QuotaStop(f"검색 호출 일일 예산 {CFG['daily_search_call_budget']}회 도달")
            self.searches += n
            return
        if self.used + n > CFG["daily_quota_budget"]:
            raise QuotaStop(f"일일 units 예산 {CFG['daily_quota_budget']} 초과 직전 (사용 {self.used})")
        self.used += n

    def save(self):
        save("quota.json", {"date": self.date, "used": self.used, "searches": self.searches})


def api(endpoint, params):
    """실제 HTTP 호출. 테스트에서는 이 함수를 통째로 교체한다."""
    key = os.environ.get("YT_API_KEY")
    if not key:
        sys.exit("환경변수 YT_API_KEY가 없습니다.")
    q = urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(API + endpoint + "?" + q, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        if e.code == 403 and "quota" in body.lower():
            raise QuotaStop("YouTube가 quotaExceeded 반환")
        raise RuntimeError(f"{endpoint} HTTP {e.code}: {body[:300]}")


def call(quota, endpoint, params):
    quota.charge(endpoint)
    return api(endpoint, params)


def iso_dur(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 10**6
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def parse_ts(s):
    return int(dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp())


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def video_row(v):
    sn, st = v.get("snippet", {}), v.get("statistics", {})
    th = sn.get("thumbnails", {})
    return {
        "id": v["id"], "c": sn.get("channelId"), "t": sn.get("title", ""),
        "pub": parse_ts(sn["publishedAt"]) if sn.get("publishedAt") else 0,
        "v": int(st.get("viewCount", 0)), "l": int(st.get("likeCount", 0)),
        "cm": int(st.get("commentCount", 0)),
        "d": iso_dur(v.get("contentDetails", {}).get("duration")),
        "th": (th.get("medium") or th.get("default") or {}).get("url", ""),
    }


def fetch_videos(quota, ids):
    out = []
    for batch in chunks(list(ids), 50):
        r = call(quota, "videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50})
        out += [video_row(v) for v in r.get("items", [])]
    return out


# ---------- 1) 발굴 ----------
def discover(quota, pool, run_idx):
    ts = now_ts()
    regions = list(CFG["regions"].items())
    cats = list(CFG["categories"].keys())
    n = CFG["searches_per_run"]
    after = dt.datetime.utcfromtimestamp(ts - CFG["search_window_hours"] * 3600).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}  # video id -> region
    # 지역 x 카테고리 조합을 실행 회차마다 돌려가며 사용 (고르게 순환)
    combos = [(rc, cat) for cat in cats for rc, _ in regions]
    start = (run_idx * n) % len(combos)
    picks = [combos[(start + i) % len(combos)] for i in range(min(n, len(combos)))]
    for rc, cat in picks:
        try:
            r = call(quota, "search", {
                "part": "snippet", "type": "video", "videoDuration": "short", "order": "viewCount",
                "q": CFG["regions"][rc].get("query", "#shorts"), "videoCategoryId": cat, "regionCode": rc,
                "relevanceLanguage": CFG["regions"][rc]["relevanceLanguage"],
                "publishedAfter": after, "maxResults": 50})
        except QuotaStop as e:  # 검색 예산이 끝나도 스냅샷/영상 갱신은 계속 진행해야 한다
            print("검색 중단:", e)
            break
        except RuntimeError as e:
            print("search 실패:", e)
            continue
        for it in r.get("items", []):
            found.setdefault(it["id"]["videoId"], rc)
    # 지역별 인기 차트 (1 unit)
    for rc, _ in regions:
        try:
            r = call(quota, "videos", {"part": "snippet,statistics,contentDetails", "chart": "mostPopular",
                                        "regionCode": rc, "maxResults": 50})
            for v in r.get("items", []):
                found.setdefault(v["id"], rc)
        except QuotaStop:
            raise
        except RuntimeError as e:
            print("mostPopular 실패:", e)
    rows = fetch_videos(quota, found.keys())
    added = 0
    for row in rows:
        if row["d"] > CFG["max_short_sec"] or not row["c"]:
            continue
        c = pool.get(row["c"])
        if c is None:
            if len(pool) >= CFG["max_pool_channels"]:
                continue
            pool[row["c"]] = {"first_seen": ts, "region": found.get(row["id"], "KR")}
            added += 1
    print(f"발굴: 영상 {len(rows)}개 확인, 신규 채널 {added}개, 풀 {len(pool)}개")
    return rows


# ---------- 국가 판별 ----------
import re as _re
_HANGUL = _re.compile(r"[\uac00-\ud7a3]")


def classify_region(sn):
    """채널의 실제 국가를 KR/US로 판별. 검색 regionCode는 신뢰할 수 없어 쓰지 않는다.
    확실하지 않으면 None(제외): 인도·인니 등 다른 나라 채널이 섞이는 것보다 놓치는 쪽이 낫다."""
    country = (sn.get("country") or "").upper()
    lang = (sn.get("defaultLanguage") or "").lower()
    if country in ("KR", "US"):
        return country
    if country:
        return None
    if _HANGUL.search(sn.get("title", "")) or lang.startswith("ko"):
        return "KR"
    if lang in ("en", "en-us"):
        return "US"
    return None


# ---------- 2) 채널 스냅샷 ----------
def snapshot_channels(quota, pool, series, meta):
    ts = now_ts()
    keep = CFG["series_keep_points"]
    ids = list(pool.keys())
    for batch in chunks(ids, 50):
        r = call(quota, "channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50})
        seen = set()
        for ch in r.get("items", []):
            cid = ch["id"]
            seen.add(cid)
            st, sn = ch.get("statistics", {}), ch.get("snippet", {})
            hidden = bool(st.get("hiddenSubscriberCount"))
            subs = int(st.get("subscriberCount", 0))
            views = int(st.get("viewCount", 0))
            vids = int(st.get("videoCount", 0))
            series.setdefault(cid, []).append([ts, subs, views, vids])
            series[cid] = series[cid][-keep:]
            rgn = classify_region(sn)
            if rgn is None:  # 한국/미국 채널이 아니면 풀과 기록에서 제거
                pool.pop(cid, None); meta.pop(cid, None); series.pop(cid, None)
                continue
            th = sn.get("thumbnails", {})
            meta[cid] = {
                "title": sn.get("title", ""), "handle": sn.get("customUrl", ""),
                "thumb": (th.get("default") or {}).get("url", ""),
                "country": sn.get("country", ""), "created": parse_ts(sn["publishedAt"]) if sn.get("publishedAt") else 0,
                "uploads": ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
                "hidden_subs": hidden, "region": rgn,
            }
        for cid in batch:  # 삭제/비공개된 채널은 풀에서 제거
            if cid not in seen:
                pool.pop(cid, None)
    print(f"스냅샷: {len(meta)}개 채널")


# ---------- 3) 최근 영상 ----------
def refresh_videos(quota, pool, meta, videos, seed_rows):
    ts = now_ts()
    stale = CFG["video_refresh_stale_hours"] * 3600
    # 발굴 단계에서 이미 받은 영상은 바로 반영(추가 호출 없음)
    for row in seed_rows:
        if row["d"] <= CFG["max_short_sec"] and row["c"] in pool:
            ent = videos.setdefault(row["c"], {"updated": 0, "items": []})
            ent["items"] = [x for x in ent["items"] if x["id"] != row["id"]] + [row]
    order = sorted(pool.keys(), key=lambda c: videos.get(c, {}).get("updated", 0))
    todo = [c for c in order if ts - videos.get(c, {}).get("updated", 0) > stale][:CFG["video_refresh_per_run"]]
    done = 0
    for cid in todo:
        up = meta.get(cid, {}).get("uploads")
        if not up:
            continue
        r = call(quota, "playlistItems", {"part": "contentDetails", "playlistId": up,
                                           "maxResults": CFG["recent_videos_per_channel"]})
        ids = [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        rows = [x for x in fetch_videos(quota, ids) if x["d"] <= CFG["max_short_sec"]]
        videos[cid] = {"updated": ts, "items": rows}
        done += 1
    print(f"최근 영상 갱신: {done}/{len(todo)} 채널")


def main():
    quota = Quota()
    pool = load("pool.json", {})
    series = load("series.json", {})
    meta = load("channels.json", {})
    videos = load("videos.json", {})
    run_idx = int(os.environ.get("RUN_IDX", dt.datetime.utcnow().hour // 6))
    seed = []
    try:
        # 단계별로 따로 보호: 앞 단계가 예산 때문에 멈춰도 뒤 단계(싼 호출)는 시도한다
        for name, fn in (("발굴", lambda: seed.extend(discover(quota, pool, run_idx))),
                         ("키워드추적", lambda: __import__("kwsearch").track(quota, pool, run_idx)),
                         ("스냅샷", lambda: snapshot_channels(quota, pool, series, meta)),
                         ("영상갱신", lambda: refresh_videos(quota, pool, meta, videos, seed))):
            try:
                fn()
            except QuotaStop as e:
                print(f"{name} 단계 할당량 중단:", e)
    finally:
        # 풀에서 빠진 채널 정리
        for d in (series, meta, videos):
            for cid in [c for c in d if c not in pool]:
                d.pop(cid, None)
        save("pool.json", pool); save("series.json", series)
        save("channels.json", meta); save("videos.json", videos)
        quota.save()
        print(f"오늘 사용 추정: units {quota.used}/{CFG['daily_quota_budget']}, 검색 {quota.searches}/{CFG['daily_search_call_budget']}회")


if __name__ == "__main__":
    sys.modules.setdefault("collect", sys.modules[__name__])  # kwsearch 가 같은 모듈(같은 예외 클래스)을 쓰게 한다
    main()
