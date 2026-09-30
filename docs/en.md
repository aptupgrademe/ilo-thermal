# ilo-thermal – full documentation (English)

[Deutsch](de.md) · [Français](fr.md) · [Español](es.md) · [README](../README.md)

---

## Contents

1. [Why this exists](#1-why-this-exists)
2. [How it works](#2-how-it-works)
3. [Requirements](#3-requirements)
4. [Which sensors are used, and why](#4-which-sensors-are-used-and-why)
5. [Installation](#5-installation)
6. [Configuration reference](#6-configuration-reference)
7. [Alert rules in detail](#7-alert-rules-in-detail)
8. [When the collector is not always on](#8-when-the-collector-is-not-always-on)
9. [The HTML report](#9-the-html-report)
10. [Files, data and retention](#10-files-data-and-retention)
11. [Troubleshooting](#11-troubleshooting)
12. [Security notes](#12-security-notes)
13. [Limitations and adapting to other models](#13-limitations-and-adapting-to-other-models)
14. [Disclaimer](#14-disclaimer)

---

## 1. Why this exists

HPE iLO 5 shows temperatures and fan speeds **live only**. There is no history: you can see that
the intake air is 25 °C right now, but not what it was last night, during the last heat wave, or
whether one server in the rack is slowly getting warmer than its neighbours.

`ilo-thermal` fills that gap:

- it polls every iLO via Redfish (read-only) every 10 minutes and keeps the values in SQLite,
- it warns about absolute limits, sudden jumps and a **suspected heat build-up at the rear**,
- it catches up on the iLO's own event log (IML) after the machine it runs on was off,
- and it renders a self-contained HTML report with charts.

It was built for a small homelab rack with three HPE servers (ProLiant DL20 Gen10 Plus and
MicroServer Gen10 Plus v2) and a Linux "jump host" that is **not** running 24/7.

## 2. How it works

```
 cron (every 10 min, and once after boot)
   └─ run.sh ── flock ──┬─ ilo_thermal.py collect
                        │     ├─ GET /redfish/v1/Chassis/1/Thermal/          (per iLO)
                        │     ├─ GET /redfish/v1/Systems/1/LogServices/IML/Entries/ (hourly / after a gap)
                        │     ├─ store readings            → ilo_thermal.db (SQLite)
                        │     ├─ evaluate alert rules
                        │     └─ notify (mail, log, alert history)
                        └─ ilo_thermal.py report
                              └─ report/index.html (charts, alerts, history)
```

- **Read-only:** only HTTP `GET` requests. Nothing on the iLO or the server is changed.
- **Standard library only:** Python 3.9+, no `pip install`, no `requests`, no `matplotlib`.
  The charts are SVG generated in the browser from data embedded in the report.
- **TLS is verified:** the script connects to the iLO's IP address but checks the certificate
  against your CA *and* the DNS name on the certificate. This works even when the collector
  does not use the DNS server that knows the iLO names (for example an AD-internal zone).

## 3. Requirements

| What | Details |
|---|---|
| Servers | HPE ProLiant with **iLO 5** (Gen10 / Gen10 Plus). Tested: DL20 Gen10 Plus, MicroServer Gen10 Plus v2, iLO firmware 3.x. |
| iLO account | Any account with the **Login** privilege is enough. Create a dedicated read-only account; don't use `Administrator`. |
| Certificates | The iLO certificates should be issued by a CA you trust (own PKI or public). Self-signed certificates: point `ca_file` at the exported iLO certificate itself. |
| Collector | Linux with Python ≥ 3.9, `cron` and `flock` (util-linux). Network access to the iLOs on TCP 443. |
| Mail (optional) | Any SMTP account (implicit TLS on 465 or STARTTLS on 587). Without SMTP, alerts only go to the log and the report. |

## 4. Which sensors are used, and why

Redfish returns every sensor with its position in the chassis. On HPE, `Oem.Hpe.LocationXmm` /
`LocationYmm` give a grid position: **y = 1 is the front (intake), y = 13–14 the very back**. You
can see the same map in the iLO web UI under *Power & Thermal → Temperatures*.

| Sensor | Position | Used for | Why |
|---|---|---|---|
| `01-Inlet Ambient` | front, context *Intake* | intake air, spikes | The air the server sucks in. It reflects the room/rack, not the server, and it is what HPE's ambient rating (35 °C for these models) and ASHRAE's recommendation (18–27 °C) refer to. |
| `xx-BMC Zone` | rear (y = 13–14) | rear air, heat build-up | An **air zone** sensor at the back with very little self-heating. The best stand-in for exhaust air on models without an exhaust sensor. |
| `Fan n` | – | fan speed (%) | Fans ramping up without warmer intake air is the most sensitive early indicator of airflow problems. |

What is deliberately **not** used:

- **Chip sensors** such as `BMC` (the iLO chip itself, ~70–77 °C) or `LOM` (network chip) – they
  are dominated by their own heat and say little about airflow.
- **`CPU` and `AHCI HD Max` at a constant 40 °C** – on these models they are placeholders, not
  measurements. The disk value in particular stays at 40 °C for non-HPE drives the iLO cannot read;
  the real SMART temperature (checked from the OS) was 31–35 °C.
- **Power:** the non-redundant power supplies of these models don't meter power; Redfish reports 0 W.

### Why "rear minus front" instead of the rear temperature?

The rear temperature follows the room: a warm afternoon raises front *and* back. What reveals a
build-up is the **gap** between rear and front. On a healthy DL20 in a quiet homelab it is about
+3 °C. If the gap grows while the intake stays the same and the load hasn't changed, the exhaust
air isn't getting away – blocked rear, missing blanking panels, recirculation, or dust.

## 5. Installation

### 5.1 Get the code

```sh
git clone https://github.com/aptupgrademe/ilo-thermal.git ~/ilo-thermal
cd ~/ilo-thermal
```

### 5.2 Create a read-only iLO account (per iLO)

iLO web UI → *Administration → User Administration → New*:

- user name e.g. `monitor`, a long random password,
- privileges: **only "Login"** (untick everything else).

Use the same account name and password on every iLO, or run one collector per password.

### 5.3 CA certificate

Export the certificate of the CA that signed your iLO certificates (PEM/Base64) and put it on
the collector, e.g. `~/homelab-root-ca.crt`. If your iLOs still use the factory self-signed
certificate: replace it (recommended) or export each iLO's certificate and use one collector
instance per certificate.

### 5.4 Configuration

```sh
cp ilo_thermal.conf.example ilo_thermal.conf
chmod 600 ilo_thermal.conf        # the script refuses to run with a looser mode
$EDITOR ilo_thermal.conf
```

In `[hosts]` list each server as `name = IP, DNS name on the certificate`:

```ini
[hosts]
HP1 = 192.0.2.29, hp1-ilo.example.home.arpa
```

The name on the left is only a label for charts and mails. The DNS name must match a
Subject Alternative Name of the iLO certificate; it does not need to resolve on the collector.

### 5.5 First run

```sh
./ilo_thermal.py collect      # prints what it stores / any alert
./ilo_thermal.py report       # writes report/index.html
./ilo_thermal.py test-mail    # only if [smtp] is configured
```

The first `collect` also sets the IML cursor of every iLO **without** reporting old entries –
otherwise you would get your servers' entire event history in the first mail.

### 5.6 Schedule it

```sh
crontab -e
```

```cron
*/10 * * * * $HOME/ilo-thermal/run.sh
@reboot      sleep 120; $HOME/ilo-thermal/run.sh
```

- `run.sh` runs `collect` and then `report`, protected by `flock` so runs never overlap.
- The `@reboot` line matters if the collector is not always on: it takes a reading shortly after
  boot and catches up on everything the iLOs logged meanwhile (see [section 8](#8-when-the-collector-is-not-always-on)).
- Output goes to `cron.log`, the script's own log to `ilo_thermal.log`.

## 6. Configuration reference

### `[general]`

| Key | Default | Meaning |
|---|---|---|
| `lang` | `en` | Language of alerts and report: `en` or `de`. |
| `keep_days` | `400` | Readings older than this are deleted. 400 days = a full year for comparison. |
| `remind_hours` | `12` | An alert that is still active is mailed again after this many hours. |
| `iml_ignore_classes` | `Network` | Comma-separated IML classes that never trigger a mail. Link up/down is logged as "Critical" at every reboot and would otherwise be noise. |
| `report_copy_to` | – | Optional. Also write the report to this directory or file, e.g. into a Samba/NFS share you can open from your desktop. |

### `[ilo]`

| Key | Meaning |
|---|---|
| `user`, `password` | The read-only iLO account. |
| `ca_file` | PEM file of the CA that signed the iLO certificates. Empty = the system trust store. `~` is expanded. |

### `[hosts]`

`label = IP, certificate DNS name` – one line per iLO.

### `[thresholds]`

| Key | Default | Meaning |
|---|---|---|
| `inlet_warn` | `30` | Intake air warning (°C). |
| `inlet_crit` | `35` | Intake air critical (°C). HPE rates these servers for 35 °C ambient. |
| `spike_degrees` | `4` | Warn if the intake rises by this much compared with the coolest value of the last hour. |
| `baseline_days` | `7` | "Normal" values are the median of this many days. |
| `rear_delta_rise` | `5` | Rear-minus-front gap must exceed its normal by this much … |
| `rear_delta_min` | `8` | … and be at least this large, to report a heat build-up. |
| `fan_rise` | `20` | Fan % above normal (percentage points) while the intake is not warmer. |
| `unreachable_after` | `3` | Consecutive failed polls before "iLO unreachable" is reported. |

### `[smtp]`

| Key | Meaning |
|---|---|
| `host` | SMTP server. Empty = no mail. |
| `port` | `465` = implicit TLS, anything else = STARTTLS (e.g. `587`). |
| `user`, `password` | SMTP login (leave `user` empty for an open relay). |
| `from`, `to` | Sender and recipient. `from` defaults to `user`. |

## 7. Alert rules in detail

Every `collect` evaluates these rules per server. Rules 1–5 are **stateful**: an alert is mailed
once when it starts, repeated after `remind_hours` while it lasts, and a "resolved" mail follows
when the condition is gone. Rule 6 produces one-shot events.

| # | Rule | Level | Trigger |
|---|---|---|---|
| 1 | Sensor unhealthy | CRIT | Any temperature sensor or fan reports a health other than `OK`. |
| 2 | Intake limit | WARN / CRIT | Intake ≥ `inlet_warn` / ≥ `inlet_crit`. |
| 3 | Temperature jump | WARN | Intake − lowest intake of the last hour ≥ `spike_degrees`. Catches a failed air conditioner, a closed door, a space heater. |
| 4 | Heat build-up at the rear | WARN | Current gap (BMC Zone − Inlet) ≥ normal gap + `rear_delta_rise` **and** ≥ `rear_delta_min`. |
| 5 | Fans without reason | WARN | Fastest fan ≥ normal + `fan_rise` while the intake is at most 3 °C above its normal. Suggests blocked airflow or dust. |
| 6 | iLO event log | WARN / CRIT | New IML entries with severity Caution/Warning/Critical, except ignored classes. |
| – | iLO unreachable | WARN | `unreachable_after` failed polls in a row. |

"Normal" (rules 4 and 5) is the median of the last `baseline_days` days. These two rules only
start once the server has at least one day of history and 50 comparable readings, so a fresh
installation does not alarm on noise.

Mails are grouped: one `collect` sends at most one mail listing everything new, repeated or
resolved. The subject contains the worst level and the affected servers.

## 8. When the collector is not always on

The iLO keeps **no temperature history**, so readings from the time the collector was off
cannot be recovered. The report shows those periods as gaps: lines break when two readings are
more than three hours apart instead of drawing a misleading straight line.

What *can* be recovered is the iLO's **Integrated Management Log (IML)**. Every hardware event –
overheating, fan failure, power loss, memory or PCIe errors – is stored there with a timestamp,
also while the collector is off. `ilo-thermal` remembers the highest `EventNumber` it has seen per
iLO. On every run after a gap (and otherwise hourly) it reads the IML and reports everything
newer, with the original time of the event:

```
IML  HP2  CRIT  iLO log 2026-07-23 20:29:31 UTC [PCI Bus]: Uncorrectable PCI Express Error Detected …
```

Together with the `@reboot` cron line this means: switch the collector on in the morning, and a
few minutes later you know whether anything happened overnight.

## 9. The HTML report

![Report with one week of demo data](report-demo.png)

*Screenshot with one week of synthetic demo data, including a simulated build-up at HP2's rear.*

- **Banner:** active alerts, or "all clear".
- **Tiles:** current intake, rear air, gap, fan and time of the last reading per server.
- **Charts:** intake air (with the warning line), rear-minus-front gap, fans. Time range 24 h /
  7 / 30 / 90 days, hover shows all servers at that moment.
- **Alert history:** the last 30 events, including resolved ones and IML catch-ups.
- Light and dark mode follow the viewer's system setting.

The report is a single file without external dependencies. Open it locally, copy it to a share,
or serve it with any web server. Data: 10-minute values for the last 7 days, hourly means for
up to 90 days.

## 10. Files, data and retention

| File | Content | In git? |
|---|---|---|
| `ilo_thermal.py` | the script | yes |
| `run.sh` | cron wrapper with `flock` | yes |
| `ilo_thermal.conf.example` | annotated example config | yes |
| `ilo_thermal.conf` | your config with credentials (mode 600) | **never** |
| `ilo_thermal.db` | SQLite: readings, active alerts, alert history, IML cursors | no |
| `ilo_thermal.log`, `cron.log` | logs | no |
| `report/index.html` | the report | no |

Size: about 15 sensors per server every 10 minutes ≈ 2,200 rows per server and day; three
servers and 400 days end up at roughly 100–150 MB. Useful queries:

```sh
sqlite3 ilo_thermal.db "SELECT datetime(ts,'unixepoch','localtime'), host, value
  FROM reading WHERE sensor LIKE '%Inlet%' ORDER BY ts DESC LIMIT 20;"
sqlite3 ilo_thermal.db "SELECT * FROM alert;"          -- currently active
sqlite3 ilo_thermal.db "SELECT * FROM meta;"           -- IML cursors
```

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `must not be readable by group/others` | `chmod 600 ilo_thermal.conf` |
| `CERTIFICATE_VERIFY_FAILED … Hostname mismatch` | The DNS name in `[hosts]` is not on the certificate. Check with `openssl s_client -connect IP:443 </dev/null \| openssl x509 -noout -ext subjectAltName`. |
| `CERTIFICATE_VERIFY_FAILED … unable to get local issuer` | `ca_file` is wrong or missing the CA that signed the iLO certificate. |
| `HTTP 401` | Wrong iLO user/password, or the account is locked. |
| No mails | `[smtp] host`/`to` empty → only log. Run `./ilo_thermal.py test-mail`, check `ilo_thermal.log`. |
| Rules 4/5 never fire | Intended during the first day (not enough history). |
| Report shows "No data yet" for a range | No readings in that period (collector off). |
| Runs overlap / nothing happens | `run.sh` uses `flock -n`; a hanging run blocks the next. Check `cron.log`, remove a stale `.lock` only if no run is active. |

To start over: stop cron, delete `ilo_thermal.db`, run `collect` again (the IML cursor is set anew).

## 12. Security notes

- Use a dedicated iLO account with **Login privilege only**. The script only reads.
- The config holds credentials: mode 600, never commit it (it is in `.gitignore`).
- TLS verification cannot be switched off on purpose. Fix the certificates instead.
- The report contains server names and temperatures. Treat it as internal if your labels reveal
  more than you want to share.

## 13. Limitations and adapting to other models

- Written and tested for **iLO 5**. iLO 6 exposes the same Redfish paths, but sensor names may
  differ; it is untested.
- The sensors are matched by name with two regular expressions at the top of the script:
  `INLET = "Inlet Ambient"` and `REAR_ZONE = "BMC Zone"`. For other models, look at the sensor
  list (and the x/y positions) in the iLO and adjust these two lines.
- Redfish only exposes current values; there is no way to fill gaps retroactively.
- One process polls all iLOs sequentially (~1–2 s each). For dozens of servers, consider a
  proper monitoring system (Prometheus + redfish_exporter, Icinga, …).

## 14. Disclaimer

This is a hobby project, provided **as is**, without any warranty – see [LICENSE](../LICENSE).
The author accepts **no liability** for its correct function, for missed or false alerts, or for
any damage to hardware, data or anything else resulting from its use. It does not replace the
protective mechanisms of your servers or a professional monitoring solution. Check the values it
reports against the iLO before you act on them.
