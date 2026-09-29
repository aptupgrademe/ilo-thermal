#!/usr/bin/env python3
"""
ilo_thermal.py - temperature history and heat build-up alerts for HPE iLO 5 servers.

The iLO only shows live values. This script polls them via Redfish (read-only),
keeps the history in SQLite, warns about spikes and suspected heat build-up at
the rear, catches up on the iLO event log (IML) after the machine it runs on was
switched off, and renders a self-contained HTML report with charts.

  ilo_thermal.py collect    poll every iLO, store, evaluate, notify
  ilo_thermal.py report     write report/index.html
  ilo_thermal.py test-mail  send a test message through the configured SMTP account

Standard library only. Config: ilo_thermal.conf next to this script, chmod 600.
"""
import base64, configparser, datetime as dt, http.client, json, os, re
import smtplib, socket, sqlite3, ssl, statistics, sys, time
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "ilo_thermal.conf")
DB = os.path.join(HERE, "ilo_thermal.db")
REPORT = os.path.join(HERE, "report", "index.html")
LOG = os.path.join(HERE, "ilo_thermal.log")

INLET = re.compile(r"Inlet Ambient", re.I)    # "01-Inlet Ambient", location "Intake": the air sucked in at the front
REAR_ZONE = re.compile(r"BMC Zone", re.I)     # air zone at the very back (y = 13-14), hardly any self-heating
IML_SEVERITY = {"Critical": "CRIT", "Caution": "WARN", "Warning": "WARN"}

# ---------------------------------------------------------------------------
# Texts (alerts, log, report). Add a language by copying one block.
# ---------------------------------------------------------------------------
TEXT = {
    "en": {
        "unreachable": "iLO unreachable for {n} polls ({err})",
        "unreachable_log": "{host}: iLO unreachable ({err}) - failure {n}",
        "health": "Sensor {name} reports {health} ({value})",
        "inlet": "Intake air {v:.0f} °C (limit {lim} °C)",
        "spike": "Temperature jump: intake +{d:.0f} °C within one hour ({a:.0f} → {b:.0f} °C)",
        "rear": "Heat build-up at the rear suspected: rear {rear:.0f} °C, front {inlet:.0f} °C, "
                "gap {d:.0f} °C (normally {base:.0f} °C)",
        "fan": "Fans at {fan:.0f} % (normally {base:.0f} %) although the intake air is not warmer "
               "({inlet:.0f} °C) - blocked airflow or dust?",
        "iml": "iLO log {when} [{cls}]: {msg}",
        "iml_unreadable": "{host}: IML not readable ({err})",
        "resolved": "Resolved: {text}",
        "kind": {"new": "NEW", "remind": "REMINDER", "resolved": "RESOLVED", "iml": "iLO LOG", "test": "TEST"},
        "no_smtp": "no SMTP configured - alerts only in the log and the report",
        "mail_sent": "mail to {to} sent ({n} item(s))",
        "mail_failed": "sending mail failed: {err}",
        "report_link": "Report",
        "test": "Test message from ilo_thermal.py",
    },
    "de": {
        "unreachable": "iLO seit {n} Abfragen nicht erreichbar ({err})",
        "unreachable_log": "{host}: iLO nicht erreichbar ({err}) - Fehlversuch {n}",
        "health": "Sensor {name} meldet {health} ({value})",
        "inlet": "Ansaugluft {v:.0f} °C (Grenze {lim} °C)",
        "spike": "Temperatursprung: Ansaugluft +{d:.0f} °C innerhalb einer Stunde ({a:.0f} → {b:.0f} °C)",
        "rear": "Wärmestau hinten vermutet: hinten {rear:.0f} °C, vorne {inlet:.0f} °C, "
                "Abstand {d:.0f} °C (normal {base:.0f} °C)",
        "fan": "Lüfter bei {fan:.0f} % (normal {base:.0f} %), obwohl die Ansaugluft nicht wärmer ist "
               "({inlet:.0f} °C) – Luftstau oder Staub?",
        "iml": "iLO-Protokoll {when} [{cls}]: {msg}",
        "iml_unreadable": "{host}: IML nicht lesbar ({err})",
        "resolved": "Entwarnung: {text}",
        "kind": {"new": "NEU", "remind": "ERINNERUNG", "resolved": "ENTWARNUNG", "iml": "iLO-LOG", "test": "TEST"},
        "no_smtp": "kein SMTP konfiguriert - Meldungen nur im Log und im Bericht",
        "mail_sent": "Mail an {to} gesendet ({n} Meldung(en))",
        "mail_failed": "Mailversand fehlgeschlagen: {err}",
        "report_link": "Bericht",
        "test": "Testmeldung von ilo_thermal.py",
    },
}

