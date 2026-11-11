"""Ghost laps for the Ghost Lap tab: the top 3 qualifying laps of every weekend from 2023 on,
as car positions every 0.2 s, so two laps can be raced against each other on the track map.

Data: OpenF1 (car positions about 4 times a second, free from 2023; F1's own archive needs an F1 TV login).
Jolpica-F1 gives the official order and lap times, which pick the right lap out of OpenF1.
Saved in ghost/ and committed by the workflow, so each lap is downloaded once.

  python ghost.py            fetch anything missing (all seasons from 2023)
  python ghost.py 2025       one season only
build.py calls update() with a time limit, so new qualifying laps appear after each weekend."""
import json, os, sys, time, math, datetime as dt, urllib.parse
import build as B

GDIR = os.path.join(B.HERE, "ghost")
FIRST = 2023          # first season OpenF1 has positions for
TOP = 3               # laps kept per weekend (pole, P2, P3)
STEP = 0.2            # seconds between saved points
WAIT_AFTER = 2        # hours after qualifying ends before trying (OpenF1 posts positions after the session)
RETRY_HOURS = 24      # a weekend with no data is retried after this long, at most 4 times
OUTLINE_N = 300       # points in a saved layout outline
LAYOUT_TOL = 250      # dm (25 m): a lap further than this from a saved layout (95th percentile) is a different layout

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
    os.makedirs(GDIR, exist_ok=True)
    tmp = os.path.join(GDIR, fn + ".tmp")
    open(tmp, "w", encoding="utf-8", newline="\n").write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, os.path.join(GDIR, fn))

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

def raw_lap(skey, num, official, sf=None, sess=None):
    """Positions and speed for the OpenF1 lap matching the official time. None if not found."""
    laps = of1(f"laps?session_key={skey}&driver_number={num}")
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

# ---------------- one weekend ----------------
def weekend(year, race, sess, circ, colors):
    """Add this weekend's top laps to the circuit file. Returns how many laps were saved."""
    sess_key = sess["session_key"]
    rows = sorted(race.get("QualifyingResults", []), key=lambda x: int(x["position"]))[:TOP]
    got, sr = 0, None
    for x in rows:
        t = secs(x.get("Q3") or x.get("Q2") or x.get("Q1"))
        num = x.get("number") or x["Driver"].get("permanentNumber")
        if not num: continue
        if not t:   # Jolpica is missing the times (Miami 2025): use OpenF1's session result instead
            if sr is None:
                sr = {}
                for r in of1(f"session_result?session_key={sess_key}") or []:
                    d = r.get("duration")
                    best = [v for v in (d if isinstance(d, list) else [d]) if v]
                    if best: sr[str(r.get("driver_number"))] = round(best[-1], 3)
            t = sr.get(str(num))
            if not t: continue
        raw = raw_lap(sess_key, num, t, circ.get("sf"), sess)
        if not raw: print(f"  {year} R{race['round']} P{x['position']} #{num}: no matching lap"); continue
        if circ.get("sf") is None:       # first lap at this circuit sets the start/finish point
            circ["sf"] = [round(interp([p[0] for p in raw["P"]], [p[1] for p in raw["P"]], 0)),
                          round(interp([p[0] for p in raw["P"]], [p[2] for p in raw["P"]], 0))]
        lid = None
        xs, ys, _ = resample(raw, 0)
        for i, lay in enumerate(circ["layouts"]):
            tf = fit(xs, ys, lay["pts"])
            mx, my = move(xs, ys, tf)
            if off_layout(mx, my, lay["pts"]) <= LAYOUT_TOL:
                lid = i
                px, py = move([p[1] for p in raw["P"]], [p[2] for p in raw["P"]], tf)   # into the saved frame
                raw["P"] = [(p[0], x, y) for p, x, y in zip(raw["P"], px, py)]
                break
        shift = align(raw, circ["sf"])
        xs, ys, vs = resample(raw, shift)
        if lid is None:
            o, L = outline(xs, ys)
            circ["layouts"].append({"pts": o, "len": round(L)}); lid = len(circ["layouts"]) - 1
        d = x["Driver"]; c = x["Constructor"]
        key = f"{year}-{int(race['round'])}-{x['position']}"
        circ["laps"] = [l for l in circ["laps"] if l["key"] != key]
        circ["laps"].append({"key": key, "year": year, "round": int(race["round"]), "gp": race["raceName"], "pos": int(x["position"]),
                             "code": d.get("code") or d["familyName"][:3].upper(), "name": f'{d["givenName"]} {d["familyName"]}',
                             "num": int(num), "team": c["name"], "color": colors.get(str(num)), "time": t, "sec": raw["sec"],
                             "layout": lid, "dt": STEP, "x": xs, "y": ys, "v": vs})
        got += 1
        print(f"  {year} R{race['round']} P{x['position']} {d.get('code')} {t}s layout {lid} shift {shift:+.2f}s")
    circ["laps"].sort(key=lambda l: (-l["year"], l["pos"]))
    return got

def due(year, idx, sched):
    """Rounds of a season worth trying now: qualifying over, not saved, not given up on or waiting to retry."""
    out = []
    for r in sched:
        k = f"{year}-{r['round']}"
        if k in idx["done"]: continue
        f = idx["fail"].get(k)
        if f and (f["tries"] >= 4 or B.NOW - when(f["at"]) < dt.timedelta(hours=RETRY_HOURS)): continue
        qs = next((x for x in r["sessions"] if x["name"] == "Qualifying"), None)
        if qs and B.parse(qs["end"]) + dt.timedelta(hours=WAIT_AFTER) <= B.NOW: out.append(r["round"])
    return out

