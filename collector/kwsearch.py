#!/usr/bin/env python3
"""키워드 / 채널 인기순 검색. 결과는 docs/data/kw.json (최신순 최대 40건).

- 정기 수집(collect.py)에서는 config.json 의 "keywords" 를 회전하며 실행한다.
- 수동 실행은 GitHub Actions 의 workflow_dispatch 입력(keyword, channel ...)으로만 한다.
  => API 키는 저장소 비밀값에만 있고 브라우저에는 절대 내려가지 않는다.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C

OUT = os.path.join(C.ROOT, "docs", "data")
KEEP = 40


def _load():
    p = os.path.join(OUT, "kw.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def _save(entries):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "kw.json"), "w", encoding="utf-8") as f:
        json.dump(entries[:KEEP], f, ensure_ascii=False, separators=(",", ":"))


def _channels(quota, ids):
    out = {}
    ids = list(dict.fromkeys(i for i in ids if i))
    for batch in C.chunks(ids, 50):
        r = C.call(quota, "channels", {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50})
        for ch in r.get("items", []):
            st, sn = ch.get("statistics", {}), ch.get("snippet", {})
            out[ch["id"]] = {
                "t": sn.get("title", ""), "h": sn.get("customUrl", ""),
                "s": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                "created": C.parse_ts(sn["publishedAt"]) if sn.get("publishedAt") else 0}
    return out


def _items(quota, rows):
    ts = C.now_ts()
    rows = [r for r in rows if r["d"] <= C.CFG["max_short_sec"] and r["c"]]
    chs = _channels(quota, [r["c"] for r in rows])
    items = []
    for r in rows:
        ch = chs.get(r["c"], {})
        subs = ch.get("s")
        age_h = max((ts - r["pub"]) / 3600, 0.5)
        items.append({
            "id": r["id"], "c": r["c"], "ct": ch.get("t", ""), "cs": subs, "t": r["t"], "th": r["th"],
            "pub": r["pub"], "v": r["v"], "vph": round(r["v"] / age_h), "d": r["d"],
            "x": round(r["v"] / subs, 2) if subs else None,
            "age": round((ts - ch["created"]) / 86400) if ch.get("created") else None})
    items.sort(key=lambda i: -i["v"])
    return items[:50], chs


def search_keyword(quota, q, region="ALL", days=30):
    """키워드로 쇼츠를 조회수순 검색. region: KR/US/ALL, days: 0이면 기간 제한 없음."""
    ts = C.now_ts()
    p = {"part": "snippet", "type": "video", "videoDuration": "short", "order": "viewCount",
         "q": q, "maxResults": 50}
    if region in C.CFG["regions"]:
        p["regionCode"] = region
        p["relevanceLanguage"] = C.CFG["regions"][region]["relevanceLanguage"]
    if days:
        p["publishedAfter"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts - days * 86400))
    r = C.call(quota, "search", p)
    ids = [it["id"]["videoId"] for it in r.get("items", [])]
    items, chs = _items(quota, C.fetch_videos(quota, ids)) if ids else ([], {})
    return {"key": f"kw:{q}|{region}|{days}", "kind": "kw", "q": q, "region": region, "days": days,
            "ts": ts, "items": items}, chs


def resolve_channel(quota, ref):
    """UC... 채널 ID 또는 @핸들 -> 채널 ID."""
    ref = ref.strip()
    m = re.search(r"(UC[0-9A-Za-z_-]{22})", ref)
    if m:
        return m.group(1)
    handle = ref.rsplit("/", 1)[-1].lstrip("@")
    r = C.call(quota, "channels", {"part": "id", "forHandle": "@" + handle})
    its = r.get("items", [])
    return its[0]["id"] if its else None


def channel_top(quota, ref):
    """레퍼런스 채널 인기순 상위 쇼츠. (최신 영상은 조용해도 인기순에는 터진 소스가 있다)"""
    cid = resolve_channel(quota, ref)
    if not cid:
        return None
    r = C.call(quota, "search", {"part": "snippet", "type": "video", "channelId": cid,
                                  "order": "viewCount", "maxResults": 50})
    ids = [it["id"]["videoId"] for it in r.get("items", [])]
    items, chs = _items(quota, C.fetch_videos(quota, ids)) if ids else ([], {})
    title = chs.get(cid, {}).get("t", cid)
    return {"key": f"ch:{cid}", "kind": "ch", "q": title, "cid": cid, "region": "", "days": 0,
            "ts": C.now_ts(), "items": items}


def put(entry):
    """같은 key 는 교체하고 최신순으로 저장."""
    ents = [e for e in _load() if e["key"] != entry["key"]]
    _save([entry] + ents)


def track(quota, pool, run_idx):
    """config.json 의 keywords 중 이번 회차 몫만 실행. 발견 채널은 수집 풀에도 넣는다."""
    kws = C.CFG.get("keywords", [])
    n = C.CFG.get("keyword_searches_per_run", 4)
    if not kws:
        return
    start = (run_idx * n) % len(kws)
    for k in [kws[(start + i) % len(kws)] for i in range(min(n, len(kws)))]:
        try:
            entry, chs = search_keyword(quota, k["q"], k.get("region", "ALL"), k.get("days", C.CFG.get("keyword_window_days", 30)))
        except C.QuotaStop:
            raise
        except RuntimeError as e:
            print("키워드 검색 실패:", k, e)
            continue
        put(entry)
        for it in entry["items"]:
            if it["c"] not in pool and len(pool) < C.CFG["max_pool_channels"]:
                pool[it["c"]] = {"first_seen": C.now_ts(), "region": k.get("region") if k.get("region") in C.CFG["regions"] else "KR"}
        print(f"키워드 추적 '{k['q']}': {len(entry['items'])}개")


def main():
    """수동 실행: 환경변수 KW, KW_REGION, KW_DAYS, KW_CHANNEL (Actions 입력에서 전달)."""
    kw = os.environ.get("KW", "").strip()
    ch = os.environ.get("KW_CHANNEL", "").strip()
    if not kw and not ch:
        sys.exit("KW 또는 KW_CHANNEL 이 필요합니다.")
    region = os.environ.get("KW_REGION", "ALL").strip() or "ALL"
    days = int(os.environ.get("KW_DAYS", "30") or 30)
    quota = C.Quota()
    try:
        if kw:
            entry, _ = search_keyword(quota, kw[:100], region, days)
            put(entry)
            print(f"키워드 '{kw}' {region} {days}일: {len(entry['items'])}개")
        if ch:
            entry = channel_top(quota, ch[:200])
            if entry:
                put(entry)
                print(f"채널 인기순 '{entry['q']}': {len(entry['items'])}개")
            else:
                print("채널을 찾지 못했습니다:", ch[:60])
    except C.QuotaStop as e:
        print("할당량 중단:", e)
    finally:
        quota.save()
        print(f"오늘 사용 추정: units {quota.used}, 검색 {quota.searches}/{C.CFG['daily_search_call_budget']}회")


if __name__ == "__main__":
    main()
