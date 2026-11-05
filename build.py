"""F1 Season Tracker (unofficial fan project).
Runs on GitHub Actions (see .github/workflows/update.yml), or by hand: python build.py
1) pulls the season from Jolpica-F1 (schedule, results, qualifying, sprints, standings)
2) adds session info, weather and quick provisional results from OpenF1
3) works out title math and teammate battles
4) writes the four pages + data.json into _site/ for GitHub Pages
No API keys. No third-party packages (standard library only)."""
import json, os, sys, time, math, hashlib, datetime as dt, urllib.request, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_site")
JOLPICA = "https://api.jolpi.ca/ergast/f1"
OPENF1 = "https://api.openf1.org/v1"
UA = {"User-Agent": "f1-season-tracker (fan project)", "Accept": "application/json"}
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
if os.environ.get("F1_NOW"):   # testing only: pretend it's another time
    NOW = dt.datetime.fromisoformat(os.environ["F1_NOW"].replace("Z", "+00:00"))

RACE_MAX, SPRINT_MAX = 25, 8                  # most points one driver can score per race / sprint
TEAM_RACE_MAX, TEAM_SPRINT_MAX = 25 + 18, 8 + 7  # a 1-2 finish
DUR = {"Practice 1": 60, "Practice 2": 60, "Practice 3": 60, "Sprint Qualifying": 45,
       "Sprint": 60, "Qualifying": 60, "Race": 120}   # minutes, used to mark sessions live/done
ERRS = {"jolpica": [], "openf1": []}
_last = [0.0]

def get(url, src, tries=3):
    """GET JSON. Jolpica allows about 4 requests a second, so space calls out."""
    for i in range(tries):
        wait = 0.3 - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            if e.code == 404: return None
            ERRS[src].append(f"{e.code} {url}"); return None
        except Exception as e:
            if i < tries - 1: time.sleep(3); continue
            ERRS[src].append(f"{type(e).__name__} {url}"); return None

def jol_all(path, table, key):
    """All pages of a Jolpica endpoint (max 100 rows a page), races merged by round."""
    races, off, total = {}, 0, None
    while total is None or off < total:
        d = get(f"{JOLPICA}/{path}/?limit=100&offset={off}", "jolpica")
        if not d: break
        m = d["MRData"]; total = int(m.get("total", 0))
        for r in m[table]["Races"]:
            races.setdefault(int(r["round"]), {**{k: v for k, v in r.items() if k != key}, key: []})[key].extend(r.get(key, []))
        off += 100
        if off > 2000: break
    return races

def iso(date, t):
    if not date: return None
    return f"{date}T{(t or '12:00:00Z').rstrip('Z')}+00:00"

def parse(s): return dt.datetime.fromisoformat(s) if s else None

# ---------------- schedule ----------------
def schedule(year):
    d = get(f"{JOLPICA}/{year}/races/?limit=100", "jolpica")
    if not d: return None
    out = []
    for r in d["MRData"]["RaceTable"]["Races"]:
        ss = []
        for k, name in (("FirstPractice", "Practice 1"), ("SecondPractice", "Practice 2"), ("ThirdPractice", "Practice 3"),
                        ("SprintQualifying", "Sprint Qualifying"), ("Sprint", "Sprint"), ("Qualifying", "Qualifying")):
            if k in r: ss.append({"name": name, "start": iso(r[k]["date"], r[k].get("time"))})
        ss.append({"name": "Race", "start": iso(r["date"], r.get("time"))})
        ss.sort(key=lambda s: s["start"])
        for s in ss: s["end"] = (parse(s["start"]) + dt.timedelta(minutes=DUR[s["name"]])).isoformat()
        c = r["Circuit"]
        out.append({"round": int(r["round"]), "name": r["raceName"], "season": int(r["season"]),
                    "circuit": {"id": c["circuitId"], "name": c["circuitName"], "locality": c["Location"]["locality"],
                                "country": c["Location"]["country"]},
                    "sprint": "Sprint" in r, "start": ss[-1]["start"], "sessions": ss})
    return out

