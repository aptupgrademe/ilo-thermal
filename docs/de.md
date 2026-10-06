# ilo-thermal – vollständige Dokumentation (Deutsch)

[English](en.md) · [Français](fr.md) · [Español](es.md) · [README](../README.md)

---

## Inhalt

1. [Wozu das Ganze](#1-wozu-das-ganze)
2. [Funktionsweise](#2-funktionsweise)
3. [Voraussetzungen](#3-voraussetzungen)
4. [Welche Sensoren genutzt werden und warum](#4-welche-sensoren-genutzt-werden-und-warum)
5. [Installation](#5-installation)
6. [Konfigurationsreferenz](#6-konfigurationsreferenz)
7. [Die Alarmregeln im Detail](#7-die-alarmregeln-im-detail)
8. [Wenn der Sammler nicht dauernd läuft](#8-wenn-der-sammler-nicht-dauernd-läuft)
9. [Der HTML-Bericht](#9-der-html-bericht)
10. [Dateien, Daten und Aufbewahrung](#10-dateien-daten-und-aufbewahrung)
11. [Fehlersuche](#11-fehlersuche)
12. [Sicherheitshinweise](#12-sicherheitshinweise)
13. [Grenzen und Anpassung an andere Modelle](#13-grenzen-und-anpassung-an-andere-modelle)
14. [Haftungsausschluss](#14-haftungsausschluss)

---

## 1. Wozu das Ganze

HPE iLO 5 zeigt Temperaturen und Lüfterdrehzahlen **nur live** an. Einen Verlauf gibt es nicht:
Man sieht, dass die Ansaugluft gerade 25 °C hat, aber nicht, wie warm sie letzte Nacht oder in der
letzten Hitzewelle war, und auch nicht, ob ein Server im Rack langsam wärmer wird als seine Nachbarn.

`ilo-thermal` schließt diese Lücke:

- Es fragt alle 10 Minuten jedes iLO per Redfish ab (nur lesend) und speichert die Werte in SQLite.
- Es warnt bei festen Grenzwerten, plötzlichen Sprüngen und bei **Verdacht auf Hitzestau hinten**.
- Es holt das iLO-eigene Ereignisprotokoll (IML) nach, wenn der Rechner, auf dem es läuft, aus war.
- Es erzeugt einen eigenständigen HTML-Bericht mit Diagrammen.

Entstanden ist es für ein kleines Homelab-Rack mit drei HPE-Servern (ProLiant DL20 Gen10 Plus und
MicroServer Gen10 Plus v2) und einem Linux-„Jump-Host“, der **nicht** rund um die Uhr läuft.

## 2. Funktionsweise

```
 cron (alle 10 Min. und einmal nach dem Booten)
   └─ run.sh ── flock ──┬─ ilo_thermal.py collect
                        │     ├─ GET /redfish/v1/Chassis/1/Thermal/          (je iLO)
                        │     ├─ GET /redfish/v1/Systems/1/LogServices/IML/Entries/ (stündlich / nach einer Lücke)
                        │     ├─ Messwerte speichern       → ilo_thermal.db (SQLite)
                        │     ├─ Alarmregeln auswerten
                        │     └─ benachrichtigen (Mail, Log, Alarmverlauf)
                        └─ ilo_thermal.py report
                              └─ report/index.html (Diagramme, Alarme, Verlauf)
```

- **Nur lesend:** ausschließlich HTTP-`GET`-Anfragen. Am iLO und am Server wird nichts verändert.
- **Nur Standardbibliothek:** Python 3.9+, kein `pip install`, kein `requests`, kein `matplotlib`.
  Die Diagramme sind SVG, die der Browser aus den im Bericht eingebetteten Daten zeichnet.
- **TLS wird geprüft:** Das Skript verbindet sich mit der IP-Adresse des iLO, prüft das Zertifikat
  aber gegen Ihre CA *und* gegen den DNS-Namen im Zertifikat. Das funktioniert auch, wenn der Sammler
  nicht den DNS-Server nutzt, der die iLO-Namen kennt (etwa eine AD-interne Zone).

## 3. Voraussetzungen

| Was | Details |
|---|---|
| Server | HPE ProLiant mit **iLO 5** (Gen10 / Gen10 Plus). Getestet: DL20 Gen10 Plus, MicroServer Gen10 Plus v2, iLO-Firmware 3.x. |
| iLO-Konto | Jedes Konto mit dem Recht **Login** genügt. Legen Sie ein eigenes Nur-Lese-Konto an; nicht `Administrator` verwenden. |
| Zertifikate | Die iLO-Zertifikate sollten von einer CA stammen, der Sie vertrauen (eigene PKI oder öffentlich). Selbstsignierte Zertifikate: `ca_file` auf das exportierte iLO-Zertifikat selbst zeigen lassen. |
| Sammler | Linux mit Python ≥ 3.9, `cron` und `flock` (util-linux). Netzwerkzugriff auf die iLOs über TCP 443. |
| Mail (optional) | Beliebiges SMTP-Konto (implizites TLS auf 465 oder STARTTLS auf 587). Ohne SMTP landen Alarme nur im Log und im Bericht. |

## 4. Welche Sensoren genutzt werden und warum

Redfish liefert jeden Sensor mit seiner Position im Gehäuse. Bei HPE legen `Oem.Hpe.LocationXmm` /
`LocationYmm` ihn auf ein Raster. Trotz des Namens sind das **Rasterfelder, keine Millimeter** (die Werte
reichen etwa von 1 bis 14). **y ist die Tiefe: y = 1 ist vorne (Ansaugung), der höchste Wert ganz
hinten**; x ist die Position quer über die Breite. Wie tief das Raster reicht, hängt vom Modell ab: Beim
DL20 Gen10 Plus ist die hinterste Reihe y = 13–14 (BMC Zone bei y = 14), beim kompakteren MicroServer
Gen10 Plus v2 endet das Raster bei y = 13, und die BMC Zone liegt bei y = 10. Dieselbe
Karte zeigt die iLO-Weboberfläche unter *Power & Thermal → Temperatures*.

| Sensor | Position | Verwendet für | Warum |
|---|---|---|---|
| `01-Inlet Ambient` | vorne, Kontext *Intake* | Ansaugluft, Sprünge | Die Luft, die der Server ansaugt. Sie spiegelt Raum bzw. Rack wider, nicht den Server selbst, und auf sie beziehen sich HPEs Umgebungsgrenze (35 °C bei diesen Modellen) und die ASHRAE-Empfehlung (18–27 °C). |
| `xx-BMC Zone` | hinten (DL20: y = 14, MicroServer: y = 10) | Luft hinten, Hitzestau | Ein **Luftzonen**-Sensor auf der Rückseite mit sehr wenig Eigenerwärmung. Der beste Ersatz für die Abluft bei Modellen ohne Abluftsensor. |
| `Fan n` | – | Lüfterdrehzahl (%) | Lüfter, die hochdrehen, ohne dass die Ansaugluft wärmer wird, sind der empfindlichste Frühindikator für Luftstromprobleme. |

Bewusst **nicht** verwendet:

- **Chip-Sensoren** wie `BMC` (der iLO-Chip selbst, ~70–77 °C) oder `LOM` (Netzwerkchip) – sie
  werden von ihrer eigenen Wärme bestimmt und sagen wenig über den Luftstrom aus.
- **`CPU` und `AHCI HD Max` mit konstant 40 °C** – bei diesen Modellen sind das Platzhalter, keine
  Messwerte. Besonders der Plattenwert bleibt bei Nicht-HPE-Laufwerken, die das iLO nicht auslesen
  kann, bei 40 °C; die echte SMART-Temperatur (aus dem Betriebssystem geprüft) lag bei 31–35 °C.
- **Leistung:** Die nicht redundanten Netzteile dieser Modelle messen keine Leistung; Redfish meldet 0 W.

### Warum „hinten minus vorne“ statt der Temperatur hinten?

Die Temperatur hinten folgt dem Raum: Ein warmer Nachmittag hebt vorne *und* hinten an. Einen Stau
erkennt man am **Abstand** zwischen hinten und vorne. Bei einem gesunden DL20 in einem ruhigen Homelab
sind das etwa +3 °C. Wächst der Abstand, während die Ansaugluft gleich bleibt und sich die Last nicht
geändert hat, kommt die Abluft nicht weg – blockierte Rückseite, fehlende Blindplatten, Rückströmung
oder Staub.

## 5. Installation

### 5.1 Code holen

```sh
git clone https://github.com/aptupgrademe/ilo-thermal.git ~/ilo-thermal
cd ~/ilo-thermal
```

### 5.2 Nur-Lese-Konto im iLO anlegen (je iLO)

iLO-Weboberfläche → *Administration → User Administration → New*:

- Benutzername z. B. `monitor`, ein langes Zufallspasswort,
- Rechte: **nur „Login“** (alles andere abwählen).

Verwenden Sie auf allen iLOs denselben Kontonamen und dasselbe Passwort, oder betreiben Sie je Passwort
einen eigenen Sammler.

### 5.3 CA-Zertifikat

Exportieren Sie das Zertifikat der CA, die Ihre iLO-Zertifikate ausgestellt hat (PEM/Base64), und legen
Sie es auf den Sammler, z. B. `~/homelab-root-ca.crt`. Nutzen Ihre iLOs noch das werkseitige
selbstsignierte Zertifikat: ersetzen (empfohlen) oder das Zertifikat jedes iLO exportieren und je
Zertifikat eine eigene Sammler-Instanz betreiben.


**Selbstsignierte (Werks-)Zertifikate:** Statt einer CA lässt sich das Zertifikat jedes iLO über seinen
SHA-256-Fingerabdruck festnageln, in einem Abschnitt `[fingerprints]` (Schlüssel: IP oder DNS-Name aus
`[hosts]`). Für ein so gepinntes iLO entfallen CA- und Namensprüfung; jedes andere Zertifikat wird abgelehnt.

```ini
[fingerprints]
192.0.2.49 = 3F:A1:…:9C      # openssl s_client -connect 192.0.2.49:443 </dev/null | openssl x509 -noout -fingerprint -sha256
```

Wird ein iLO-Zertifikat erneuert oder zurückgesetzt, den Fingerabdruck anpassen, sonst gilt das iLO als nicht erreichbar.

### 5.4 Konfiguration

```sh
cp ilo_thermal.conf.example ilo_thermal.conf
chmod 600 ilo_thermal.conf        # bei lockereren Rechten verweigert das Skript den Start
$EDITOR ilo_thermal.conf
```

Unter `[hosts]` jeden Server als `Name = IP, DNS-Name im Zertifikat` eintragen:

```ini
[hosts]
HP1 = 192.0.2.29, hp1-ilo.example.home.arpa
```

Der Name links ist nur eine Beschriftung für Diagramme und Mails. Der DNS-Name muss einem Subject
Alternative Name des iLO-Zertifikats entsprechen; auf dem Sammler auflösbar sein muss er nicht.

### 5.5 Erster Lauf

```sh
./ilo_thermal.py collect      # zeigt, was gespeichert wird / etwaige Alarme
./ilo_thermal.py report       # schreibt report/index.html
./ilo_thermal.py test-mail    # nur wenn [smtp] eingerichtet ist
```

Der erste `collect` setzt außerdem den IML-Zeiger jedes iLO, **ohne** alte Einträge zu melden – sonst
käme mit der ersten Mail die komplette Ereignisgeschichte Ihrer Server.

### 5.6 Zeitplan einrichten

```sh
crontab -e
```

```cron
*/10 * * * * $HOME/ilo-thermal/run.sh
@reboot      sleep 120; $HOME/ilo-thermal/run.sh
```

- `run.sh` führt `collect` und danach `report` aus, geschützt durch `flock`, damit sich Läufe nie überschneiden.
- Die `@reboot`-Zeile ist wichtig, wenn der Sammler nicht dauernd läuft: Sie misst kurz nach dem Start
  und holt alles nach, was die iLOs in der Zwischenzeit protokolliert haben (siehe [Abschnitt 8](#8-wenn-der-sammler-nicht-dauernd-läuft)).
- Ausgaben gehen nach `cron.log`, das eigene Log des Skripts nach `ilo_thermal.log`.

## 6. Konfigurationsreferenz

### `[general]`

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `lang` | `en` | Sprache von Alarmen und Bericht: `en` oder `de`. Mit `de` erscheinen Mails, Log und Bericht auf Deutsch (die Beispiele in dieser Doku). |
| `keep_days` | `400` | Messwerte, die älter sind, werden gelöscht. 400 Tage = ein volles Jahr zum Vergleichen. |
| `remind_hours` | `12` | Ein weiterhin aktiver Alarm wird nach so vielen Stunden erneut gemailt. |
| `iml_ignore_classes` | `Network` | Kommagetrennte IML-Klassen, die nie eine Mail auslösen. Link up/down wird bei jedem Neustart als „Critical“ protokolliert und wäre sonst nur Rauschen. |
| `report_copy_to` | – | Optional. Den Bericht zusätzlich in dieses Verzeichnis bzw. diese Datei schreiben, z. B. in eine Samba-/NFS-Freigabe, die Sie vom Arbeitsplatz aus öffnen können. |

### `[ilo]`

| Schlüssel | Bedeutung |
|---|---|
| `user`, `password` | Das Nur-Lese-Konto im iLO. |
| `ca_file` | PEM-Datei der CA, die die iLO-Zertifikate ausgestellt hat. Leer = Zertifikatsspeicher des Systems. `~` wird aufgelöst. |

### `[hosts]`

`Name = IP, DNS-Name im Zertifikat` – eine Zeile je iLO.

### `[thresholds]`

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `inlet_warn` | `30` | Warnung Ansaugluft (°C). |
| `inlet_crit` | `35` | Kritisch Ansaugluft (°C). HPE gibt diese Server für 35 °C Umgebung frei. |
| `spike_degrees` | `4` | Warnen, wenn die Ansaugluft um so viel über dem kühlsten Wert der letzten Stunde liegt. |
| `baseline_days` | `7` | „Normalwerte“ sind der Median über so viele Tage. |
| `rear_delta_rise` | `5` | Der Abstand hinten minus vorne muss seinen Normalwert um so viel übersteigen … |
| `rear_delta_min` | `8` | … und mindestens so groß sein, damit ein Hitzestau gemeldet wird. |
| `fan_rise` | `20` | Lüfter-% über normal (Prozentpunkte), während die Ansaugluft nicht wärmer ist. |
| `unreachable_after` | `3` | Anzahl fehlgeschlagener Abfragen in Folge, bevor „iLO nicht erreichbar“ gemeldet wird. |

### `[smtp]`

| Schlüssel | Bedeutung |
|---|---|
| `host` | SMTP-Server. Leer = keine Mail. |
| `port` | `465` = implizites TLS, alles andere = STARTTLS (z. B. `587`). |
| `user`, `password` | SMTP-Anmeldung (`user` leer lassen bei einem offenen Relay). |
| `from`, `to` | Absender und Empfänger. `from` ist standardmäßig `user`. |

## 7. Die Alarmregeln im Detail

Jeder `collect` wertet diese Regeln je Server aus. Die Regeln 1–5 haben einen **Zustand**: Ein Alarm
wird beim Auftreten einmal gemailt, solange er anhält nach `remind_hours` wiederholt, und wenn die
Bedingung weg ist, folgt eine „Entwarnung“-Mail. Regel 6 erzeugt einmalige Ereignisse.

| # | Regel | Stufe | Auslöser |
|---|---|---|---|
| 1 | Sensor nicht in Ordnung | CRIT | Ein Temperatursensor oder Lüfter meldet einen anderen Zustand als `OK`. |
| 2 | Grenzwert Ansaugluft | WARN / CRIT | Ansaugluft ≥ `inlet_warn` / ≥ `inlet_crit`. |
| 3 | Temperatursprung | WARN | Ansaugluft − niedrigster Wert der letzten Stunde ≥ `spike_degrees`. Erkennt eine ausgefallene Klimaanlage, eine geschlossene Tür, einen Heizlüfter. |
| 4 | Hitzestau hinten | WARN | Aktueller Abstand (BMC Zone − Inlet) ≥ normaler Abstand + `rear_delta_rise` **und** ≥ `rear_delta_min`. |
| 5 | Lüfter ohne Grund | WARN | Schnellster Lüfter ≥ normal + `fan_rise`, während die Ansaugluft höchstens 3 °C über normal liegt. Deutet auf behinderten Luftstrom oder Staub hin. |
| 6 | iLO-Ereignisprotokoll | WARN / CRIT | Neue IML-Einträge mit Schweregrad Caution/Warning/Critical, außer ignorierten Klassen. |
| – | iLO nicht erreichbar | WARN | `unreachable_after` fehlgeschlagene Abfragen in Folge. |

„Normal“ (Regeln 4 und 5) ist der Median der letzten `baseline_days` Tage. Diese beiden Regeln greifen
erst, wenn der Server mindestens einen Tag Verlauf und 50 vergleichbare Messwerte hat, damit eine
frische Installation nicht wegen Rauschen Alarm schlägt.

Mails werden gebündelt: Ein `collect` schickt höchstens eine Mail, die alles Neue, Wiederholte und
Behobene auflistet. Der Betreff enthält die schlimmste Stufe und die betroffenen Server.

## 8. Wenn der Sammler nicht dauernd läuft

Das iLO speichert **keinen Temperaturverlauf**; Messwerte aus der Zeit, in der der Sammler aus war,
lassen sich also nicht zurückholen. Der Bericht zeigt diese Zeiträume als Lücken: Liegen zwei Messwerte
mehr als drei Stunden auseinander, bricht die Linie ab, statt eine irreführende Gerade zu zeichnen.

Zurückholen *lässt* sich dagegen das **Integrated Management Log (IML)** des iLO. Jedes
Hardware-Ereignis – Überhitzung, Lüfterausfall, Stromausfall, Speicher- oder PCIe-Fehler – wird dort mit
Zeitstempel gespeichert, auch während der Sammler aus ist. `ilo-thermal` merkt sich je iLO die höchste
bereits gesehene `EventNumber`. Bei jedem Lauf nach einer Lücke (sonst stündlich) liest es das IML und
meldet alles Neuere mit der ursprünglichen Uhrzeit des Ereignisses:

```
IML  HP2  CRIT  iLO-Protokoll 2026-07-23 20:29:31 UTC [PCI Bus]: Uncorrectable PCI Express Error Detected …
```

Zusammen mit der `@reboot`-Zeile im cron heißt das: Sammler morgens einschalten, und ein paar Minuten
später wissen Sie, ob über Nacht etwas passiert ist.

### Ausgeschaltete Server

Auch bei ausgeschaltetem Server läuft das iLO weiter und beantwortet Abfragen – mit den **zuletzt
gemessenen, eingefrorenen** Werten, die genauso als „Enabled“ markiert sind wie echte. `ilo-thermal`
fragt deshalb bei jedem Lauf zusätzlich `PowerState` unter `/redfish/v1/Systems/1/` ab. Ist der Server
nicht `On`, werden **keine Messwerte gespeichert und keine Regeln ausgewertet**; das IML wird weiter
gelesen. Der Bericht zeigt die Kachel als „ausgeschaltet seit …“, der Verlauf endet mit der letzten echten
Messung. Ein- und Ausschalten steht einmal im Log. Lässt sich `PowerState` nicht lesen, wird wie bisher
gesammelt.

## 9. Der HTML-Bericht

![Bericht mit einer Woche Demo-Daten](report-demo.png)

*Screenshot mit einer Woche synthetischer Demo-Daten, einschließlich eines simulierten Staus hinten an HP2.*

- **Banner:** aktive Meldungen oder „Alles im grünen Bereich“.
- **Kacheln:** aktuelle Ansaugluft, Luft hinten, Abstand, Lüfter und Zeitpunkt der letzten Messung je Server.
- **Diagramme:** Ansaugluft (mit Warnlinie), Abstand hinten minus vorne, Lüfter. Zeitraum 24 h /
  7 / 30 / 90 Tage; beim Überfahren mit der Maus werden alle Server zu diesem Zeitpunkt angezeigt.
- **Alarmverlauf:** die letzten 30 Ereignisse, einschließlich behobener und nachgeholter IML-Einträge.
- Heller und dunkler Modus folgen der Systemeinstellung des Betrachters.

Der Bericht ist eine einzelne Datei ohne externe Abhängigkeiten. Lokal öffnen, auf eine Freigabe
kopieren oder mit einem beliebigen Webserver ausliefern. Daten: 10-Minuten-Werte der letzten 7 Tage,
Stundenmittel für bis zu 90 Tage.

## 10. Dateien, Daten und Aufbewahrung

| Datei | Inhalt | Im Git? |
|---|---|---|
| `ilo_thermal.py` | das Skript | ja |
| `run.sh` | cron-Wrapper mit `flock` | ja |
| `ilo_thermal.conf.example` | kommentierte Beispielkonfiguration | ja |
| `ilo_thermal.conf` | Ihre Konfiguration mit Zugangsdaten (Rechte 600) | **nie** |
| `ilo_thermal.db` | SQLite: Messwerte, aktive Alarme, Alarmverlauf, IML-Zeiger | nein |
| `ilo_thermal.log`, `cron.log` | Logs | nein |
| `report/index.html` | der Bericht | nein |

Größe: etwa 15 Sensoren je Server alle 10 Minuten ≈ 2.200 Zeilen je Server und Tag; drei Server und
400 Tage ergeben rund 100–150 MB. Nützliche Abfragen:

```sh
sqlite3 ilo_thermal.db "SELECT datetime(ts,'unixepoch','localtime'), host, value
  FROM reading WHERE sensor LIKE '%Inlet%' ORDER BY ts DESC LIMIT 20;"
sqlite3 ilo_thermal.db "SELECT * FROM alert;"          -- derzeit aktiv
sqlite3 ilo_thermal.db "SELECT * FROM meta;"           -- IML-Zeiger
```

## 11. Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `must not be readable by group/others` | `chmod 600 ilo_thermal.conf` |
| `CERTIFICATE_VERIFY_FAILED … Hostname mismatch` | Der DNS-Name unter `[hosts]` steht nicht im Zertifikat. Prüfen mit `openssl s_client -connect IP:443 </dev/null \| openssl x509 -noout -ext subjectAltName`. |
| `CERTIFICATE_VERIFY_FAILED … unable to get local issuer` | `ca_file` ist falsch oder enthält nicht die CA, die das iLO-Zertifikat ausgestellt hat. |
| `HTTP 401` | Falscher iLO-Benutzer / falsches Passwort, oder das Konto ist gesperrt. |
| Keine Mails | `[smtp] host`/`to` leer → nur Log. `./ilo_thermal.py test-mail` ausführen, `ilo_thermal.log` prüfen. |
| Regeln 4/5 lösen nie aus | Am ersten Tag so gewollt (noch nicht genug Verlauf). |
| Bericht zeigt „Noch keine Daten.“ für einen Zeitraum | Keine Messwerte in diesem Zeitraum (Sammler war aus). |
| Läufe überschneiden sich / nichts passiert | `run.sh` nutzt `flock -n`; ein hängender Lauf blockiert den nächsten. `cron.log` prüfen; eine verwaiste `.lock` nur entfernen, wenn kein Lauf aktiv ist. |

Neu anfangen: cron anhalten, `ilo_thermal.db` löschen, `collect` erneut ausführen (der IML-Zeiger wird neu gesetzt).

## 12. Sicherheitshinweise

- Ein eigenes iLO-Konto **nur mit Login-Recht** verwenden. Das Skript liest nur.
- Die Konfiguration enthält Zugangsdaten: Rechte 600, niemals committen (sie steht in `.gitignore`).
- Die TLS-Prüfung lässt sich absichtlich nicht abschalten. Stattdessen die Zertifikate in Ordnung bringen.
- Der Bericht enthält Servernamen und Temperaturen. Behandeln Sie ihn als intern, wenn Ihre
  Beschriftungen mehr verraten, als Sie teilen möchten.

## 13. Grenzen und Anpassung an andere Modelle

- Geschrieben und getestet für **iLO 5**. iLO 6 bietet dieselben Redfish-Pfade, die Sensornamen können
  aber abweichen; ungetestet.
- Die Sensoren werden über zwei reguläre Ausdrücke am Anfang des Skripts per Namen gefunden:
  `INLET = "Inlet Ambient"` und `REAR_ZONE = "BMC Zone"`. Für andere Modelle die Sensorliste (und die
  x/y-Positionen) im iLO ansehen und diese beiden Zeilen anpassen.
- Redfish liefert nur aktuelle Werte; Lücken lassen sich nachträglich nicht füllen.
- Ein Prozess fragt alle iLOs nacheinander ab (~1–2 s je iLO). Bei Dutzenden Servern besser ein
  richtiges Monitoring-System verwenden (Prometheus + redfish_exporter, Icinga, …).

## 14. Haftungsausschluss

Dies ist ein Hobbyprojekt und wird **ohne Gewähr** bereitgestellt – siehe [LICENSE](../LICENSE).
Der Autor übernimmt **keine Haftung** für die korrekte Funktion, für ausgebliebene oder falsche Alarme
oder für Schäden an Hardware, Daten oder sonstigem, die aus der Nutzung entstehen. Es ersetzt weder die
Schutzmechanismen Ihrer Server noch eine professionelle Monitoring-Lösung. Prüfen Sie die gemeldeten
Werte im iLO, bevor Sie danach handeln.