UI = {
    "en": {"title": "Rack temperatures", "stand": "As of", "every": "readings every 10 minutes while the collector runs (gaps = collector was off)",
           "crit": "⛔ Critical", "warn": "⚠️ Warning", "allok": "✓ All clear", "noalerts": "no active alerts.",
           "front": "front", "rear": "rear", "gap": "gap", "fan": "fan", "last": "last reading",
           "h_inlet": "Intake air at the front (Inlet Ambient)",
           "e_inlet": "The air the server sucks in at the front. Recommended 18–27 °C (ASHRAE); dashed: this script's warning limit.",
           "h_delta": "Rear minus front (BMC Zone − Inlet)",
           "e_delta": "How much warmer the air at the back of the chassis is than at the front. If this gap grows without more load, the exhaust air is not getting away.",
           "h_fan": "Fans (fastest fan per server)",
           "e_fan": "Fans ramping up while the intake stays the same point to blocked airflow or dust.",
           "h_hist": "Alerts", "none": "No alerts so far.", "nodata": "No data yet.",
           "t_time": "Time", "t_host": "Server", "t_level": "Level", "t_msg": "Message",
           "resolved": "✓ Resolved", "iml": "iLO log · ", "warnline": "Warning",
           "ranges": ["24 h", "7 days", "30 days", "90 days"], "range_label": "Time range", "locale": "en-GB"},
    "de": {"title": "Rack-Temperaturen", "stand": "Stand", "every": "Werte alle 10 Minuten, solange der Sammler läuft (Lücken = Rechner aus)",
           "crit": "⛔ Kritisch", "warn": "⚠️ Warnung", "allok": "✓ Alles im grünen Bereich", "noalerts": "keine aktiven Meldungen.",
           "front": "vorne", "rear": "hinten", "gap": "Abstand", "fan": "Lüfter", "last": "letzte Messung",
           "h_inlet": "Ansaugluft vorne (Inlet Ambient)",
           "e_inlet": "Die Luft, die der Server vorne ansaugt. Empfohlen 18–27 °C (ASHRAE); gestrichelt die Warngrenze dieses Skripts.",
           "h_delta": "Abstand hinten minus vorne (BMC Zone − Inlet)",
           "e_delta": "Wie viel wärmer die Luft hinten im Gehäuse ist als vorne. Steigt dieser Abstand ohne mehr Last, zieht die Abluft schlecht ab.",
           "h_fan": "Lüfter (höchster Lüfter je Server)",
           "e_fan": "Drehen die Lüfter hoch, obwohl die Ansaugluft gleich bleibt, deutet das auf Luftstau oder Staub.",
           "h_hist": "Meldungen", "none": "Bisher keine Meldungen.", "nodata": "Noch keine Daten.",
           "t_time": "Zeit", "t_host": "Server", "t_level": "Stufe", "t_msg": "Meldung",
           "resolved": "✓ Entwarnung", "iml": "iLO-Log · ", "warnline": "Warnung",
           "ranges": ["24 h", "7 Tage", "30 Tage", "90 Tage"], "range_label": "Zeitraum", "locale": "de-DE"},
}


def log(msg):
    line = f"{dt.datetime.now():%F %T} {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_conf():
    if not os.path.exists(CONF):
        sys.exit(f"missing {CONF} - copy ilo_thermal.conf.example and fill it in")
    if os.stat(CONF).st_mode & 0o077:
        sys.exit(f"{CONF} must not be readable by group/others (chmod 600)")
    c = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    c.optionxform = str          # keep host names as written (HP1, not hp1)
    c.read(CONF)
    return c


def tr(conf):
    return TEXT.get(conf["general"].get("lang", "en"), TEXT["en"])


