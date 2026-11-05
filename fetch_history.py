"""Saves finished F1 seasons to history/<year>.json so the History tab opens instantly.
Finished seasons never change, so each one is fetched once and kept.
  python fetch_history.py            fill in every missing season (slow on purpose: about 1 hour for all of them)
  python fetch_history.py 2025       just one season
build.py also calls ensure() each run, so a season that just ended gets added on its own."""
import json, os, sys, time, datetime as dt, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "history")
API = "https://api.jolpi.ca/ergast/f1"
UA = {"User-Agent": "f1-season-tracker (fan project)", "Accept": "application/json"}
_last = [0.0]

def get(path, gap):
    """Jolpica allows about 500 requests an hour, so wait `gap` seconds between calls."""
    for i in range(6):
        w = gap - (time.time() - _last[0])
        if w > 0: time.sleep(w)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{API}/{path}", headers=UA), timeout=30) as r:
                return json.loads(r.read())["MRData"]
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(60 * (i + 1)); continue
            if e.code >= 500: time.sleep(10); continue
            raise
        except Exception:
            time.sleep(10)
    raise RuntimeError(f"gave up on {path}")

def pages(path, key, gap):
    races, off, total = {}, 0, 1
    while off < total:
        m = get(f"{path}/?limit=100&offset={off}", gap); total = int(m["total"])
        for r in m["RaceTable"]["Races"]:
            races.setdefault(r["round"], {**{k: r[k] for k in ("round", "raceName", "date")}, key: []})[key].extend(r.get(key, []))
        off += 100
    return [races[k] for k in sorted(races, key=int)]

def drv(d): return {k: d.get(k) for k in ("givenName", "familyName", "code") if d.get(k)}

def season(y, gap=7.5):
    ds = get(f"{y}/driverstandings/?limit=100", gap)["StandingsTable"]["StandingsLists"]
    cs = get(f"{y}/constructorstandings/?limit=100", gap)["StandingsTable"]["StandingsLists"]
    races = pages(f"{y}/results", "Results", gap)
    sprints = pages(f"{y}/sprint", "SprintResults", gap) if y >= 2021 else []
    out = {
        "ds": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [
            {"position": x.get("position"), "positionText": x.get("positionText"), "points": x["points"], "wins": x["wins"],
             "Driver": drv(x["Driver"]), "Constructors": [{"name": c["name"]} for c in x.get("Constructors", [])[-1:]]}
            for x in ds[0]["DriverStandings"]]}] if ds else []}},
        "cs": {"StandingsTable": {"StandingsLists": [{"ConstructorStandings": [
            {"position": x.get("position"), "points": x["points"], "wins": x["wins"], "Constructor": {"name": x["Constructor"]["name"]}}
            for x in cs[0]["ConstructorStandings"]]}] if cs else []}},
        "races": [{"round": r["round"], "raceName": r["raceName"], "date": r["date"], "Results": [
            {"position": x["position"], "grid": x.get("grid"), "points": x.get("points", "0"), "status": x.get("status", ""),
             **({"Time": {"millis": x["Time"]["millis"]}} if (x.get("Time") or {}).get("millis") else {}),
             "Driver": drv(x["Driver"]), "Constructor": {"name": x["Constructor"]["name"]}} for x in r["Results"]]} for r in races],
        "sprints": [{"round": s["round"], "SprintResults": [{"points": x.get("points", "0"), "Driver": drv(x["Driver"])} for x in s["SprintResults"]]} for s in sprints],
    }
    if not out["races"] or not out["ds"]["StandingsTable"]["StandingsLists"]:
        raise RuntimeError(f"{y}: no results yet")
    return out

def save(y, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = os.path.join(DIR, f"{y}.json.tmp")
    open(tmp, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, os.path.join(DIR, f"{y}.json"))

def have(y): return os.path.exists(os.path.join(DIR, f"{y}.json"))

def ensure(years, gap=0.3):
    """Add any of these finished seasons that are missing. Used by build.py (fast: only new seasons)."""
    added = []
    for y in years:
        if have(y): continue
        try: save(y, season(y, gap)); added.append(y)
        except Exception as e: print(f"NOTE history {y} not saved ({e})")
    return added

if __name__ == "__main__":
    last = dt.date.today().year - 1
    years = [int(a) for a in sys.argv[1:]] or list(range(last, 1949, -1))
    for y in years:
        if have(y) and len(sys.argv) == 1: continue
        t = time.time()
        save(y, season(y))
        print(f"OK {y} saved ({time.time() - t:.0f}s)", flush=True)
    print("DONE", flush=True)