# ---------------- results ----------------
def driver_row(x, kind="race"):
    d, c = x["Driver"], x["Constructor"]
    row = {"pos": int(x["position"]), "pt": x.get("positionText", x["position"]), "id": d["driverId"],
           "code": d.get("code") or d["familyName"][:3].upper(), "name": f'{d["givenName"]} {d["familyName"]}',
           "num": d.get("permanentNumber") or x.get("number"), "team": c["name"], "teamId": c["constructorId"],
           "pts": float(x.get("points", 0) or 0)}
    if kind == "quali":
        row["q"] = x.get("Q3") or x.get("Q2") or x.get("Q1") or ""
        return row
    st = x.get("status", "")
    row.update({"grid": int(x.get("grid") or 0), "laps": int(x.get("laps") or 0), "status": st,
                "time": (x.get("Time") or {}).get("time"),
                "fl": (x.get("FastestLap") or {}).get("rank") == "1",
                "flTime": ((x.get("FastestLap") or {}).get("Time") or {}).get("time"),
                "dnf": not (st == "Finished" or st == "Lapped" or st.startswith("+"))})
    return row

def season_results(year):
    res = jol_all(f"{year}/results", "RaceTable", "Results")
    q = jol_all(f"{year}/qualifying", "RaceTable", "QualifyingResults")
    sp = jol_all(f"{year}/sprint", "RaceTable", "SprintResults")
    races = []
    for rnd in sorted(res):
        r = res[rnd]
        rows = sorted((driver_row(x) for x in r["Results"]), key=lambda z: z["pos"])
        races.append({"round": rnd, "name": r["raceName"], "date": iso(r["date"], r.get("time")),
                      "circuit": r["Circuit"]["circuitName"], "results": rows,
                      "quali": sorted((driver_row(x, "quali") for x in q.get(rnd, {}).get("QualifyingResults", [])), key=lambda z: z["pos"]),
                      "sprint": sorted((driver_row(x) for x in sp.get(rnd, {}).get("SprintResults", [])), key=lambda z: z["pos"])})
    return races

def standings(year):
    out = {"drivers": [], "teams": [], "round": 0}
    d = get(f"{JOLPICA}/{year}/driverstandings/?limit=100", "jolpica")
    try:
        sl = d["MRData"]["StandingsTable"]["StandingsLists"][0]; out["round"] = int(sl["round"])
        for x in sl["DriverStandings"]:
            dr, cs = x["Driver"], x.get("Constructors") or [{}]
            out["drivers"].append({"pos": int(x.get("position") or x.get("positionText", "0").replace("-", "0") or 0), "id": dr["driverId"],
                                   "code": dr.get("code") or dr["familyName"][:3].upper(), "name": f'{dr["givenName"]} {dr["familyName"]}',
                                   "num": dr.get("permanentNumber"), "team": cs[-1].get("name", ""), "teamId": cs[-1].get("constructorId", ""),
                                   "pts": float(x["points"]), "wins": int(x["wins"])})
    except Exception: pass
    c = get(f"{JOLPICA}/{year}/constructorstandings/?limit=100", "jolpica")
    try:
        for x in c["MRData"]["StandingsTable"]["StandingsLists"][0]["ConstructorStandings"]:
            out["teams"].append({"pos": int(x.get("position") or 0), "id": x["Constructor"]["constructorId"],
                                 "name": x["Constructor"]["name"], "pts": float(x["points"]), "wins": int(x["wins"])})
    except Exception: pass
    return out

# ---------------- title math ----------------
def fmt_pts(p): return str(int(p)) if float(p).is_integer() else f"{p:g}"