# ---------------------------------------------------------------------------
# Redfish: connect by IP but verify the certificate against the configured CA
# and the DNS name on the certificate. Useful when the collector does not use
# the DNS server that knows the iLO names (e.g. an AD-internal zone).
# ---------------------------------------------------------------------------
class _Conn(http.client.HTTPSConnection):
    def __init__(self, ip, name, ctx):
        super().__init__(name, context=ctx, timeout=20)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, 443), 20)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def redfish(conf, ip, name, path):
    ca = conf["ilo"].get("ca_file", "").strip()
    ctx = ssl.create_default_context(cafile=os.path.expanduser(ca) if ca else None)
    auth = base64.b64encode(f"{conf['ilo']['user']}:{conf['ilo']['password']}".encode()).decode()
    c = _Conn(ip, name, ctx)
    c.request("GET", path, headers={"Authorization": "Basic " + auth})
    r = c.getresponse()
    body = r.read()
    if r.status != 200:
        raise RuntimeError(f"HTTP {r.status} for {path}")
    return json.loads(body)


def db():
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS reading (
        ts INTEGER NOT NULL, host TEXT NOT NULL, sensor TEXT NOT NULL,
        value REAL, health TEXT, PRIMARY KEY (ts, host, sensor));
    CREATE INDEX IF NOT EXISTS reading_host_sensor ON reading(host, sensor, ts);
    CREATE TABLE IF NOT EXISTS alert (
        key TEXT PRIMARY KEY, host TEXT, level TEXT, text TEXT,
        first_ts INTEGER, last_seen INTEGER, last_sent INTEGER);
    CREATE TABLE IF NOT EXISTS alert_log (ts INTEGER, host TEXT, level TEXT, text TEXT, kind TEXT);
    CREATE TABLE IF NOT EXISTS failcount (host TEXT PRIMARY KEY, n INTEGER);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return con


def hosts(conf):
    for host, spec in conf["hosts"].items():
        ip, name = [s.strip() for s in spec.split(",")]
        yield host, ip, name


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
def collect(conf):
    T = tr(conf)
    con = db()
    now = int(time.time())
    active = {}       # stateful conditions: key -> (host, level, text)
    iml_events = []   # one-shot events from the iLO's own log, incl. while the collector was off
    for host, ip, name in hosts(conf):
        try:
            th = redfish(conf, ip, name, "/redfish/v1/Chassis/1/Thermal/")
        except Exception as e:
            n = con.execute("SELECT n FROM failcount WHERE host=?", (host,)).fetchone()
            n = (n[0] if n else 0) + 1
            con.execute("INSERT OR REPLACE INTO failcount VALUES (?,?)", (host, n))
            log(T["unreachable_log"].format(host=host, err=e, n=n))
            if n >= int(conf["thresholds"].get("unreachable_after", "3")):
                active[f"{host}:unreachable"] = (host, "WARN", T["unreachable"].format(n=n, err=e))
            continue
        con.execute("INSERT OR REPLACE INTO failcount VALUES (?,0)", (host,))
        iml_events += check_iml(conf, con, host, ip, name, now)
        rows = []
        for t in th.get("Temperatures", []):
            st = t.get("Status", {})
            if st.get("State") == "Absent" or t.get("ReadingCelsius") is None:
                continue
            rows.append((now, host, t["Name"], float(t["ReadingCelsius"]), st.get("Health")))
        for f in th.get("Fans", []):
            st = f.get("Status", {})
            rows.append((now, host, "fan:" + (f.get("Name") or "?"), f.get("Reading"), st.get("Health")))
        con.executemany("INSERT OR REPLACE INTO reading VALUES (?,?,?,?,?)", rows)
        active.update(evaluate(conf, con, host, now, rows))
    con.commit()
    notify(conf, con, now, active, iml_events)
    keep = int(conf["general"].get("keep_days", "400"))
    con.execute("DELETE FROM reading WHERE ts < ?", (now - keep * 86400,))
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# IML catch-up. The iLO keeps no temperature history, but its Integrated
# Management Log records every hardware event with a timestamp - also while
# the collector was switched off. The highest EventNumber seen per host is the
# cursor; everything newer is new. The very first run only sets the cursor.
# ---------------------------------------------------------------------------
def check_iml(conf, con, host, ip, name, now):
    T = tr(conf)
    meta = lambda k: (con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone() or [None])[0]
    cursor, checked = meta(f"iml_cursor:{host}"), meta(f"iml_checked:{host}")
    last_reading = con.execute("SELECT MAX(ts) FROM reading WHERE host=?", (host,)).fetchone()[0] or 0
    # hourly is plenty; right after a gap (collector was off) check immediately
    if cursor and checked and now - int(checked) < 3300 and now - last_reading < 1800:
        return []
    try:
        j = redfish(conf, ip, name, "/redfish/v1/Systems/1/LogServices/IML/Entries/")
    except Exception as e:
        log(T["iml_unreadable"].format(host=host, err=e))
        return []
    ignore = {c.strip().lower() for c in conf["general"].get("iml_ignore_classes", "Network").split(",") if c.strip()}
    entries = sorted(((int(e.get("Oem", {}).get("Hpe", {}).get("EventNumber", 0)), e) for e in j.get("Members", [])),
                     key=lambda x: x[0])
    top = max([n for n, _ in entries] + [int(cursor or 0)])
    events = []
    if cursor is not None:
        for n, e in entries:
            hpe = e.get("Oem", {}).get("Hpe", {})
            level = IML_SEVERITY.get(e.get("Severity"))
            if n <= int(cursor) or not level or (hpe.get("ClassDescription") or "").lower() in ignore:
                continue
            when = e.get("Created", "?").replace("T", " ").replace("Z", " UTC")
            events.append((host, level, T["iml"].format(when=when, cls=hpe.get("ClassDescription", "?"),
                                                        msg=e.get("Message", "").strip())))
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"iml_cursor:{host}", str(top)))
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"iml_checked:{host}", str(now)))
    return events


