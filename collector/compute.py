#!/usr/bin/env python3
"""수집된 시계열/영상 데이터로 지표를 계산해 docs/data/*.json 생성."""
import json, os, re, math, statistics as stt, time, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "docs", "data")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
KST = dt.timezone(dt.timedelta(hours=9))
DAY = 86400


def load(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def dump(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def clamp01(x):
    return max(0.0, min(1.0, x))


def grade(s):
    return "SSS" if s >= 80 else "SS" if s >= 60 else "S" if s >= 40 else "A" if s >= 20 else "B"


def point_before(pts, ts, min_age):
    """ts 기준 min_age초 이상 지난 가장 최근 포인트 (없으면 None)."""
    cand = [p for p in pts if ts - p[0] >= min_age]
    return cand[-1] if cand else None


def views_delta(pts, ts, window):
    """최근 window초 동안 늘어난 채널 총 조회수. 이력이 모자라면 None."""
    if len(pts) < 2:
        return None
    base = point_before(pts, ts, window * 0.8)  # 80% 이상 채워진 구간만 인정
    if not base:
        return None
    span = pts[-1][0] - base[0]
    if span <= 0:
        return None
    return (pts[-1][2] - base[2]) * (window / span)


def norm_title(t):
    """같은 소재 판별용 제목 정규화: 해시태그/기호/공백 제거, 소문자."""
    t = re.sub(r"#\S+", " ", t.lower())
    return re.sub(r"[^0-9a-z가-힣ぁ-んァ-ン一-龥]", "", t)


def main():
    ts = int(time.time())
    meta = load("channels.json", {})
    series = load("series.json", {})
    videos = load("videos.json", {})
    rpm = CFG["rpm_won_per_view"]
    hg = CFG["hidden_gem"]
    chans, vid_out = {}, []
    hints = [h.lower() for h in CFG.get("rights_holder_hints", [])]
    # 같은 소재(제목)가 서로 다른 채널에서 몇 곳 올라왔는지: 퍼지는 중인 소스 신호
    owners = {}
    for cid, ent in videos.items():
        for v in ent.get("items", []):
            k = norm_title(v["t"])
            if len(k) >= 8:
                owners.setdefault(k, set()).add(cid)

    for cid, m in meta.items():
        pts = series.get(cid, [])
        if not pts:
            continue
        subs = None if m.get("hidden_subs") else pts[-1][1]
        items = videos.get(cid, {}).get("items", [])
        recent30 = [v for v in items if ts - v["pub"] <= 30 * DAY]
        base = recent30 if len(recent30) >= 3 else items
        views_list = [v["v"] for v in base]
        median_v = stt.median(views_list) if views_list else 0
        mean_v = (sum(views_list) / len(views_list)) if views_list else 0
        # 중앙값 기반: 영상 하나가 터진 채널이 비율만 부풀리는 것을 막는다
        ratio = (median_v / subs) if subs else None
        up30 = len(recent30)
        up7 = len([v for v in items if ts - v["pub"] <= 7 * DAY])

        # 월 예상 조회수: 실측 시계열 우선, 이력 짧으면 (중앙값 x 월 업로드 수)
        d30 = views_delta(pts, ts, 30 * DAY)
        if d30 is not None and pts[-1][0] - pts[0][0] >= 3 * DAY:
            est_month, est_src = d30, "실측"
        else:
            est_month, est_src = median_v * up30, "추정"
        revenue = est_month * rpm

        # 1일 증가 조회수 (급상승/성장 계산용)
        d1 = views_delta(pts, ts, 1 * DAY)
        d3 = views_delta(pts, ts, 3 * DAY)
        if d1 is None:  # 이력 없음 -> 최근 3일 내 영상 조회수로 대체 추정
            est3 = sum(v["v"] for v in items if ts - v["pub"] <= 3 * DAY)
            d1, d3, grow_src = est3 / 3, est3, "영상기반"
        else:
            grow_src = "실측"
            if d3 is None:  # 3일치 이력이 아직 없으면 1일 증가량으로 환산
                d3 = d1 * 3
        subs_d7 = None
        b7 = point_before(pts, ts, 5 * DAY)
        if b7 and subs:
            subs_d7 = pts[-1][1] - b7[1]

        rev_pts = 40 * clamp01(math.log10(1 + revenue / 10000) / math.log10(1 + 5_000_000 / 10000))
        ratio_pts25 = 35 * clamp01((ratio or 0) / 3)
        cons_pts = 25 * clamp01(up30 / 20)
        honey = round(rev_pts + ratio_pts25 + cons_pts)
        growth_pts = 40 * clamp01((d1 / max(subs or 1000, 1000)) / 0.5)
        ratio_pts40 = 40 * clamp01((ratio or 0) / 3)
        act_pts = 20 * clamp01(up7 / 7)
        hot = round(ratio_pts40 + growth_pts + act_pts)
        age_days = (ts - m["created"]) / DAY if m.get("created") else None
        confident = len(items) >= 3 and subs is not None

        chans[cid] = {
            "id": cid, "t": m["title"], "h": m.get("handle", ""), "th": m.get("thumb", ""),
            "rg": m.get("region", "KR"), "co": m.get("country", ""),
            "s": subs, "v": pts[-1][2], "n": pts[-1][3],
            "med": round(median_v), "avg": round(mean_v), "r": None if ratio is None else round(ratio, 3),
            "rev": round(revenue), "rsrc": est_src, "up30": up30, "up7": up7,
            "age": None if age_days is None else round(age_days),
            "honey": honey, "g": grade(honey), "hot": hot,
            "d1": round(d1), "d3": round(d3), "gsrc": grow_src, "sd7": subs_d7,
            "ok": confident, "pts": len(pts),
            # 방송사/공식 채널로 보이는지 (이름 휴리스틱일 뿐, 권리 판단이 아님)
            "rh": any(h in m["title"].lower() for h in hints),
        }
        # 영상 피드 (14일 이내, 조회수 3천 이상만 저장해 용량 제한)
        for v in items:
            age_h = max((ts - v["pub"]) / 3600, 0.5)
            if age_h <= 14 * 24 and v["v"] >= 3000:
                vid_out.append({
                    "id": v["id"], "c": cid, "rg": m.get("region", "KR"), "t": v["t"], "th": v["th"], "pub": v["pub"], "v": v["v"],
                    "vph": round(v["v"] / age_h), "lk": v["l"], "d": v["d"],
                    "x": None if not subs else round(v["v"] / subs, 2),
                    "m": round(v["v"] / median_v, 1) if median_v else None,
                    "dup": len(owners.get(norm_title(v["t"]), ())) if len(norm_title(v["t"])) >= 8 else 0,
                })

    ok = [c for c in chans.values() if c["ok"]]
    by = lambda lst, k, n=30: sorted(lst, key=lambda c: -(c[k] or 0))[:n]
    lists = {
        "surge24": [c["id"] for c in by([c for c in ok if c["d1"] > 0], "d1")],
        "surge3d": [c["id"] for c in by([c for c in ok if c["d3"] > 0], "d3")],
        "newborn": [c["id"] for c in by([c for c in ok if c["age"] is not None and c["age"] <= CFG["new_channel_days"]], "hot")],
        "hidden": [c["id"] for c in by([c for c in ok if c["s"] and 500 <= c["s"] <= hg["max_subs"]
                                         and (c["r"] or 0) >= hg["min_ratio"]], "r")],
        "honey_top": [c["id"] for c in by(ok, "honey", 10)],
    }
    # 오늘의 한 방: 48시간 이내 영상이 채널 평소(중앙값)의 3배 이상 & 1만 조회 이상
    shots = [v for v in vid_out if ts - v["pub"] <= 48 * 3600 and v["v"] >= 10000
             and v["m"] and v["m"] >= 3 and chans[v["c"]]["ok"]]
    shots = sorted(shots, key=lambda v: -v["m"])[:20]

    # 날짜·국가별로 따로 상위 150개씩 (한쪽 국가가 다른 쪽을 밀어내지 않게)
    daily, cnt = {}, {}
    for v in sorted(vid_out, key=lambda v: -v["v"]):
        d = dt.datetime.fromtimestamp(v["pub"], KST).strftime("%Y-%m-%d")
        k = (d, v["rg"])
        if cnt.get(k, 0) < 150:
            cnt[k] = cnt.get(k, 0) + 1
            daily.setdefault(d, []).append(v)

    series_out = {}
    for cid in chans:
        pts = series[cid][-30:]
        series_out[cid] = [[(p[0] - ts) // 3600, p[1], p[2]] for p in pts]  # 현재 기준 '시간 전'

    dump("channels.json", chans)
    dump("daily.json", daily)
    dump("series.json", series_out)
    dump("dashboard.json", {
        "generated": ts, "channels": len(chans), "trusted": len(ok),
        "lists": lists, "shots": shots,
        "regions": {k: v["label"] for k, v in CFG["regions"].items()},
        "cats": CFG["categories"], "hg": hg, "newDays": CFG["new_channel_days"],
        "history_days": round(max((max((p[-1][0] for p in series.values() if p), default=ts) -
                                    min((p[0][0] for p in series.values() if p), default=ts)) / DAY, 0), 1),
    })
    print(f"계산 완료: 채널 {len(chans)} (신뢰 {len(ok)}), 영상 {len(vid_out)}, 한방 {len(shots)}")


if __name__ == "__main__":
    main()