def title_math(rows, races_left, sprints_left, rmax, smax, who, next_sprint=False):
    """Who can still win, and what has to happen. rows sorted by points, desc."""
    if not rows: return None
    max_left = races_left * rmax + sprints_left * smax
    lead = rows[0]; out = {"maxLeft": max_left, "racesLeft": races_left, "sprintsLeft": sprints_left, "rows": []}
    alive = []
    for r in rows:
        gap = lead["pts"] - r["pts"]
        best = r["pts"] + max_left
        if r is lead: state = "leader"
        elif gap > max_left: state = "out"
        elif gap == max_left: state = "tie"     # can only tie: countback (most wins, then 2nds...) decides
        else: state = "alive"
        if state in ("alive", "tie"): alive.append(r)
        per = None
        if state in ("alive", "tie") and races_left + sprints_left:
            per = math.ceil((gap + (0 if state == "tie" else 1)) / max(1, races_left))
        out["rows"].append({"id": r.get("id"), "name": r.get("name"), "pts": r["pts"], "gap": gap, "best": best, "state": state, "perRace": per})
    out["clinched"] = not alive and len(rows) > 1
    out["leader"] = lead.get("name"); out["leaderPts"] = lead["pts"]
    if out["clinched"]:
        out["headline"] = f"{lead['name']} {'is the champion' if who == 'driver' else 'are the champions'}" + (" (season over)" if not (races_left + sprints_left) else ", mathematically locked")
        out["steps"] = [f"Nobody can reach {fmt_pts(lead['pts'])} points with {fmt_pts(max_left)} left on the table."]
    else:
        rival = alive[0]; gap = lead["pts"] - rival["pts"]
        one = rmax + (smax if next_sprint else 0)          # most a leader can gain at the next weekend
        after = max_left - one
        need = max(0, after - gap + 1)
        out["headline"] = f"{len(alive) + 1} still in it. {lead['name']} leads {rival['name']} by {fmt_pts(gap)}."
        steps = []
        if races_left == 0:
            steps.append("No races left.")
        else:
            if need <= one:
                steps.append(f"{lead['name']} clinches at the next race weekend by outscoring {rival['name']}"
                             + (f" (and everyone else still in it)" if len(alive) > 1 else "") + f" by {fmt_pts(need)} or more.")
            else:
                steps.append(f"{lead['name']} can't clinch at the next race weekend: the most he can gain there is {fmt_pts(one)}, and he needs {fmt_pts(need)}.")
            for a in alive:
                g = lead["pts"] - a["pts"]
                steps.append(f"{a['name']} needs to outscore {lead['name']} by {fmt_pts(g + 1)} over {races_left} race{'s' if races_left != 1 else ''}"
                             + (f" and {sprints_left} sprint{'s' if sprints_left != 1 else ''}" if sprints_left else "")
                             + f": about {math.ceil((g + 1) / races_left)} a race. If {lead['name']} scores "
                             + f"{fmt_pts(max(0, a['pts'] + max_left - lead['pts']) + 1)}+ more, it's over for {a['name']}.")
        out["steps"] = steps
    # position fights below the top (who can still catch whom), most useful once the title is settled
    fights = []
    for i in range(1, min(len(rows), 6)):
        a, b = rows[i - 1], rows[i]
        g = a["pts"] - b["pts"]
        fights.append({"pos": i, "ahead": a.get("name"), "behind": b.get("name"), "gap": g, "locked": g > max_left})
    out["fights"] = fights
    return out

# ---------------- teammate battles ----------------
def teammates(races, std):
    teams = {}
    order = {t["id"]: t["pos"] for t in std.get("teams", [])}
    for r in races:
        for row in r["results"]:
            t = teams.setdefault(row["teamId"], {"id": row["teamId"], "name": row["team"], "drivers": {}})
            d = t["drivers"].setdefault(row["id"], {"id": row["id"], "code": row["code"], "name": row["name"], "num": row["num"], "races": 0, "pts": 0.0})
            d["races"] += 1; d["pts"] += row["pts"]
        for row in r.get("sprint", []):
            t = teams.get(row["teamId"])
            if t and row["id"] in t["drivers"]: t["drivers"][row["id"]]["pts"] += row["pts"]
    out = []
    for t in teams.values():
        ds = sorted(t["drivers"].values(), key=lambda d: (-d["races"], -d["pts"]))
        a, b = (ds + [None, None])[:2]
        if not b: continue
        battle = {"id": t["id"], "name": t["name"], "pos": order.get(t["id"], 99), "a": a, "b": b,
                  "others": [d for d in ds[2:]], "rounds": [], "q": [0, 0], "r": [0, 0], "rBoth": 0, "pts": [a["pts"], b["pts"]]}
        for r in races:
            byid = {x["id"]: x for x in r["results"]}
            qid = {x["id"]: x for x in r["quali"]}
            ra, rb = byid.get(a["id"]), byid.get(b["id"])
            if not (ra and rb and ra["teamId"] == t["id"] and rb["teamId"] == t["id"]): continue
            qa, qb = qid.get(a["id"]), qid.get(b["id"])
            rec = {"round": r["round"], "name": r["name"], "qa": qa["pos"] if qa else None, "qb": qb["pos"] if qb else None,
                   "fa": ra["pt"], "fb": rb["pt"], "pa": ra["pts"], "pb": rb["pts"], "q": None, "r": None}
            if qa and qb:
                rec["q"] = 0 if qa["pos"] < qb["pos"] else 1; battle["q"][rec["q"]] += 1
            if not ra["dnf"] and not rb["dnf"]:   # race H2H only counts when both are classified
                rec["r"] = 0 if ra["pos"] < rb["pos"] else 1; battle["r"][rec["r"]] += 1; battle["rBoth"] += 1
            battle["rounds"].append(rec)
        n = battle["q"][0] + battle["q"][1] + battle["r"][0] + battle["r"][1]
        lead = 0 if battle["q"][0] + battle["r"][0] >= battle["q"][1] + battle["r"][1] else 1
        battle["dom"] = round(100 * (battle["q"][lead] + battle["r"][lead]) / n) if n else 50
        battle["leader"] = lead
        out.append(battle)
    out.sort(key=lambda x: x["pos"])
    return out