def series(con, host, sensor_like, since):
    return con.execute("SELECT ts, value FROM reading WHERE host=? AND sensor LIKE ? AND ts>=? ORDER BY ts",
                       (host, sensor_like, since)).fetchall()


def evaluate(conf, con, host, now, rows):
    T, th, out = tr(conf), conf["thresholds"], {}
    cur = {r[2]: r for r in rows}
    inlet = next((r[3] for n, r in cur.items() if INLET.search(n)), None)
    rear = next((r[3] for n, r in cur.items() if REAR_ZONE.search(n)), None)
    fans = [r[3] for n, r in cur.items() if n.startswith("fan:") and r[3] is not None]
    fan = max(fans) if fans else None

    # 1) a sensor or fan is no longer healthy
    for n, r in cur.items():
        if r[4] not in (None, "OK"):
            out[f"{host}:health:{n}"] = (host, "CRIT", T["health"].format(name=n.removeprefix("fan:"), health=r[4], value=r[3]))

    if inlet is not None:
        # 2) absolute intake limits
        if inlet >= float(th["inlet_crit"]):
            out[f"{host}:inlet"] = (host, "CRIT", T["inlet"].format(v=inlet, lim=th["inlet_crit"]))
        elif inlet >= float(th["inlet_warn"]):
            out[f"{host}:inlet"] = (host, "WARN", T["inlet"].format(v=inlet, lim=th["inlet_warn"]))
        # 3) sudden jump: rise against the coolest value of the last hour
        hour = [v for _, v in series(con, host, "%Inlet Ambient%", now - 3600)]
        if hour and inlet - min(hour) >= float(th["spike_degrees"]):
            out[f"{host}:spike"] = (host, "WARN", T["spike"].format(d=inlet - min(hour), a=min(hour), b=inlet))

    # "normal" = medians of the last N days, once at least a day of history exists
    since = now - int(th["baseline_days"]) * 86400
    first = con.execute("SELECT MIN(ts) FROM reading WHERE host=?", (host,)).fetchone()[0] or now
    if now - first >= 86400 and inlet is not None and rear is not None:
        inl = dict(series(con, host, "%Inlet Ambient%", since))
        rz = dict(series(con, host, "%BMC Zone%", since))
        deltas = [rz[t] - inl[t] for t in rz if t in inl]
        if len(deltas) > 50:
            base = statistics.median(deltas)
            delta = rear - inlet
            # 4) heat build-up at the rear: the rear zone pulls away from the intake
            if delta >= base + float(th["rear_delta_rise"]) and delta >= float(th["rear_delta_min"]):
                out[f"{host}:rear"] = (host, "WARN", T["rear"].format(rear=rear, inlet=inlet, d=delta, base=base))
        if fan is not None:
            fan_base = statistics.median([v for _, v in series(con, host, "fan:%", since) if v is not None] or [fan])
            inlet_base = statistics.median(list(inl.values()) or [inlet])
            # 5) fans ramp up although the intake is not warmer -> air cannot get out, or dust
            if fan >= fan_base + float(th["fan_rise"]) and inlet <= inlet_base + 3:
                out[f"{host}:fan"] = (host, "WARN", T["fan"].format(fan=fan, base=fan_base, inlet=inlet))
    return out


