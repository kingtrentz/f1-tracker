"""Push alerts for the installed F1 Tracker app (Home Screen web app on iPhone, mirrors to Apple Watch).

Three alerts, each a toggle on the page: session starting (about 15 min before), results in, major news.
Practice sessions only alert for people who tick "Include practice".

How it fits together:
- The page subscribes the phone (standard Web Push) and seals the subscription with the site's public key
  (ECDH P-256 + HKDF + AES-GCM), then posts it to an ntfy.sh topic. Anyone can read that topic, nobody can open it.
- Every build pulls the topic (ntfy keeps 12 hours), opens new messages with VAPID_PRIVATE_KEY (GitHub secret),
  and keeps subscriptions in push/subs.enc, encrypted with a key derived from the same secret.
- push/state.json (plain) remembers which alerts were already sent so nothing goes out twice.
- No secret set: everything here is skipped and the page hides the Alerts box.
Logs never print endpoints or keys (Actions logs on a public repo are public)."""
import base64, json, os, re, secrets, time, datetime as dt, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "push")
STATE = os.path.join(DIR, "state.json")
SUBS = os.path.join(DIR, "subs.enc")
TOPIC = "f1-tracker-alerts-7b2e94c1d8"        # also put into the page by build.py (__ALERTS__)
SITE = "https://kingtrentz.github.io/f1-tracker/"
SUBJECT = os.environ.get("VAPID_SUB") or "https://kingtrentz.github.io"   # VAPID contact: https origin (no path) or a clean mailto:
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")
MAX_SUBS = 200
NEWS_MIN_OUTLETS, NEWS_PER_DAY, NEWS_MAX_AGE_H = 3, 2, 18
START_BEFORE, START_AFTER = 25, 15             # minutes around a session start when the start alert may go out
RESULT_SESSIONS = ("Qualifying", "Sprint Qualifying", "Sprint", "Race")
CT = dt.timezone(dt.timedelta(hours=-6))