# ---------------- OpenF1 extras ----------------
def openf1_extras(year, nxt):
    """Circuit info, team colours, weather and quick results. All optional: pages work without them."""
    ex = {"colors": {}, "meeting": None, "weather": None, "provisional": None, "sessionKeys": {}}
    drv = get(f"{OPENF1}/drivers?session_key=latest", "openf1") or []
    for x in drv if isinstance(drv, list) else []:
        if x.get("team_colour"): ex["colors"][str(x.get("driver_number"))] = "#" + x["team_colour"].lstrip("#")
        if x.get("team_colour") and x.get("team_name"): ex["colors"]["team:" + x["team_name"].lower()] = "#" + x["team_colour"].lstrip("#")
    if not nxt: return ex
    meets = get(f"{OPENF1}/meetings?year={nxt['season']}", "openf1") or []
    start = parse(nxt["sessions"][0]["start"])
    for m in meets if isinstance(meets, list) else []:
        ms = parse(m.get("date_start"))
        if ms and abs((ms - start).total_seconds()) < 3 * 86400:
            ex["meeting"] = {k: m.get(k) for k in ("meeting_key", "meeting_name", "circuit_short_name", "circuit_type", "gmt_offset", "location", "country_name")}
            break
    if ex["meeting"]:
        ss = get(f"{OPENF1}/sessions?meeting_key={ex['meeting']['meeting_key']}", "openf1") or []
        ex["openf1Sessions"] = [{"name": s.get("session_name"), "start": s.get("date_start"), "end": s.get("date_end"), "key": s.get("session_key")} for s in ss] if isinstance(ss, list) else []
        weekend = parse(nxt["sessions"][0]["start"]) - dt.timedelta(hours=2) <= NOW <= parse(nxt["sessions"][-1]["end"]) + dt.timedelta(hours=6)
        if weekend:
            w = get(f"{OPENF1}/weather?meeting_key={ex['meeting']['meeting_key']}", "openf1")
            if isinstance(w, list) and w:
                x = w[-1]
                ex["weather"] = {"air": x.get("air_temperature"), "track": x.get("track_temperature"), "rain": x.get("rainfall"),
                                 "wind": x.get("wind_speed"), "hum": x.get("humidity"), "at": x.get("date")}
            # newest finished session: quick results from OpenF1 before Jolpica posts them
            done = [s for s in ex["openf1Sessions"] if s.get("end") and parse(s["end"]) <= NOW]
            if done:
                s = done[-1]
                res = get(f"{OPENF1}/session_result?session_key={s['key']}", "openf1")
                names = get(f"{OPENF1}/drivers?session_key={s['key']}", "openf1") or []
                nm = {str(x.get("driver_number")): x for x in names} if isinstance(names, list) else {}
                if isinstance(res, list) and res:
                    rows = []
                    for x in sorted(res, key=lambda z: (z.get("position") is None, z.get("position") or 99)):
                        n = nm.get(str(x.get("driver_number")), {})
                        rows.append({"pos": x.get("position"), "num": x.get("driver_number"), "code": n.get("name_acronym"),
                                     "name": n.get("full_name", "").title() if n.get("full_name") else None, "team": n.get("team_name"),
                                     "laps": x.get("number_of_laps"), "gap": x.get("gap_to_leader"), "dnf": x.get("dnf"), "dns": x.get("dns"), "dsq": x.get("dsq"),
                                     "best": x.get("duration")})
                    ex["provisional"] = {"session": s["name"], "end": s["end"], "rows": rows}
    return ex