def notify(conf, con, now, active, iml_events=()):
    T = tr(conf)
    remind = int(conf["general"].get("remind_hours", "12")) * 3600
    to_send = []
    known = {r[0]: r for r in con.execute("SELECT key, host, level, text, first_ts, last_seen, last_sent FROM alert")}
    for key, (host, level, text) in active.items():
        if key not in known:
            con.execute("INSERT INTO alert VALUES (?,?,?,?,?,?,?)", (key, host, level, text, now, now, now))
            con.execute("INSERT INTO alert_log VALUES (?,?,?,?,?)", (now, host, level, text, "new"))
            to_send.append((host, level, text, "new"))
            log(f"ALERT {host} {level}: {text}")
        else:
            if now - known[key][6] >= remind:
                to_send.append((host, level, text, "remind"))
                con.execute("UPDATE alert SET last_sent=? WHERE key=?", (now, key))
            con.execute("UPDATE alert SET last_seen=?, level=?, text=? WHERE key=?", (now, level, text, key))
    for key, row in known.items():
        if key not in active:
            con.execute("DELETE FROM alert WHERE key=?", (key,))
            con.execute("INSERT INTO alert_log VALUES (?,?,?,?,?)", (now, row[1], "OK", row[3], "resolved"))
            to_send.append((row[1], "OK", T["resolved"].format(text=row[3]), "resolved"))
            log(f"RESOLVED {row[1]}: {row[3]}")
    for host, level, text in iml_events:
        con.execute("INSERT INTO alert_log VALUES (?,?,?,?,?)", (now, host, level, text, "iml"))
        to_send.append((host, level, text, "iml"))
        log(f"IML {host} {level}: {text}")
    con.commit()
    if to_send:
        send_mail(conf, to_send)


def send_mail(conf, items):
    T = tr(conf)
    s = conf["smtp"] if conf.has_section("smtp") else {}
    if not s.get("host") or not s.get("to"):
        log(T["no_smtp"])
        return
    worst = "CRIT" if any(i[1] == "CRIT" for i in items) else "WARN" if any(i[1] == "WARN" for i in items) else "OK"
    msg = EmailMessage()
    msg["Subject"] = f"[iLO-Thermal] {worst}: " + ", ".join(sorted({h for h, *_ in items}))
    msg["From"] = s.get("from") or s.get("user")
    msg["To"] = s["to"]
    msg.set_content("\n".join(f"{T['kind'][kind]:11} {host:6} {level:4}  {text}" for host, level, text, kind in items)
                    + f"\n\n{T['report_link']}: {REPORT}\n")
    try:
        port = int(s.get("port", "465"))
        if port == 465:
            srv = smtplib.SMTP_SSL(s["host"], port, context=ssl.create_default_context(), timeout=30)
        else:
            srv = smtplib.SMTP(s["host"], port, timeout=30)
            srv.starttls(context=ssl.create_default_context())
        if s.get("user"):
            srv.login(s["user"], s["password"])
        srv.send_message(msg)
        srv.quit()
        log(T["mail_sent"].format(to=s["to"], n=len(items)))
    except Exception as e:
        log(T["mail_failed"].format(err=e))


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def report(conf):
    con = db()
    now = int(time.time())
    names = [h for h, _, _ in hosts(conf)]
    data = {}
    for h in names:
        # 10-minute points for the last 7 days, hourly means further back (up to 90 days)
        d = {}
        for key, like in (("inlet", "%Inlet Ambient%"), ("rear", "%BMC Zone%"), ("fan", "fan:%")):
            agg = "MAX" if key == "fan" else "AVG"
            fine = con.execute(f"""SELECT ts, {agg}(value) FROM reading WHERE host=? AND sensor LIKE ? AND ts>=?
                                   GROUP BY ts ORDER BY ts""", (h, like, now - 7 * 86400)).fetchall()
            coarse = con.execute("""SELECT (ts/3600)*3600 AS b, AVG(value) FROM reading WHERE host=? AND sensor LIKE ?
                                    AND ts>=? AND ts<? GROUP BY b ORDER BY b""",
                                 (h, like, now - 90 * 86400, now - 7 * 86400)).fetchall()
            d[key] = [[t, round(v, 1)] for t, v in coarse + fine if v is not None]
        inl = dict(d["inlet"])
        d["delta"] = [[t, round(v - inl[t], 1)] for t, v in d["rear"] if t in inl]
        d["last_ts"] = con.execute("SELECT MAX(ts) FROM reading WHERE host=?", (h,)).fetchone()[0]
        data[h] = d
    alerts = con.execute("SELECT host, level, text, first_ts FROM alert ORDER BY level").fetchall()
    history = con.execute("SELECT ts, host, level, text, kind FROM alert_log ORDER BY ts DESC LIMIT 30").fetchall()
    con.close()
    lang = conf["general"].get("lang", "en")
    payload = {"generated": now, "hosts": names, "data": data, "alerts": alerts, "history": history,
               "inlet_warn": float(conf["thresholds"]["inlet_warn"]), "ui": UI.get(lang, UI["en"])}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    page = TEMPLATE.replace("__LANG__", lang).replace("/*DATA*/", json.dumps(payload).replace("</", "<\\/"))
    with open(REPORT + ".tmp", "w") as f:
        f.write(page)
    os.replace(REPORT + ".tmp", REPORT)
    # optional second copy, e.g. into a Samba/NFS share you can open from your desktop
    dest = conf["general"].get("report_copy_to", "").strip()
    if dest:
        dest = os.path.expanduser(dest)
        target = os.path.join(dest, "index.html") if os.path.isdir(dest) or dest.endswith("/") else dest
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target + ".tmp", "w") as f:
                f.write(page)
            os.replace(target + ".tmp", target)
        except OSError as e:
            log(f"report copy to {target} failed: {e}")


