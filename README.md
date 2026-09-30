# ilo-thermal

**Temperature history and heat build-up alerts for HPE iLO 5 servers** – because the iLO only shows live values.

📖 Full documentation: **[English](docs/en.md)** · **[Deutsch](docs/de.md)** · **[Français](docs/fr.md)** · **[Español](docs/es.md)**

![Report with one week of demo data](docs/report-demo.png)
*Report with one week of synthetic demo data (simulated heat build-up at HP2's rear).*

## What it does

- Polls every iLO via **Redfish, read-only**, every 10 minutes and keeps the history in **SQLite**.
- Warns by mail about:
  - intake air above your limits,
  - sudden **temperature jumps**,
  - a suspected **heat build-up at the rear** (rear air zone pulling away from the intake),
  - **fans ramping up** although the intake is not warmer (blocked airflow, dust),
  - unhealthy sensors and unreachable iLOs.
- **Catches up after downtime:** if the machine running it was off, it reads the iLO's event log (IML)
  on the next start and reports everything that happened meanwhile – with the original timestamps.
- Renders a **self-contained HTML report**: intake air, rear-minus-front gap and fans for 24 h / 7 / 30 / 90 days,
  hover tooltips, light and dark mode, alert history.
- **Python standard library only.** No pip, no requests, no matplotlib.

## Quick start

```sh
git clone https://github.com/aptupgrademe/ilo-thermal.git ~/ilo-thermal
cd ~/ilo-thermal
cp ilo_thermal.conf.example ilo_thermal.conf && chmod 600 ilo_thermal.conf
$EDITOR ilo_thermal.conf          # iLO account (Login privilege is enough), CA file, hosts, SMTP
./ilo_thermal.py collect && ./ilo_thermal.py report
crontab -e                        # add the two lines below
```

```cron
*/10 * * * * $HOME/ilo-thermal/run.sh
@reboot      sleep 120; $HOME/ilo-thermal/run.sh
```

Then open `report/index.html`. Details, alert rules, all configuration keys and troubleshooting:
see the [documentation](docs/en.md).

## Requirements

HPE ProLiant with iLO 5 (tested: DL20 Gen10 Plus, MicroServer Gen10 Plus v2) · Linux with Python ≥ 3.9,
cron and flock · a read-only iLO account · optionally an SMTP account.

## Background

Written for a three-server homelab rack; the story behind it is on the blog:
[apt-upgrade.me](https://www.apt-upgrade.me/).

## Disclaimer

Hobby project, provided **as is**, without any warranty. The author accepts **no liability** for correct
function, missed or false alerts, or any damage resulting from its use. It does not replace your servers'
own protection or a professional monitoring system. See [LICENSE](LICENSE).