def b64u(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def unb64u(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def _key():
    pem = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip().replace("\\n", "\n")
    if not pem: return None
    from cryptography.hazmat.primitives import serialization
    try: return serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as e:
        print(f"NOTE alerts off: VAPID_PRIVATE_KEY isn't a readable key ({type(e).__name__})"); return None

KEY = _key()

def public_key():
    """Base64url public key for the page (applicationServerKey + sealing). Empty when alerts aren't set up."""
    if not KEY: return ""
    from cryptography.hazmat.primitives import serialization as s
    return b64u(KEY.public_key().public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint))

def _hkdf(material, info):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(material)

def unseal(msg):
    """Opens a message sealed by the page: b64u(ephemeral pub) . b64u(iv) . b64u(ciphertext)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    epk, iv, ct = (unb64u(p) for p in msg.strip().split("."))
    shared = KEY.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk))
    return json.loads(AESGCM(_hkdf(shared, b"f1-tracker seal")).decrypt(iv, ct, None))

def _store_key():
    return _hkdf(KEY.private_numbers().private_value.to_bytes(32, "big"), b"f1-tracker subs store")

def load_subs():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try: raw = open(SUBS, "rb").read()
    except FileNotFoundError: return {}
    try: return json.loads(AESGCM(_store_key()).decrypt(raw[:12], raw[12:], None))
    except Exception:
        print("NOTE alerts: push/subs.enc couldn't be opened (key changed?), starting a fresh list"); return {}

def save_subs(subs):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    os.makedirs(DIR, exist_ok=True)
    open(SUBS, "wb").write(iv + AESGCM(_store_key()).encrypt(iv, json.dumps(subs, sort_keys=True).encode(), None))

def load_state():
    try: return json.load(open(STATE, encoding="utf-8"))
    except Exception: return {}

def save_state(st):
    os.makedirs(DIR, exist_ok=True)
    new = json.dumps(st, indent=1, sort_keys=True)
    try: old = open(STATE, encoding="utf-8").read()
    except Exception: old = ""
    if new != old: open(STATE, "w", encoding="utf-8", newline="\n").write(new)

def ok_endpoint(u):
    m = re.fullmatch(r"https://([a-z0-9.-]+)(/[!-~]*)?", u or "")
    return bool(m) and len(u) < 600 and any(m.group(1) == h or m.group(1).endswith("." + h) for h in PUSH_HOSTS)

def clean_prefs(p):
    p = p if isinstance(p, dict) else {}
    return {k: bool(p.get(k, d)) for k, d in (("start", True), ("res", True), ("news", True), ("prac", False))}

def pull():
    url = f"https://ntfy.sh/{TOPIC}/json?poll=1&since=12h"
    req = urllib.request.Request(url, headers={"User-Agent": "f1-tracker (fan project)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        lines = r.read().decode("utf-8", "replace").splitlines()
    return [m for m in (json.loads(l) for l in lines if l.strip()) if m.get("event") == "message"]

def send(sub, payload, ttl=6 * 3600, urgency="normal"):
    """True sent, False failed, None = subscription is gone (remove it)."""
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid02     # RFC 8292 header: Authorization: vapid t=..., k=...
    v = Vapid02(private_key=KEY)
    try:
        webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=v, vapid_claims={"sub": SUBJECT},
                ttl=ttl, headers={"Urgency": urgency}, timeout=15)
        return True
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 410): return None
        reason = ""
        try: reason = (e.response.text or "")[:80]
        except Exception: pass
        reason = re.sub(r"[^A-Za-z0-9 :{}_\".,-]", "", reason)
        print(f"NOTE alert not delivered (HTTP {code} {reason})"); return False
    except Exception as e:
        print(f"NOTE alert not delivered ({type(e).__name__})"); return False

def short_gp(name): return re.sub(r"\s*Grand Prix$", " GP", name or "").strip()
def last(r): return (r.get("name") or "").split(" ")[-1] or r.get("code") or "?"
def sess_short(n): return {"Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3", "Sprint Qualifying": "Sprint quali"}.get(n, n)

def result_alert(gp, s):
    rows = [r for r in s["rows"] if r.get("pos")]
    if not rows: return None
    p1, rest = rows[0], rows[1:3]
    tail = ", ".join(f"P{r['pos']} {last(r)}" for r in rest)
    prov = "" if s.get("official") or s["name"].startswith("Practice") else " (provisional)"
    n = s["name"]
    title = {"Race": f"{last(p1)} wins the {gp}", "Sprint": f"{last(p1)} wins the {gp} sprint",
             "Qualifying": f"{last(p1)} on pole for the {gp}", "Sprint Qualifying": f"{last(p1)} on sprint pole in {gp.replace(' GP', '')}"}.get(n)
    if not title: title = f"{last(p1)} tops {sess_short(n)} in {gp.replace(' GP', '')}"
    return {"title": title, "body": (tail + prov).strip() or "Results are in", "url": "index.html", "tag": f"res-{n}"}

def events(data, now):
    """Every alert that could go out right now: (key, pref, payload). Sending is deduped by key."""
    out = []
    nxt = data.get("next") or {}
    for s in nxt.get("sessions") or []:
        st = dt.datetime.fromisoformat(s["start"])
        mins = (st - now).total_seconds() / 60
        if -START_AFTER <= mins <= START_BEFORE:
            gp = short_gp(nxt.get("name"))
            when = f"Starts in {round(mins)} min" if mins >= 2 else "Underway now"
            local = st.astimezone(CT).strftime("%-I:%M %p CT")
            pref = "prac" if s["name"].startswith("Practice") else "start"
            out.append((f"start:{nxt.get('season')}-{nxt.get('round')}-{s['name']}", pref,
                        {"title": f"{gp}: {sess_short(s['name'])}", "body": f"{when} ({local})", "url": "index.html", "tag": "start"}))
    wk = data.get("weekend") or {}
    for s in wk.get("sessions") or []:
        if s.get("grid") or not s.get("rows"): continue
        if s["name"] not in RESULT_SESSIONS and not s["name"].startswith("Practice"): continue
        a = result_alert(short_gp(wk.get("name")), s)
        if a: out.append((f"res:{wk.get('season')}-{wk.get('round')}-{s['name']}", "prac" if s["name"].startswith("Practice") else "res", a))
    race_day = False
    for s in (wk.get("sessions") or []):
        if s["name"] in ("Race", "Sprint"):
            st = dt.datetime.fromisoformat(s["start"])
            if st - dt.timedelta(hours=1) <= now <= st + dt.timedelta(hours=6): race_day = True
    nd = data.get("news") or {}
    if not race_day:
        stories = [x for v in (nd.get("rounds") or {}).values() for x in v]
        for x in sorted(stories, key=lambda x: (x["n"], x["k"]), reverse=True):
            if x.get("n", 0) < NEWS_MIN_OUTLETS or x.get("k", 0) < NEWS_MIN_OUTLETS: continue
            try: age = (now - dt.datetime.fromisoformat(x["at"])).total_seconds() / 3600
            except Exception: continue
            if age > NEWS_MAX_AGE_H: continue
            out.append((f"news:{x['u']}", "news", {"title": "F1 news", "body": x["t"][:180], "url": x["u"], "tag": "news"}))
    return out

def run(data, now):
    if not KEY: return
    st = load_state()
    subs = load_subs()
    changed = False
    # 1) new or changed subscriptions from the page
    try: msgs = pull()
    except Exception as e: msgs = []; print(f"NOTE alerts: signups not pulled ({type(e).__name__})")
    seen = set(st.get("seen", []))
    welcome = []
    for m in sorted(msgs, key=lambda m: m.get("time", 0)):
        mid = m.get("id", "")
        if mid in seen: continue
        seen.add(mid)
        try: d = unseal(m.get("message", ""))
        except Exception: continue
        sub = d.get("sub") or {}
        ep = sub.get("endpoint", "")
        if not ok_endpoint(ep): continue
        k = b64u(__import__("hashlib").sha256(ep.encode()).digest())[:22]
        if d.get("op") == "unsub":
            if subs.pop(k, None): changed = True
            continue
        keys = sub.get("keys") or {}
        if not (isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str)): continue
        if k not in subs and len(subs) >= MAX_SUBS: continue
        isnew = k not in subs
        subs[k] = {"sub": {"endpoint": ep, "keys": {"p256dh": keys["p256dh"][:200], "auth": keys["auth"][:100]}},
                   "p": clean_prefs(d.get("p")), "at": subs.get(k, {}).get("at") or now.isoformat(timespec="seconds")}
        changed = True
        if isnew: welcome.append(k)
    st["seen"] = sorted(seen)[-400:]
    for k in welcome:
        p = subs[k]["p"]
        on = [n for n, f in (("session starts", p["start"]), ("results", p["res"]), ("major news", p["news"])) if f]
        r = send(subs[k]["sub"], {"title": "F1 Tracker alerts are on", "body": ("You'll get " + ", ".join(on) + ".") if on else "All alerts are off.", "url": "index.html", "tag": "welcome"})
        if r is None: subs.pop(k, None)
    # 2) alerts
    sent = st.get("sent", {})
    evs = events(data, now)
    if not st.get("init"):      # first run: mark what already happened as sent so nobody gets a pile of old alerts
        for key, _, _ in evs: sent[key] = now.isoformat(timespec="seconds")
        st["init"] = now.isoformat(timespec="seconds"); evs = []
        print("OK alerts set up (existing results and news marked as already sent)")
    today = now.astimezone(CT).strftime("%Y-%m-%d")
    news_today = sum(1 for k, v in sent.items() if k.startswith("news:") and v[:10] == today)
    n_alerts = n_msgs = 0
    for key, pref, payload in evs:
        if key in sent: continue
        if pref == "news":
            if news_today >= NEWS_PER_DAY: continue
            news_today += 1
        sent[key] = now.astimezone(CT).isoformat(timespec="seconds")
        n_alerts += 1
        for k in list(subs):
            p = subs[k]["p"]
            want = p.get(pref) if pref != "prac" else (p.get("prac") and (p.get("start") if key.startswith("start:") else p.get("res")))
            if not want: continue
            r = send(subs[k]["sub"], payload, urgency="high" if pref in ("start", "res", "prac") else "normal")
            if r is None: subs.pop(k, None); changed = True
            elif r: n_msgs += 1
    # keep the sent list short: drop entries older than 30 days
    cut = (now - dt.timedelta(days=30)).isoformat()
    st["sent"] = {k: v for k, v in sent.items() if v >= cut[:10]}
    st["subscribers"] = len(subs)
    if changed or welcome: save_subs(subs)
    save_state(st)
    print(f"OK alerts: {len(subs)} subscriber(s), {len(welcome)} new, {n_alerts} alert(s) out ({n_msgs} delivered)")
