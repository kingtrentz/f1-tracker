"""Top stories per round for the Next Race and Standings tabs.
Every build reads a few F1 news feeds (RSS, free, no keys), files each headline under the round it belongs to,
and ranks stories by how many outlets are covering them. Only headlines and links are kept (no article text).
Saved in news/<season>.json (the workflow commits it): raw headlines for open rounds, and the final top stories
for rounds that are over, so earlier rounds keep their stories after the feeds move on.
  python news.py   update once and print the top stories per round"""
import json, math, os, re, datetime as dt, urllib.request, email.utils, xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "news")
UTC = dt.timezone.utc
FEEDS = [   # (outlet, url, category that must be present or None). Order = which outlet's headline leads a story.
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/formula1/rss.xml", None),
    ("Formula 1", "https://www.formula1.com/en/latest/all.xml", None),
    ("The Race", "https://www.the-race.com/feed/", "Formula 1"),
    ("Autosport", "https://www.autosport.com/rss/f1/news/", None),
    ("Motorsport.com", "https://www.motorsport.com/rss/f1/news/", None),
]
SKIP = re.compile(r"\b(betting|odds|quiz|podcast|live:|live blog|as it happened|watch:|tv times|how to watch|how to follow|f2|f3|f1 academy|motogp|indycar|nascar|wec)\b", re.I)
STOP = set("""about after again against also amid another around back been before being best between both bring brings
could does doing down during each even ever every fans final first from gets give given goes going grand have here into
just keep keeps last like look looks made make makes more most much need next only other over prix race races racing
reveals said says season should since some still than that their them then there these they this those three time
told under until very want wants week weekend were what when where which while will with without would year years your
formula""".split())
TOP_N = 8

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (f1-tracker fan project)"})
    with urllib.request.urlopen(req, timeout=20) as r: return r.read()

def fetch(now):
    out, errs = [], []
    for src, url, need in FEEDS:
        try: root = ET.fromstring(_get(url))
        except Exception as e: errs.append(f"{src}: {type(e).__name__}"); continue
        for it in root.iter("item"):
            t = re.sub(r"\s+", " ", (it.findtext("title") or "")).strip()
            u = (it.findtext("link") or it.findtext("guid") or "").strip()
            if not t or not u.startswith("http") or SKIP.search(t): continue
            cats = [(c.text or "").strip() for c in it.findall("category")]
            if need and need not in cats: continue
            at = None
            if it.findtext("pubDate"):
                try: at = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(UTC)
                except Exception: at = None
            u = re.sub(r"[?#].*$", "", u) if "bbc.co" in u else u
            out.append({"t": t, "u": u, "src": src, "at": (at or now).isoformat(timespec="seconds"), "nd": 0 if at else 1,
                        "cat": " ".join(c for c in cats if c and c != "Formula 1")[:80]})
    return out, errs

def keywords(rnd):
    """Words that tie a headline to a round: 'Las Vegas', 'Qatar', 'Lusail', 'Abu Dhabi', 'Yas Marina'..."""
    c = rnd["circuit"]; ks = set()
    for s in (rnd["name"].replace("Grand Prix", ""), c["locality"], c["country"], c["name"]):
        s = re.sub(r"\b(Circuit|International|Autodromo|Autódromo|Street|Strip|Park|Raceway|de|del|of|the|Grand Prix)\b", " ", s, flags=re.I)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 4 and s.lower() not in ("usa", "united states", "united kingdom", "uk"): ks.add(s.lower())
    for s in list(ks): ks.update(w for w in s.split() if len(w) >= 5)
    return ks

def assign(item, rounds):
    """Round for a headline: one it names (within a week either side of that weekend), else the round whose
    news window it falls in (from 4 days before its FP1 to 4 days before the next FP1)."""
    at = dt.datetime.fromisoformat(item["at"])
    text = (item["t"] + " " + item.get("cat", "")).lower()
    for r in rounds:
        fp1, race = r["_fp1"], r["_race"]
        if fp1 - dt.timedelta(days=7) <= at <= race + dt.timedelta(days=7) and any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in r["_kw"]):
            return r["round"]
    cur = None
    for r in rounds:
        if at >= r["_fp1"] - dt.timedelta(days=4): cur = r["round"]
    return cur

def words(t):
    t = re.sub(r"[’']s\b", "", t.lower())
    return {w for w in re.findall(r"[a-z0-9]+", t) if (len(w) >= 4 or w.isdigit() and len(w) == 4) and w not in STOP}

