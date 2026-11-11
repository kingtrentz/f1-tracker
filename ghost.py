"""Ghost laps for the Ghost Lap tab: every driver's best qualifying lap of every weekend from 2023 on,
as car positions every 0.2 s, so any two can be raced against each other on the track map.

Data: OpenF1 (car positions about 4 times a second, free from 2023; F1's own archive needs an F1 TV login).
Jolpica-F1 gives the official order and lap times, which pick the right lap out of OpenF1.
Saved in ghost/ and committed by the workflow, so each lap is downloaded once:
  ghost/index.json             circuits, plus which weekends are done
  ghost/<circuit>.json         that circuit's laps (driver, team, time, sectors), track outline, start line
  ghost/laps/<circuit>/<year>-<round>-<car number>.json   one lap's positions and speed

  python ghost.py              fetch anything missing (all seasons from 2023)
  python ghost.py 2025 2024    those seasons only
build.py calls update() with a time limit, so new qualifying laps appear after each weekend."""
import json, os, sys, time, math, datetime as dt, urllib.parse
import build as B
try: import fcntl            # lets several fetchers share ghost/ safely (Linux; not needed on Windows)
except ImportError: fcntl = None

GDIR = os.path.join(B.HERE, "ghost")
FIRST = 2023          # first season OpenF1 has positions for
VERSION = 2           # 2 = whole field (1 kept only the top 3)
STEP = 0.2            # seconds between saved points
WAIT_AFTER = 2        # hours after qualifying ends before trying (OpenF1 posts positions after the session)
RETRY_HOURS = 24      # a weekend with missing laps is retried after this long, at most 4 times
OUTLINE_N = 300       # points in a saved layout outline
LAYOUT_TOL = 250      # dm (25 m): a lap further than this from a saved layout (95th percentile) is a different layout
SAME_FRAME = 80       # dm: a lap this close to the outline needs no frame fitting

def secs(s):
    """'1:29.179' -> 89.179"""
    if not s: return None
    try:
        p = s.split(":")
        return round(float(p[-1]) + (60 * int(p[0]) if len(p) > 1 else 0), 3)
    except ValueError: return None