TEMPLATE = r"""<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rack temperatures</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a897f;--grid:#e6e5df;
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--warn:#c98500;--warnbg:#fff4dd;--critbg:#fde8e8;--okbg:#e6f4ea}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--card:#232322;--ink:#fff;--ink2:#c3c2b7;
 --muted:#8f8e85;--grid:#34342f;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--warnbg:#3a2f12;--critbg:#3d1d1d;--okbg:#17301f}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--card:#232322;--ink:#fff;--ink2:#c3c2b7;--muted:#8f8e85;--grid:#34342f;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--warnbg:#3a2f12;--critbg:#3d1d1d;--okbg:#17301f}
body{margin:0;background:var(--surface);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:22px;margin:0 0 2px} h2{font-size:16px;margin:26px 0 4px} .sub{color:var(--ink2);margin:0 0 14px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:10px 12px}
.tile .h{display:flex;align-items:center;gap:8px;font-weight:600}.sw{width:10px;height:10px;border-radius:3px;display:inline-block}
.big{font-size:28px;font-variant-numeric:tabular-nums}.big small{font-size:13px;color:var(--ink2);margin-left:6px}
.kv{color:var(--ink2);font-size:13px;font-variant-numeric:tabular-nums}
.banner{border-radius:10px;padding:10px 14px;margin:0 0 14px;border:1px solid var(--grid)}
.banner.ok{background:var(--okbg)} .banner.warn{background:var(--warnbg)} .banner.crit{background:var(--critbg)}
.bar{display:flex;gap:6px;margin:12px 0 4px;flex-wrap:wrap} .bar button{font:inherit;border:1px solid var(--grid);background:var(--card);color:var(--ink);
 border-radius:7px;padding:4px 12px;cursor:pointer} .bar button[aria-pressed=true]{border-color:var(--ink2);font-weight:600}
.chart{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:8px 10px 4px;position:relative}
.legend{display:flex;gap:14px;color:var(--ink2);font-size:13px;margin:2px 0 4px}
svg{display:block;width:100%;height:auto} .tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--grid);
 border-radius:8px;padding:6px 9px;font-size:12.5px;box-shadow:0 4px 14px #0002;display:none;white-space:nowrap;font-variant-numeric:tabular-nums}
.expl{color:var(--ink2);font-size:13px;margin:4px 2px 0}
.tbl{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px} td,th{text-align:left;padding:4px 8px 4px 0;border-bottom:1px solid var(--grid)}
th{color:var(--ink2);font-weight:500}
</style></head><body><main>
<h1 id="title"></h1><p class="sub" id="sub"></p>
<div id="banner"></div><div class="tiles" id="tiles"></div>
<div class="bar" role="group" id="range"></div>
<h2 id="h_inlet"></h2><div class="chart" id="c_inlet"></div><p class="expl" id="e_inlet"></p>
<h2 id="h_delta"></h2><div class="chart" id="c_delta"></div><p class="expl" id="e_delta"></p>
<h2 id="h_fan"></h2><div class="chart" id="c_fan"></div><p class="expl" id="e_fan"></p>
<h2 id="h_hist"></h2><div class="tbl" id="hist"></div>
</main>
<script>
const D=/*DATA*/, U=D.ui, COL=["var(--s1)","var(--s2)","var(--s3)","#e87ba4","#008300"];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtT=t=>new Date(t*1000).toLocaleString(U.locale,{weekday:"short",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
document.title=U.title;
for(const k of ["title","h_inlet","e_inlet","h_delta","e_delta","h_fan","e_fan","h_hist"])document.getElementById(k).textContent=U[k];
document.getElementById("sub").textContent=U.stand+" "+fmtT(D.generated)+" · "+U.every;
const b=document.getElementById("banner");
if(D.alerts.length){const lvl=D.alerts.some(a=>a[1]=="CRIT")?"crit":"warn";
 b.innerHTML=`<div class="banner ${lvl}"><b>${U[lvl]}</b><br>`+D.alerts.map(a=>esc(a[0]+": "+a[2])).join("<br>")+"</div>";}
else b.innerHTML=`<div class="banner ok"><b>${U.allok}</b> – ${U.noalerts}</div>`;
const last=a=>a.length?a[a.length-1][1]:"–";
document.getElementById("tiles").innerHTML=D.hosts.map((h,i)=>{const d=D.data[h];
 return `<div class="tile"><div class="h"><span class="sw" style="background:${COL[i]}"></span>${esc(h)}</div>
 <div class="big">${last(d.inlet)} °C<small>${U.front}</small></div>
 <div class="kv">${U.rear} ${last(d.rear)} °C · ${U.gap} ${last(d.delta)} °C · ${U.fan} ${last(d.fan)} %</div>
 <div class="kv">${U.last} ${d.last_ts?fmtT(d.last_ts):"–"}</div></div>`}).join("");
const RANGES=[86400,7*86400,30*86400,90*86400]; let range=7*86400;
function niceStep(s){const p=Math.pow(10,Math.floor(Math.log10(s))),m=s/p;return (m<1.5?1:m<3?2:m<7?5:10)*p}
function chart(el,key,unit,lines){
 const W=1060,H=260,L=44,R=12,T=10,B=26,t1=D.generated,t0=t1-range;
 const ser=D.hosts.map(h=>D.data[h][key].filter(p=>p[0]>=t0));
 const vals=ser.flat().map(p=>p[1]).concat(lines.map(l=>l[0]));
 if(!ser.flat().length){el.innerHTML=`<p class="expl">${U.nodata}</p>`;return}
 let lo=Math.min(...vals),hi=Math.max(...vals); const pad=Math.max(1,(hi-lo)*0.12); lo=Math.floor(lo-pad); hi=Math.ceil(hi+pad);
 const x=t=>L+(t-t0)/(t1-t0)*(W-L-R), y=v=>T+(hi-v)/(hi-lo)*(H-T-B), step=niceStep((hi-lo)/5); let g="";
 for(let v=Math.ceil(lo/step)*step;v<=hi;v+=step)g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${L-6}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="var(--ink2)">${+v.toFixed(1)}</text>`;
 const tick=range<=86400?6*3600:range<=7*86400?86400:range<=30*86400?5*86400:15*86400;
 for(let t=Math.ceil(t0/tick)*tick;t<=t1;t+=tick){const dd=new Date(t*1000);
  g+=`<text x="${x(t)}" y="${H-8}" text-anchor="middle" font-size="11" fill="var(--ink2)">${range<=86400?dd.toLocaleTimeString(U.locale,{hour:"2-digit",minute:"2-digit"}):dd.toLocaleDateString(U.locale,{day:"2-digit",month:"2-digit"})}</text>`}
 lines.forEach(([v,lab,c])=>{g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="${c}" stroke-dasharray="5 4" stroke-width="1.2"/><text x="${W-R-4}" y="${y(v)-4}" text-anchor="end" font-size="11" fill="var(--ink2)">${lab}</text>`});
 // a gap of more than 3 hours (collector was off) breaks the line instead of bridging it
 const paths=ser.map((s,i)=>{let d="",prev=null;s.forEach(p=>{d+=(d&&!(prev&&p[0]-prev>3*3600)?"L":"M")+x(p[0]).toFixed(1)+" "+y(p[1]).toFixed(1);prev=p[0]});
  return `<path d="${d}" fill="none" stroke="${COL[i]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`}).join("");
 el.innerHTML=`<div class="legend">`+D.hosts.map((h,i)=>`<span><span class="sw" style="background:${COL[i]}"></span> ${esc(h)}</span>`).join("")+`</div>`+
  `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(U["h_"+(key=="delta"?"delta":key)]||key)}">${g}${paths}<line class="xh" y1="${T}" y2="${H-B}" stroke="var(--muted)" visibility="hidden"/>`+
  ser.map((s,i)=>`<circle class="dot" r="4" fill="${COL[i]}" stroke="var(--card)" stroke-width="2" visibility="hidden"/>`).join("")+
  `<rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/></svg><div class="tip"></div>`;
 const svg=el.querySelector("svg"),tip=el.querySelector(".tip"),xh=svg.querySelector(".xh"),dots=svg.querySelectorAll(".dot");
 svg.addEventListener("mousemove",e=>{const r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)/r.width*W;if(sx<L)return;
  const t=t0+(sx-L)/(W-L-R)*(t1-t0),rows=[];
  ser.forEach((s,i)=>{if(!s.length){dots[i].setAttribute("visibility","hidden");return}
   let best=s[0];for(const p of s)if(Math.abs(p[0]-t)<Math.abs(best[0]-t))best=p;
   if(Math.abs(best[0]-t)>3*3600){dots[i].setAttribute("visibility","hidden");return}
   dots[i].setAttribute("cx",x(best[0]));dots[i].setAttribute("cy",y(best[1]));dots[i].setAttribute("visibility","visible");rows.push([i,best])});
  if(!rows.length){tip.style.display="none";return} const tt=rows[0][1][0];
  xh.setAttribute("x1",x(tt));xh.setAttribute("x2",x(tt));xh.setAttribute("visibility","visible");
  tip.innerHTML=`<b>${fmtT(tt)}</b><br>`+rows.map(([i,p])=>`<span class="sw" style="background:${COL[i]}"></span> ${esc(D.hosts[i])}: <b>${p[1]}</b> ${unit}`).join("<br>");
  tip.style.display="block";const px=x(tt)/W*r.width;tip.style.left=Math.max(4,Math.min(px+14,r.width-tip.offsetWidth-4))+"px";tip.style.top="30px"});
 svg.addEventListener("mouseleave",()=>{tip.style.display="none";xh.setAttribute("visibility","hidden");dots.forEach(d=>d.setAttribute("visibility","hidden"))});
}
function draw(){chart(document.getElementById("c_inlet"),"inlet","°C",[[D.inlet_warn,U.warnline+" "+D.inlet_warn+" °C","var(--warn)"]]);
 chart(document.getElementById("c_delta"),"delta","°C",[]);chart(document.getElementById("c_fan"),"fan","%",[])}
const bar=document.getElementById("range");bar.setAttribute("aria-label",U.range_label);
bar.innerHTML=RANGES.map((s,i)=>`<button data-s="${s}" aria-pressed="${s==range}">${U.ranges[i]}</button>`).join("");
bar.onclick=e=>{const s=+e.target.dataset.s;if(!s)return;range=s;bar.querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",+b.dataset.s==s));draw()};
draw();
const lvl=r=>r[4]=="resolved"?U.resolved:(r[4]=="iml"?U.iml:"")+(r[2]=="CRIT"?U.crit:U.warn);
document.getElementById("hist").innerHTML=D.history.length?`<table><tr><th>${U.t_time}</th><th>${U.t_host}</th><th>${U.t_level}</th><th>${U.t_msg}</th></tr>`+
 D.history.map(r=>`<tr><td>${fmtT(r[0])}</td><td>${esc(r[1])}</td><td>${lvl(r)}</td><td>${esc(r[3])}</td></tr>`).join("")+"</table>":`<p class="expl">${U.none}</p>`;
</script></body></html>"""


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    conf = load_conf()
    if cmd == "collect":
        collect(conf)
    elif cmd == "report":
        report(conf)
    elif cmd == "test-mail":
        send_mail(conf, [("TEST", "OK", tr(conf)["test"], "test")])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