def season(year, idx, deadline, sched=None):
    """Fetch every finished, missing weekend of one season. Returns False if it ran out of time."""
    sched = sched if sched is not None else (B.schedule(year) or [])
    if year < B.NOW.year and str(year) in idx["complete"]: return True
    if not due(year, idx, sched):
        if year < B.NOW.year and sched: idx["complete"].append(str(year))   # past season, nothing left to try
        return True
    sched = {r["round"]: r for r in sched}
    jq = B.jol_all(f"{year}/qualifying", "RaceTable", "QualifyingResults")
    sessions = of1(f"sessions?year={year}&session_name=Qualifying") or []
    for rnd in sorted(jq):
        k = f"{year}-{rnd}"
        if k in idx["done"]: continue
        f = idx["fail"].get(k)
        if f and (f["tries"] >= 4 or B.NOW - when(f["at"]) < dt.timedelta(hours=RETRY_HOURS)): continue
        s = sched.get(rnd); qs = next((x for x in (s or {}).get("sessions", []) if x["name"] == "Qualifying"), None)
        if not qs or B.parse(qs["end"]) + dt.timedelta(hours=WAIT_AFTER) > B.NOW: continue
        if time.time() > deadline: return False
        start = B.parse(qs["start"])
        # nearest OpenF1 qualifying within 2.5 days (sessions get moved: Brazil 2024 ran Sunday morning,
        # and Jolpica lists Las Vegas 2024 a day early)
        near = sorted((abs((when(x["date_start"]) - start).total_seconds()), i) for i, x in enumerate(sessions)
                      if x.get("date_start") and not x.get("is_cancelled"))
        m = sessions[near[0][1]] if near and near[0][0] < 2.5 * 86400 else None
        race = jq[rnd]; cid = race["Circuit"]["circuitId"]
        print(f"{year} R{rnd} {race['raceName']} ({cid})")
        n = 0
        if m:
            colors = {}
            for d in of1(f"drivers?session_key={m['session_key']}") or []:
                if d.get("team_colour"): colors[str(d.get("driver_number"))] = "#" + d["team_colour"].lstrip("#")
            circ = load(f"{cid}.json", None) or {"id": cid, "name": race["Circuit"]["circuitName"],
                    "loc": race["Circuit"]["Location"]["locality"], "country": race["Circuit"]["Location"]["country"],
                    "sf": None, "layouts": [], "laps": []}
            n = weekend(year, race, m, circ, colors)
            if n: save(f"{cid}.json", circ)
        if n:
            idx["done"][k] = {"circuit": cid, "laps": n}; idx["fail"].pop(k, None)
        else:
            idx["fail"][k] = {"tries": (f or {}).get("tries", 0) + 1, "at": B.NOW.isoformat(timespec="seconds")}
            print("  no laps saved, will retry later")
        write_index(idx)
    if year < B.NOW.year and jq:      # past rounds with no qualifying result at all (cancelled): stop asking
        for rnd in due(year, idx, list(sched.values())):
            if rnd not in jq:
                f = idx["fail"].get(f"{year}-{rnd}") or {}
                idx["fail"][f"{year}-{rnd}"] = {"tries": f.get("tries", 0) + 1, "at": B.NOW.isoformat(timespec="seconds")}
    return True

def write_index(idx):
    """index.json: the circuit list with lap details (no positions), small enough to load on page open."""
    circuits = []
    for fn in sorted(os.listdir(GDIR)) if os.path.isdir(GDIR) else []:
        if not fn.endswith(".json") or fn == "index.json": continue
        c = load(fn, None)
        if not c or not c.get("laps"): continue
        circuits.append({"id": c["id"], "name": c["name"], "loc": c["loc"], "country": c["country"],
                         "laps": [{k: l[k] for k in ("key", "year", "round", "gp", "pos", "code", "name", "team", "color", "time", "layout")} for l in c["laps"]]})
    idx["circuits"] = circuits
    save("index.json", idx)

def update(budget=150, years=None, sched_now=None):
    """Called by build.py (with this year's schedule, saving a request). Fetches missing weekends until
    the time budget (seconds) runs out. Past seasons are marked complete and not checked again."""
    deadline = time.time() + budget
    idx = load("index.json", {})
    idx.setdefault("done", {}); idx.setdefault("fail", {}); idx.setdefault("complete", [])
    before = json.dumps(idx, sort_keys=True)
    for y in years or range(B.NOW.year, FIRST - 1, -1):     # newest first, so a fresh qualifying lands quickly
        if not season(y, idx, deadline, sched_now if y == B.NOW.year else None): print("ghost: out of time, the rest next run"); break
    if json.dumps(idx, sort_keys=True) != before or "circuits" not in idx: write_index(idx)
    return sorted(c["id"] for c in idx.get("circuits", []))

if __name__ == "__main__":
    ys = [int(a) for a in sys.argv[1:] if a.isdigit()] or None
    update(budget=10 ** 6, years=ys)
    print("errors:", B.ERRS["openf1"][:5], B.ERRS["jolpica"][:5])