def when(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def q(d): return urllib.parse.quote(d.astimezone(B.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"))

def of1(path):
    """OpenF1 GET with gentle pacing (shared timer with build.get) and longer back-off on 429."""
    for i in range(5):
        n = len(B.ERRS["openf1"])
        d = B.get(f"{B.OPENF1}/{path}", "openf1", tries=1)
        if d is not None: return d
        if len(B.ERRS["openf1"]) == n: return None            # 404: nothing there
        last = B.ERRS["openf1"][-1]
        if last[:1] == "4" and not last.startswith("429"): return None   # a real refusal, don't hammer
        B.ERRS["openf1"].pop()            # rate limit, server hiccup or timeout we waited out isn't an error
        time.sleep(4 * (i + 1))
    B.ERRS["openf1"].append(f"gave up {path[:80]}")
    return None

def interp(ts, vals, t):
    """Linear interpolation of vals over sorted ts at time t (clamped)."""
    if t <= ts[0]: return vals[0]
    if t >= ts[-1]: return vals[-1]
    lo, hi = 0, len(ts) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if ts[m] <= t: lo = m
        else: hi = m
    a = (t - ts[lo]) / ((ts[hi] - ts[lo]) or 1)
    return vals[lo] + a * (vals[hi] - vals[lo])

def load(fn, default):
    try: return json.load(open(os.path.join(GDIR, fn), encoding="utf-8"))
    except Exception: return default

def save(fn, obj):
    full = os.path.join(GDIR, fn)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = f"{full}.{os.getpid()}.tmp"
    open(tmp, "w", encoding="utf-8", newline="\n").write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, full)

# ---------------- one lap ----------------
def crossing_lap(skey, num, official, sf, sess):
    """Fallback when OpenF1 has no lap list (Baku 2025): find the lap in the car's positions for the whole
    session, as two passes of the start/finish point that are the official lap time apart."""
    if not sf or not sess.get("date_start") or not sess.get("date_end"): return None
    a, b = when(sess["date_start"]) - dt.timedelta(minutes=5), when(sess["date_end"]) + dt.timedelta(minutes=30)
    loc = of1(f"location?session_key={skey}&driver_number={num}&date>={q(a)}&date<={q(b)}") or []
    P = sorted(((when(p["date"]) - a).total_seconds(), p["x"], p["y"]) for p in loc if p.get("date") and (p.get("x") or p.get("y")))
    if len(P) < 100: return None
    T, X, Y = [p[0] for p in P], [p[1] for p in P], [p[2] for p in P]
    near = [i for i in range(len(P)) if math.hypot(X[i] - sf[0], Y[i] - sf[1]) < 500]
    groups, cur = [], []
    for i in near:
        if cur and i - cur[-1] > 3: groups.append(cur); cur = []
        cur.append(i)
    if cur: groups.append(cur)
    cross = []
    for g in groups:   # closest approach to the line, to 0.01 s
        t0, t1 = T[max(0, g[0] - 1)], T[min(len(T) - 1, g[-1] + 1)]
        best = min((math.hypot(interp(T, X, t0 + k / 100) - sf[0], interp(T, Y, t0 + k / 100) - sf[1]), t0 + k / 100)
                   for k in range(int((t1 - t0) * 100) + 1))
        if best[0] < 300: cross.append(best[1])
    pairs = [(abs((c2 - c1) - official), c1) for c1, c2 in zip(cross, cross[1:])]
    if not pairs or min(pairs)[0] > 0.3: return None
    return {"date_start": (a + dt.timedelta(seconds=min(pairs)[1])).isoformat(), "lap_duration": official}

def raw_lap(skey, num, official, sf=None, sess=None, laps=None):
    """Positions and speed for the OpenF1 lap matching the official time. None if not found.
    laps: the session's lap list for this driver if already fetched (one call per session, not per driver)."""
    if laps is None: laps = of1(f"laps?session_key={skey}&driver_number={num}")
    cand = [l for l in laps or [] if l.get("lap_duration") and l.get("date_start")]
    if cand:
        lap = min(cand, key=lambda l: abs(l["lap_duration"] - official))
        if abs(lap["lap_duration"] - official) > 0.2: return None
    else:
        lap = crossing_lap(skey, num, official, sf, sess or {})
        if not lap: return None
    st = when(lap["date_start"]); dur = official
    a, b = st - dt.timedelta(seconds=2), st + dt.timedelta(seconds=dur + 2)
    loc = of1(f"location?session_key={skey}&driver_number={num}&date>={q(a)}&date<={q(b)}")
    car = of1(f"car_data?session_key={skey}&driver_number={num}&date>={q(a)}&date<={q(b)}")
    P = sorted(((when(p["date"]) - st).total_seconds(), p["x"], p["y"]) for p in loc or []
               if p.get("date") and not (p.get("x") == 0 and p.get("y") == 0))
    if len(P) < dur * 2.5: return None          # too few points to draw (normal rate is ~3.7 a second)
    C = sorted(((when(c["date"]) - st).total_seconds(), c.get("speed") or 0) for c in car or [] if c.get("date"))
    secs3 = [lap.get(f"duration_sector_{i}") for i in (1, 2, 3)]
    return {"dur": dur, "P": P, "C": C, "sec": secs3 if all(secs3) else None}

def align(raw, sf):
    """Shift time so 0 is where the car passes the start/finish point sf (OpenF1 lap start times wobble by ~0.2 s)."""
    T, X, Y = [p[0] for p in raw["P"]], [p[1] for p in raw["P"]], [p[2] for p in raw["P"]]
    if sf is None: return 0.0
    best, bt = None, 0.0
    for i in range(-150, 151):              # search +-1.5 s in 0.01 s steps
        t = i / 100
        d = math.hypot(interp(T, X, t) - sf[0], interp(T, Y, t) - sf[1])
        if best is None or d < best: best, bt = d, t
    return bt if best is not None and best < 600 else 0.0   # not near the line at all: leave as is

def resample(raw, shift):
    T, X, Y = [p[0] - shift for p in raw["P"]], [p[1] for p in raw["P"]], [p[2] for p in raw["P"]]
    CT, CV = [c[0] - shift for c in raw["C"]], [c[1] for c in raw["C"]]
    n = int(raw["dur"] / STEP)
    ts = [k * STEP for k in range(n + 1)] + ([raw["dur"]] if n * STEP < raw["dur"] - 1e-6 else [])
    xs = [round(interp(T, X, t)) for t in ts]
    ys = [round(interp(T, Y, t)) for t in ts]
    vs = [round(interp(CT, CV, t)) if CT else 0 for t in ts]
    return xs, ys, vs

def outline(xs, ys):
    """Evenly spaced points along a lap (by distance), used to spot layout changes and draw the track."""
    d = [0.0]
    for i in range(1, len(xs)): d.append(d[-1] + math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]))
    L = d[-1] or 1
    return [[round(interp(d, xs, L * k / OUTLINE_N)), round(interp(d, ys, L * k / OUTLINE_N))] for k in range(OUTLINE_N)], L

