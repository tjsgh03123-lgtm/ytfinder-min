"""가짜 YouTube API로 수집 -> 계산 -> 결과 JSON 까지 전 과정을 검증한다."""
import json, os, random, sys, tempfile, time, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import collect, compute, kwsearch

random.seed(7)
NOW = [1_800_000_000]
time.time = lambda: NOW[0]  # 시간 고정/조작
tmp = tempfile.mkdtemp()
collect.DATA = compute.DATA = tmp
kwsearch.OUT = os.path.join(tmp, 'out')
collect.CFG['keywords'] = [{'q': '야구', 'region': 'KR', 'days': 30}, {'q': 'baseball', 'region': 'US'}]
compute.OUT = os.path.join(tmp, "out")

def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

# --- 가짜 세계: 채널 80개, 영상 각 10개 ---
W = {}
for i in range(80):
    subs = random.choice([800, 3000, 20000, 90000, 800000])
    age = random.choice([20, 60, 400, 900])
    hot = random.random() < 0.2
    W[f"UC{i}"] = {"subs": subs, "age": age, "views": subs * random.randint(5, 50),
                   "vids": [{"id": f"V{i}_{k}", "pub": NOW[0] - random.randint(1, 20 * 24) * 3600,
                             "v": int(subs * random.uniform(0.2, 1.0) * (6 if hot else 1) * random.uniform(0.3, 3)),
                             "dur": random.choice([20, 35, 58, 58, 140])} for k in range(10)]}
for cid in ("UC1", "UC2", "UC3"):  # 서로 다른 채널에서 같은 소재(제목)가 터진 상황
    W[cid]["vids"][0]["title"] = "Amazing catch #shorts 놀라운 장면"
VID = {v["id"]: (cid, v) for cid, c in W.items() for v in c["vids"]}
calls = {"search": 0, "videos": 0, "channels": 0, "playlistItems": 0}

def vitem(vid):
    cid, v = VID[vid]
    return {"id": vid, "snippet": {"channelId": cid, "title": v.get("title", f"영상 {vid} <b>&\""), "publishedAt": iso(v["pub"]),
            "thumbnails": {"medium": {"url": "https://x/y.jpg"}}},
            "statistics": {"viewCount": str(v["v"]), "likeCount": str(v["v"] // 30), "commentCount": "5"},
            "contentDetails": {"duration": f"PT{v['dur'] // 60}M{v['dur'] % 60}S" if v["dur"] >= 60 else f"PT{v['dur']}S"}}

def fake_api(ep, p):
    calls[ep] += 1
    if ep == "search" and p.get("channelId"):
        c = W[p["channelId"]]
        return {"items": [{"id": {"videoId": v["id"]}} for v in sorted(c["vids"], key=lambda x: -x["v"])]}
    if ep == "search":
        recent = sorted([x for x in VID if VID[x][1]["pub"] > NOW[0] - 72 * 3600], key=lambda x: -VID[x][1]["v"])
        random.shuffle(recent)
        return {"items": [{"id": {"videoId": x}} for x in recent[:50]]}
    if ep == "videos":
        if p.get("chart"):
            return {"items": [vitem(x) for x in random.sample(list(VID), 20)]}
        return {"items": [vitem(x) for x in p["id"].split(",") if x in VID]}
    if ep == "channels":
        out = []
        ids = p["id"].split(",") if p.get("id") else (["UC7"] if p.get("forHandle") == "@uc7" else [])
        if p.get("part") == "id":
            return {"items": [{"id": i} for i in ids]}
        for cid in ids:
            c = W.get(cid)
            if not c:
                continue
            c["subs"] = int(c["subs"] * 1.003); c["views"] += random.randint(500, 20000)
            out.append({"id": cid, "snippet": {"title": (f"MBC News {cid}" if int(cid[2:]) % 10 == 0 else f"채널 {cid}"), "customUrl": f"@{cid.lower()}", "publishedAt": iso(NOW[0] - c["age"] * 86400),
                        "country": "KR", "thumbnails": {"default": {"url": "https://x/a.jpg"}}},
                        "statistics": {"subscriberCount": str(c["subs"]), "viewCount": str(c["views"]), "videoCount": "10"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid}}})
        return {"items": out}
    if ep == "playlistItems":
        cid = p["playlistId"][2:]
        return {"items": [{"contentDetails": {"videoId": v["id"]}} for v in W[cid]["vids"]][:p["maxResults"]]}

collect.api = fake_api

for run in range(8):                       # 2일치(6시간 간격 x 8회) 시뮬레이션
    os.environ["RUN_IDX"] = str(run)
    collect.main()
    compute.main()
    NOW[0] += 6 * 3600

# --- 키워드/채널 수동 검색 ---
quota = collect.Quota()
e, _ = kwsearch.search_keyword(quota, "야구", "KR", 7); kwsearch.put(e)
ec = kwsearch.channel_top(quota, "@uc7"); kwsearch.put(ec)
ec2 = kwsearch.channel_top(quota, "UC" + "A" * 22) if False else None
quota.save()
kw = json.load(open(os.path.join(tmp, "out", "kw.json")))
print("kw entries:", [(x["key"], len(x["items"])) for x in kw])
assert kw[0]["kind"] == "ch" and kw[0]["cid"] == "UC7" and len(kw[0]["items"]) > 0
assert any(x["key"] == "kw:야구|KR|7" and x["items"] for x in kw)
assert any(x["key"] == "kw:baseball|US|30" for x in kw), "추적 키워드 결과가 저장돼야 함"
assert all(i["d"] <= 60 for x in kw for i in x["items"])
assert all(kw[0]["items"][k]["v"] >= kw[0]["items"][k + 1]["v"] for k in range(len(kw[0]["items"]) - 1))

q = json.load(open(os.path.join(tmp, "quota.json")))
d = json.load(open(os.path.join(tmp, "out", "dashboard.json")))
ch = json.load(open(os.path.join(tmp, "out", "channels.json")))
daily = json.load(open(os.path.join(tmp, "out", "daily.json")))
print("calls:", calls, "quota:", q)
print("lists:", {k: len(v) for k, v in d["lists"].items()}, "shots:", len(d["shots"]), "history_days:", d["history_days"])
assert all(len(set(v)) == len(v) for v in d["lists"].values())
assert len(ch) > 0 and any(c["gsrc"] == "실측" for c in ch.values()), "실측 성장 계산 안 됨"
assert all(0 <= c["honey"] <= 100 and 0 <= c["hot"] <= 100 for c in ch.values())
assert all(v["d"] <= 60 for vs in daily.values() for v in vs), "60초 초과 영상 섞임"
sample = next(c for c in ch.values() if c["r"] is not None)
print("sample:", {k: sample[k] for k in ("t", "s", "med", "r", "rev", "g", "d1", "gsrc")})
assert any(c["rh"] for c in ch.values()) and not all(c["rh"] for c in ch.values()), "권리자 휴리스틱"
dups = [v["dup"] for vs in daily.values() for v in vs if v["dup"] > 1]
print("rh:", sum(c["rh"] for c in ch.values()), "dup videos:", len(dups), "max dup:", max(dups or [0]))
assert dups and max(dups) >= 3, "같은 소재 반복 감지"
print("tmp:", tmp)