def last_year(nxt):
    """Last year's race at this circuit: winner, pole, fastest lap, laps."""
    if not nxt: return None
    y, cid = nxt["season"] - 1, nxt["circuit"]["id"]
    out = {"year": y}
    d = get(f"{JOLPICA}/{y}/circuits/{cid}/results/?limit=30", "jolpica")
    try:
        r = d["MRData"]["RaceTable"]["Races"][0]["Results"]
        w = r[0]; out["winner"] = f'{w["Driver"]["givenName"]} {w["Driver"]["familyName"]}'; out["winnerTeam"] = w["Constructor"]["name"]
        out["laps"] = int(w["laps"]); out["time"] = (w.get("Time") or {}).get("time")
        fl = [x for x in r if (x.get("FastestLap") or {}).get("rank") == "1"]
        if fl: out["fl"] = f'{fl[0]["Driver"]["familyName"]} {fl[0]["FastestLap"]["Time"]["time"]}'
    except Exception: return None
    q = get(f"{JOLPICA}/{y}/circuits/{cid}/qualifying/1/", "jolpica")
    try:
        p = q["MRData"]["RaceTable"]["Races"][0]["QualifyingResults"][0]
        out["pole"] = f'{p["Driver"]["givenName"]} {p["Driver"]["familyName"]}'; out["poleTime"] = p.get("Q3") or p.get("Q2") or p.get("Q1")
    except Exception: pass
    return out

# ---------------- main ----------------
def main():
    y = NOW.year
    sched = schedule(y) or []
    if sched and all(parse(r["sessions"][-1]["end"]) < NOW for r in sched):
        nxt_sched = schedule(y + 1) or []
    else:
        nxt_sched = []
    upcoming = [r for r in sched + nxt_sched if parse(r["sessions"][-1]["end"]) + dt.timedelta(hours=3) > NOW]
    nxt = upcoming[0] if upcoming else None

    # results season: this year's, until next year's first race has actually run
    rs_year = y
    if nxt_sched and nxt and nxt["season"] == y + 1 and nxt["round"] > 1:
        rs_year = y + 1
    if not sched and not nxt_sched: rs_year = y - 1   # calendar missing entirely: fall back to last season
    races = season_results(rs_year)
    if not races and rs_year == y:   # very start of a new year before round 1: show last season as final
        prev = season_results(y - 1)
        if prev: races, rs_year = prev, y - 1
    std = standings(rs_year)
    full = schedule(rs_year) if rs_year != y else sched
    full = full or []
    done_rounds = {r["round"] for r in races}
    left = [r for r in full if r["round"] not in done_rounds and r["round"] > std.get("round", 0)]
    races_left = len(left)
    sprints_left = 0
    for r in left:
        if r["sprint"]:
            sp_done = any(rr["round"] == r["round"] and rr["sprint"] for rr in races)
            if not sp_done: sprints_left += 1
    tm_d = title_math(sorted(std["drivers"], key=lambda r: -r["pts"]), races_left, sprints_left, RACE_MAX, SPRINT_MAX, "driver", bool(left and left[0]["sprint"]))
    tm_t = title_math([{"id": t["id"], "name": t["name"], "pts": t["pts"]} for t in sorted(std["teams"], key=lambda r: -r["pts"])],
                      races_left, sprints_left, TEAM_RACE_MAX, TEAM_SPRINT_MAX, "team", bool(left and left[0]["sprint"]))
    try:   # a season that just finished gets saved for the History tab (the workflow commits it)
        import fetch_history
        done = rs_year if races_left == 0 and races else rs_year - 1
        for a in fetch_history.ensure([done, done - 1]): print(f"OK saved {a} to history")
    except Exception as e: print(f"NOTE history check skipped ({e})")
    ex = openf1_extras(y, nxt)
    data = {
        "season": rs_year, "nextSeason": nxt["season"] if nxt else None,
        "schedule": full, "next": nxt, "nextYearSchedule": nxt_sched if nxt and nxt["season"] != rs_year else [],
        "lastYear": last_year(nxt), "meeting": ex.get("meeting"), "openf1Sessions": ex.get("openf1Sessions"),
        "weather": ex.get("weather"), "provisional": ex.get("provisional"), "colors": ex.get("colors"),
        "standings": std, "title": {"drivers": tm_d, "teams": tm_t},
        "results": races, "teammates": teammates(races, std),
        "racesLeft": races_left, "sprintsLeft": sprints_left,
        "stories": load_stories(),
    }
    if data["provisional"] and nxt and any(r["round"] == nxt["round"] for r in races) and data["provisional"]["session"] == "Race":
        data["provisional"] = None   # Jolpica already has the official result
    ok_j = not ERRS["jolpica"] and bool(std["drivers"] or full)
    data["status"] = {"jolpica": "ok" if ok_j else "error", "openf1": "ok" if not ERRS["openf1"] else "error",
                      "checked": NOW.isoformat(timespec="seconds"), "errors": (ERRS["jolpica"] + ERRS["openf1"])[:6]}
    data["cadence"] = cadence(nxt)
    write_site(data)
    print(f"OK season {rs_year}, {len(races)} races, next: {nxt['name'] if nxt else 'none'}, errors: {len(ERRS['jolpica'])} jolpica / {len(ERRS['openf1'])} openf1")
    return 0 if ok_j else 1