def fit(xs, ys, ref, rounds=12):
    """Best rotation + shift that lays this lap onto a saved outline (OpenF1 moves its map frame between
    some seasons, e.g. Suzuka 2023-24 sits ~30 m off). Returns (angle, dx, dy, cx, cy), applied by move()."""
    px, py = xs[::3], ys[::3]
    cx, cy = sum(px) / len(px), sum(py) / len(py)
    ang, dx, dy = 0.0, 0.0, 0.0
    for _ in range(rounds):
        mx, my = move(px, py, (ang, dx, dy, cx, cy))
        pairs = [(x, y, *min(ref, key=lambda r: (r[0] - x) ** 2 + (r[1] - y) ** 2)) for x, y in zip(mx, my)]
        ax = sum(p[0] for p in pairs) / len(pairs); ay = sum(p[1] for p in pairs) / len(pairs)
        bx = sum(p[2] for p in pairs) / len(pairs); by = sum(p[3] for p in pairs) / len(pairs)
        sxx = sum((p[0] - ax) * (p[2] - bx) + (p[1] - ay) * (p[3] - by) for p in pairs)
        sxy = sum((p[0] - ax) * (p[3] - by) - (p[1] - ay) * (p[2] - bx) for p in pairs)
        da = math.atan2(sxy, sxx)
        # compose: rotate the moved points by da about their centre, then shift centre onto the matches
        ang += da
        ncx, ncy = ax - cx - dx, ay - cy - dy          # centre offset of moved points vs pivot (rotated frame)
        rx = ncx * math.cos(da) - ncy * math.sin(da); ry = ncx * math.sin(da) + ncy * math.cos(da)
        dx, dy = bx - cx - rx, by - cy - ry
    return (ang, dx, dy, cx, cy)

def move(xs, ys, tf):
    ang, dx, dy, cx, cy = tf
    c, s = math.cos(ang), math.sin(ang)
    return ([round((x - cx) * c - (y - cy) * s + cx + dx) for x, y in zip(xs, ys)],
            [round((x - cx) * s + (y - cy) * c + cy + dy) for x, y in zip(xs, ys)])

def off_layout(xs, ys, ref):
    """95th percentile distance from this lap to a saved layout outline (dm)."""
    ds = sorted(min(math.hypot(x - a, y - b) for a, b in ref) for x, y in zip(xs[::3], ys[::3]))
    return ds[int(len(ds) * 0.95)] if ds else 1e9


class Lock:
    """Holds ghost/.lock while circuit files and the index are read and rewritten."""
    def __enter__(self):
        os.makedirs(GDIR, exist_ok=True)
        self.f = open(os.path.join(GDIR, ".lock"), "w")
        if fcntl: fcntl.flock(self.f, fcntl.LOCK_EX)
        return self
    def __exit__(self, *a):
        if fcntl: fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()

def lap_path(cid, key): return os.path.join("laps", cid, key + ".json")

# ---------------- one lap into a circuit ----------------
def place(raw, circ):
    """Line the lap up with the circuit: same map frame as its layout, time 0 at the start/finish point.
    Returns (layout id, xs, ys, vs). Creates the circuit's first layout / start point when needed."""
    if circ.get("sf") is None:
        T = [p[0] for p in raw["P"]]
        circ["sf"] = [round(interp(T, [p[1] for p in raw["P"]], 0)), round(interp(T, [p[2] for p in raw["P"]], 0))]
    xs, ys, _ = resample(raw, 0)
    lid = None
    for i, lay in enumerate(circ["layouts"]):
        if off_layout(xs, ys, lay["pts"]) <= SAME_FRAME: lid = i; break
        tf = fit(xs, ys, lay["pts"])
        mx, my = move(xs, ys, tf)
        if off_layout(mx, my, lay["pts"]) <= LAYOUT_TOL:
            lid = i
            px, py = move([p[1] for p in raw["P"]], [p[2] for p in raw["P"]], tf)
            raw["P"] = [(p[0], x, y) for p, x, y in zip(raw["P"], px, py)]
            break
    xs, ys, vs = resample(raw, align(raw, circ["sf"]))
    if lid is None:
        o, L = outline(xs, ys)
        circ["layouts"].append({"pts": o, "len": round(L)}); lid = len(circ["layouts"]) - 1
    return lid, xs, ys, vs