def top(items, place=()):
    """Group headlines about the same story and rank by how many outlets have it, then how many headlines, then how recent.
    Words are weighted by rarity across the round (every story names Verstappen or Las Vegas, few name Camara), and a
    headline joins a story only if it's close to the story on average, so common names can't chain stories together."""
    items = sorted(items, key=lambda x: x["at"])
    drop = {w for k in place for w in k.split()}
    W = [words(x["t"]) - drop for x in items]
    df = {}
    for w in W:
        for t in w: df[t] = df.get(t, 0) + 1
    idf = {t: math.log((len(W) + 1) / (c + 0.5)) for t, c in df.items()}
    def sim(a, b):
        if not a or not b: return 0
        return sum(idf[t] for t in a & b) / math.sqrt(sum(idf[t] for t in a) * sum(idf[t] for t in b))
    groups = []
    for i, w in enumerate(W):
        best, bs = None, 0
        for g in groups:
            v = sum(sim(w, W[j]) for j in g) / len(g)
            if v > bs: best, bs = g, v
        if best is not None and bs >= 0.3: best.append(i)
        else: groups.append([i])
    rank = {s: k for k, (s, _, _) in enumerate(FEEDS)}
    out = []
    for g in groups:
        g = [items[j] for j in g]
        srcs = sorted({x["src"] for x in g}, key=lambda s: rank.get(s, 9))
        lead = min(g, key=lambda x: (rank.get(x["src"], 9), x["at"]))
        out.append({"t": re.sub(r"^F1 [^:]{3,40}: ", "", lead["t"]), "u": lead["u"], "src": lead["src"], "at": max(x["at"] for x in g if not x.get("nd")) if any(not x.get("nd") for x in g) else max(x["at"] for x in g),
                    "n": len(srcs), "k": len(g), "also": srcs[1:]})
    out.sort(key=lambda x: (x["n"], x["k"], x["at"]), reverse=True)
    return out[:TOP_N]

def update(sched, now):
    """sched: this season's Jolpica schedule (build.schedule). Returns {round: [top stories]} for the season."""
    if not sched: return {}
    season = sched[0]["season"]
    rounds = []
    for r in sched:
        r = dict(r); r["_fp1"] = dt.datetime.fromisoformat(r["sessions"][0]["start"]); r["_race"] = dt.datetime.fromisoformat(r["start"])
        r["_kw"] = keywords(r); rounds.append(r)
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, f"{season}.json")
    try: store = json.load(open(path, encoding="utf-8"))
    except Exception: store = {"items": [], "final": {}}
    fresh, errs = fetch(now)
    seen = {x["u"] for x in store["items"]}
    for x in fresh:
        if x["u"] in seen or dt.datetime.fromisoformat(x["at"]) < now - dt.timedelta(days=10): continue
        r = assign(x, rounds)
        if r is None: continue
        x["r"] = r; store["items"].append(x); seen.add(x["u"])
    # a round's stories are final 10 days after its race (the feeds have moved on by then): keep the top list, drop the raw headlines
    for r in rounds:
        k = str(r["round"])
        if k not in store["final"] and now > r["_race"] + dt.timedelta(days=10):
            its = [x for x in store["items"] if x.get("r") == r["round"]]
            if its: store["final"][k] = top(its, r["_kw"])
            store["items"] = [x for x in store["items"] if x.get("r") != r["round"]]
    out = dict(store["final"])
    for r in rounds:
        k = str(r["round"])
        if k in out: continue
        its = [x for x in store["items"] if x.get("r") == r["round"]]
        if its: out[k] = top(its, r["_kw"])
    new = json.dumps(store, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    try: old = open(path, encoding="utf-8").read()
    except Exception: old = ""
    if new != old: open(path, "w", encoding="utf-8").write(new)
    if errs: print("NOTE news feeds skipped: " + ", ".join(errs))
    return {"season": season, "rounds": out, "checked": now.isoformat(timespec="seconds")}

if __name__ == "__main__":
    import build
    now = dt.datetime.now(UTC)
    d = update(build.schedule(now.year), now)
    for k, v in sorted(d.get("rounds", {}).items(), key=lambda x: int(x[0])):
        print(f"R{k}")
        for s in v: print(f"  {s['n']}x {s['src']}: {s['t']}")