def load_stories():
    """Researched headlines for the History tab (templates/stories.json), keyed by season."""
    try:
        d = json.load(open(os.path.join(HERE, "templates", "stories.json"), encoding="utf-8"))
        return {k: v for k, v in d.items() if k.isdigit()}
    except Exception as e:
        print(f"NOTE stories.json not loaded ({e})"); return {}

def cadence(nxt):
    """Minutes between updates: every 10 on race weekends, hourly otherwise (the workflow follows this)."""
    if nxt and parse(nxt["sessions"][0]["start"]) - dt.timedelta(hours=12) <= NOW <= parse(nxt["sessions"][-1]["end"]) + dt.timedelta(hours=12):
        return 10
    return 60

def write_site(data):
    tpl = open(os.path.join(HERE, "templates", "page.html"), encoding="utf-8").read()
    os.makedirs(OUT, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    pages = {"index.html": ("next", "Next Race"), "standings.html": ("standings", "Standings"),
             "results.html": ("results", "Results"), "teammates.html": ("teammates", "Teammate Battles"),
             "history.html": ("history", "History")}
    for fn, (pid, title) in pages.items():
        html = tpl.replace("__PAGE__", pid).replace("__TITLE__", title).replace("__DATA__", blob)
        open(os.path.join(OUT, fn), "w", encoding="utf-8", newline="\n").write(html)
    open(os.path.join(OUT, "data.json"), "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    hist = os.path.join(HERE, "history")   # saved finished seasons for the History tab
    if os.path.isdir(hist):
        os.makedirs(os.path.join(OUT, "history"), exist_ok=True)
        for f in os.listdir(hist):
            if f.endswith(".json"): open(os.path.join(OUT, "history", f), "wb").write(open(os.path.join(hist, f), "rb").read())
    for f in ("logo.svg",):
        src = os.path.join(HERE, "templates", f)
        if os.path.exists(src): open(os.path.join(OUT, f), "wb").write(open(src, "rb").read())
    open(os.path.join(OUT, ".nojekyll"), "w").write("")
    stable = {k: v for k, v in data.items() if k not in ("status", "weather")}
    open(os.path.join(OUT, "fingerprint.txt"), "w").write(hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest())

def gate():
    """Called by the workflow every 10 min. Prints run=true when an update is due:
    always on race weekends (cadence 10), otherwise once the live data is 55+ min old."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    due, why = True, "no live data yet"
    if "/" in repo:
        user, name = repo.split("/", 1)
        d = get(f"https://{user.lower()}.github.io/{name}/data.json?t={int(time.time())}", "openf1", tries=1)
        if d and d.get("status", {}).get("checked"):
            age = (NOW - parse(d["status"]["checked"])).total_seconds() / 60
            cad = d.get("cadence", 60)
            nxt = d.get("next") or {}
            soon = bool(nxt.get("sessions")) and parse(nxt["sessions"][0]["start"]) - dt.timedelta(hours=13) <= NOW
            due = cad <= 10 or soon or age >= 55
            why = f"cadence {cad}, last update {age:.0f} min ago" + (", weekend starting" if soon else "")
    print(f"{'Update due' if due else 'Skipping'}: {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out: open(out, "a").write(f"run={'true' if due else 'false'}\n")
    return 0

if __name__ == "__main__":
    if "--gate" in sys.argv: sys.exit(gate())
    sys.exit(main())