def best_time(x, sr):
    t = [secs(x.get(k)) for k in ("Q1", "Q2", "Q3")]
    t = [v for v in t if v]
    if t: return min(t)
    return sr.get(str(x.get("number")))

# ---------------- one weekend ----------------
def weekend(year, race, sess, cid):
    """Fetch every driver's best qualifying lap not saved yet. Returns (saved now, missing)."""
    skey = sess["session_key"]
    rows = sorted(race.get("QualifyingResults", []), key=lambda x: int(x["position"]))
    sr = None
    if any(not best_time(x, {}) for x in rows):   # Jolpica is missing times (Miami 2025): OpenF1's session result fills in
        sr = {}
        for r in of1(f"session_result?session_key={skey}") or []:
            d = r.get("duration"); v = [a for a in (d if isinstance(d, list) else [d]) if a]
            if v: sr[str(r.get("driver_number"))] = round(min(v), 3)
    colors = {str(d.get("driver_number")): "#" + d["team_colour"].lstrip("#")
              for d in of1(f"drivers?session_key={skey}") or [] if d.get("team_colour")}
    by_car = {}
    for l in of1(f"laps?session_key={skey}") or []: by_car.setdefault(str(l.get("driver_number")), []).append(l)
    with Lock(): circ = load(f"{cid}.json", None)
    have = {l["key"] for l in (circ or {}).get("laps", [])}
    saved = missing = 0
    for x in rows:
        num, t = x.get("number"), best_time(x, sr or {})
        if not num or not t: continue
        key = f"{year}-{int(race['round'])}-{num}"
        if key in have and os.path.exists(os.path.join(GDIR, lap_path(cid, key))): continue
        raw = raw_lap(skey, num, t, (circ or {}).get("sf"), sess, by_car.get(str(num), []))
        if not raw:
            missing += 1; print(f"  {year} R{race['round']} P{x['position']} #{num}: no matching lap"); continue
        d, c = x["Driver"], x["Constructor"]
        with Lock():
            circ = load(f"{cid}.json", None) or {"id": cid, "name": race["Circuit"]["circuitName"],
                    "loc": race["Circuit"]["Location"]["locality"], "country": race["Circuit"]["Location"]["country"],
                    "sf": None, "layouts": [], "laps": []}
            lid, xs, ys, vs = place(raw, circ)
            save(lap_path(cid, key), {"dt": STEP, "x": xs, "y": ys, "v": vs})
            circ["laps"] = [l for l in circ["laps"] if l["key"] != key]
            circ["laps"].append({"key": key, "year": year, "round": int(race["round"]), "gp": race["raceName"], "pos": int(x["position"]),
                                 "code": d.get("code") or d["familyName"][:3].upper(), "name": f'{d["givenName"]} {d["familyName"]}',
                                 "num": int(num), "team": c["name"], "color": colors.get(str(num)), "time": t, "sec": raw["sec"], "layout": lid})
            circ["laps"].sort(key=lambda l: (-l["year"], -l["round"], l["pos"]))
            save(f"{cid}.json", circ)
        saved += 1
    print(f"  {year} R{race['round']} {cid}: {saved} saved, {missing} missing")
    return saved, missing

def due(year, idx, sched):
    """Rounds of a season worth trying now: qualifying over, not finished, not given up on or waiting to retry."""
    out = []
    for r in sched:
        k = f"{year}-{r['round']}"
        if idx["done"].get(k, {}).get("v") == VERSION: continue
        f = idx["fail"].get(k)
        if f and (f["tries"] >= 4 or B.NOW - when(f["at"]) < dt.timedelta(hours=RETRY_HOURS)): continue
        qs = next((x for x in r["sessions"] if x["name"] == "Qualifying"), None)
        if qs and B.parse(qs["end"]) + dt.timedelta(hours=WAIT_AFTER) <= B.NOW: out.append(r["round"])
    return out

