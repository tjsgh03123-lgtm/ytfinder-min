"""가짜 YouTube API로 수집 -> 계산 -> 결과 JSON 까지 전 과정을 검증한다."""
import json, os, random, sys, tempfile, time, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import collect, compute

random.seed(7)
NOW = [1_800_000_000]
time.time = lambda: NOW[0]  # 시간 고정/조작
tmp = tempfile.mkdtemp()
collect.DATA = compute.DATA = tmp
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
VID = {v["id"]: (cid, v) for cid, c in W.items() for v in c["vids"]}
calls = {"search": 0, "videos": 0, "channels": 0, "playlistItems": 0}

def vitem(vid):
    cid, v = VID[vid]
    return {"id": vid, "snippet": {"channelId": cid, "title": f"영상 {vid} <b>&\"", "publishedAt": iso(v["pub"]),
            "thumbnails": {"medium": {"url": "https://x/y.jpg"}}},
            "statistics": {"viewCount": str(v["v"]), "likeCount": str(v["v"] // 30), "commentCount": "5"},
            "contentDetails": {"duration": f"PT{v['dur'] // 60}M{v['dur'] % 60}S" if v["dur"] >= 60 else f"PT{v['dur']}S"}}

def fake_api(ep, p):
    calls[ep] += 1
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
        for cid in p["id"].split(","):
            c = W.get(cid)
            if not c:
                continue
            c["subs"] = int(c["subs"] * 1.003); c["views"] += random.randint(500, 20000)
            out.append({"id": cid, "snippet": {"title": f"채널 {cid}", "customUrl": f"@{cid.lower()}", "publishedAt": iso(NOW[0] - c["age"] * 86400),
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
print("tmp:", tmp)