def season(year, deadline, sched=None):
    """Fetch every finished, missing weekend of one season. Returns False if it ran out of time."""
    with Lock(): idx = load_index()
    if year < B.NOW.year and str(year) in idx["complete"]: return True
    sched = sched if sched is not None else (B.schedule(year) or [])
    todo = due(year, idx, sched)
    if not todo:
        if year < B.NOW.year and sched:
            with Lock():
                idx = load_index()
                if str(year) not in idx["complete"]: idx["complete"].append(str(year)); save("index.json", idx)
        return True
    bysched = {r["round"]: r for r in sched}
    jq = B.jol_all(f"{year}/qualifying", "RaceTable", "QualifyingResults")
    sessions = of1(f"sessions?year={year}&session_name=Qualifying") or []
    shard = os.environ.get("GHOST_SHARD")            # "i/n": this fetcher takes every n-th round (parallel backfills)
    if shard: i, n = map(int, shard.split("/")); todo = [r for r in todo if r % n == i]
    for rnd in todo:
        if time.time() > deadline: return False
        k = f"{year}-{rnd}"
        race = jq.get(rnd)
        if not race:
            if year < B.NOW.year: mark(k, None, 0, 1)   # no qualifying result at all (cancelled)
            continue
        qs = next(x for x in bysched[rnd]["sessions"] if x["name"] == "Qualifying")
        start = B.parse(qs["start"])
        # nearest OpenF1 qualifying within 2.5 days (sessions get moved: Brazil 2024 ran Sunday morning,
        # and Jolpica lists Las Vegas 2024 a day early)
        near = sorted((abs((when(x["date_start"]) - start).total_seconds()), i) for i, x in enumerate(sessions)
                      if x.get("date_start") and not x.get("is_cancelled"))
        m = sessions[near[0][1]] if near and near[0][0] < 2.5 * 86400 else None
        cid = race["Circuit"]["circuitId"]
        print(f"{year} R{rnd} {race['raceName']} ({cid})")
        n, miss = weekend(year, race, m, cid) if m else (0, 1)
        mark(k, cid, n, miss)
    return True

def mark(k, cid, saved, missing):
    with Lock():
        idx = load_index()
        prev = idx["fail"].get(k) or {}
        if missing == 0 or prev.get("tries", 0) >= 3:      # whole field in, or tried enough: done
            idx["done"][k] = {"circuit": cid, "v": VERSION, "missing": missing}; idx["fail"].pop(k, None)
        else:
            idx["fail"][k] = {"tries": prev.get("tries", 0) + 1, "at": B.NOW.isoformat(timespec="seconds")}
            print(f"  {k}: {missing} laps missing, will retry later")
        save("index.json", idx)

def load_index():
    idx = load("index.json", {})
    for k, v in (("done", {}), ("fail", {}), ("complete", [])): idx.setdefault(k, v)
    return idx

def write_index():
    """index.json lists the circuits (small, loaded when the page opens); each circuit file lists its laps."""
    with Lock():
        idx = load_index(); circuits = []
        for fn in sorted(os.listdir(GDIR)):
            if not fn.endswith(".json") or fn == "index.json": continue
            c = load(fn, None)
            if not c or not c.get("laps"): continue
            last = max(c["laps"], key=lambda l: (l["year"], l["round"]))
            circuits.append({"id": c["id"], "name": c["name"], "loc": c["loc"], "country": c["country"], "gp": last["gp"],
                             "years": sorted({l["year"] for l in c["laps"]}, reverse=True), "laps": len(c["laps"])})
        if circuits != idx.get("circuits"): idx["circuits"] = circuits; save("index.json", idx)
        return [c["id"] for c in circuits]

def update(budget=150, years=None, sched_now=None):
    """Called by build.py (with this year's schedule, saving a request). Fetches missing weekends until
    the time budget (seconds) runs out. Past seasons are marked complete and not checked again."""
    deadline = time.time() + budget
    for y in years or range(B.NOW.year, FIRST - 1, -1):     # newest first, so a fresh qualifying lands quickly
        if not season(y, deadline, sched_now if y == B.NOW.year else None): print("ghost: out of time, the rest next run"); break
    return write_index()

if __name__ == "__main__":
    ys = [int(a) for a in sys.argv[1:] if a.isdigit()] or None
    update(budget=10 ** 6, years=ys)
    print("errors:", B.ERRS["openf1"][:5], B.ERRS["jolpica"][:5])
