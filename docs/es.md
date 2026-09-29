# ilo-thermal – documentación completa (español)

[English](en.md) · [Français](fr.md) · [README](../README.md)

---

## Índice

1. [Por qué existe](#1-por-qué-existe)
2. [Cómo funciona](#2-cómo-funciona)
3. [Requisitos](#3-requisitos)
4. [Qué sensores se usan y por qué](#4-qué-sensores-se-usan-y-por-qué)
5. [Instalación](#5-instalación)
6. [Referencia de configuración](#6-referencia-de-configuración)
7. [Reglas de alerta en detalle](#7-reglas-de-alerta-en-detalle)
8. [Cuando el recolector no está siempre encendido](#8-cuando-el-recolector-no-está-siempre-encendido)
9. [El informe HTML](#9-el-informe-html)
10. [Archivos, datos y retención](#10-archivos-datos-y-retención)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Seguridad](#12-seguridad)
13. [Limitaciones y adaptación a otros modelos](#13-limitaciones-y-adaptación-a-otros-modelos)
14. [Exención de responsabilidad](#14-exención-de-responsabilidad)

---

## 1. Por qué existe

El iLO 5 de HPE muestra las temperaturas y la velocidad de los ventiladores **solo en directo**.
No hay historial: se ve que el aire de entrada está ahora a 25 °C, pero no cómo estaba anoche,
durante la última ola de calor, ni si un servidor del rack se va calentando poco a poco más que
sus vecinos.

`ilo-thermal` cubre ese hueco:

- consulta cada iLO por Redfish (solo lectura) cada 10 minutos y guarda los valores en SQLite,
- avisa de límites absolutos, saltos bruscos y una **presunta acumulación de calor en la parte trasera**,
- recupera el registro de eventos del propio iLO (IML) cuando la máquina que lo ejecuta estuvo apagada,
- y genera un informe HTML autónomo con gráficos.

Se creó para un pequeño rack de homelab con tres servidores HPE (ProLiant DL20 Gen10 Plus y
MicroServer Gen10 Plus v2) y un «jump host» Linux que **no** funciona las 24 horas.

## 2. Cómo funciona

```
 cron (cada 10 min, y una vez tras el arranque)
   └─ run.sh ── flock ──┬─ ilo_thermal.py collect
                        │     ├─ GET /redfish/v1/Chassis/1/Thermal/          (por iLO)
                        │     ├─ GET /redfish/v1/Systems/1/LogServices/IML/Entries/ (cada hora / tras una interrupción)
                        │     ├─ guardar lecturas         → ilo_thermal.db (SQLite)
                        │     ├─ evaluar reglas de alerta
                        │     └─ notificar (correo, registro, historial)
                        └─ ilo_thermal.py report
                              └─ report/index.html (gráficos, alertas, historial)
```

- **Solo lectura:** únicamente peticiones HTTP `GET`. No se cambia nada en el iLO ni en el servidor.
- **Solo biblioteca estándar:** Python 3.9+, sin `pip install`, sin `requests`, sin `matplotlib`.
  Los gráficos son SVG generados en el navegador a partir de los datos incrustados en el informe.
- **TLS verificado:** el script se conecta a la dirección IP del iLO, pero comprueba el
  certificado contra su CA *y* contra el nombre DNS del certificado. Funciona incluso si el
  recolector no usa el servidor DNS que conoce los nombres de los iLO (por ejemplo, una zona
  interna de Active Directory).

## 3. Requisitos

| Qué | Detalles |
|---|---|
| Servidores | HPE ProLiant con **iLO 5** (Gen10 / Gen10 Plus). Probado: DL20 Gen10 Plus, MicroServer Gen10 Plus v2, firmware iLO 3.x. |
| Cuenta iLO | Basta con una cuenta que tenga solo el privilegio **Login**. Cree una cuenta dedicada de solo lectura; no use `Administrator`. |
| Certificados | Los certificados del iLO deberían estar emitidos por una CA de confianza (PKI propia o pública). Certificados autofirmados: apunte `ca_file` al certificado exportado del iLO. |
| Recolector | Linux con Python ≥ 3.9, `cron` y `flock` (util-linux). Acceso de red a los iLO por TCP 443. |
| Correo (opcional) | Cualquier cuenta SMTP (TLS implícito en 465 o STARTTLS en 587). Sin SMTP, las alertas solo van al registro y al informe. |

## 4. Qué sensores se usan y por qué

Redfish devuelve cada sensor con su posición en el chasis. En HPE, `Oem.Hpe.LocationXmm` /
`LocationYmm` dan una posición en una cuadrícula: **y = 1 es el frontal (entrada de aire),
y = 13–14 la parte trasera**. El mismo mapa se ve en la interfaz web del iLO en
*Power & Thermal → Temperatures*.

| Sensor | Posición | Se usa para | Por qué |
|---|---|---|---|
| `01-Inlet Ambient` | frontal, contexto *Intake* | aire de entrada, saltos | El aire que aspira el servidor. Refleja la sala o el rack, no el servidor, y es la referencia de la especificación de HPE (35 °C ambiente para estos modelos) y de la recomendación ASHRAE (18–27 °C). |
| `xx-BMC Zone` | trasera (y = 13–14) | aire trasero, acumulación de calor | Un sensor de **zona de aire** en la parte trasera, con muy poco calor propio. El mejor sustituto del aire de salida en modelos sin sensor de escape. |
| `Fan n` | – | velocidad de ventiladores (%) | Que los ventiladores aceleren sin que el aire de entrada esté más caliente es el indicador temprano más sensible de un problema de flujo de aire. |

Deliberadamente **no** se usan:

- **Sensores de chips** como `BMC` (el propio chip del iLO, ~70–77 °C) o `LOM` (chip de red):
  dominados por su propio calor, dicen poco sobre el flujo de aire.
- **`CPU` y `AHCI HD Max` fijos en 40 °C**: en estos modelos son valores de relleno, no mediciones.
  El valor de los discos se queda en 40 °C con discos que no son de HPE y que el iLO no puede
  leer; la temperatura SMART real (comprobada desde el sistema operativo) era de 31–35 °C.
- **Consumo:** las fuentes de alimentación no redundantes de estos modelos no miden la potencia;
  Redfish indica 0 W.

### ¿Por qué «trasera menos frontal» y no la temperatura trasera?

La temperatura trasera sigue a la de la sala: una tarde calurosa sube el frontal *y* la trasera.
Lo que delata una acumulación es la **diferencia** entre trasera y frontal. En un DL20 sano en un
homelab tranquilo ronda los +3 °C. Si la diferencia crece mientras el aire de entrada es el mismo
y la carga no ha cambiado, el aire caliente no sale: trasera obstruida, faltan tapas ciegas,
recirculación o polvo.

## 5. Instalación

### 5.1 Obtener el código

```sh
git clone https://github.com/aptupgrademe/ilo-thermal.git ~/ilo-thermal
cd ~/ilo-thermal
```

### 5.2 Crear una cuenta iLO de solo lectura (en cada iLO)

Interfaz web del iLO → *Administration → User Administration → New*:

- nombre de usuario, p. ej. `monitor`, con una contraseña larga y aleatoria,
- privilegios: **solo «Login»** (desmarque todo lo demás).

Use el mismo nombre y contraseña en todos los iLO, o un recolector por contraseña.

### 5.3 Certificado de la CA

Exporte el certificado de la CA que firmó los certificados del iLO (PEM/Base64) y colóquelo en
el recolector, p. ej. `~/homelab-root-ca.crt`. Si sus iLO siguen con el certificado autofirmado de
fábrica: sustitúyalo (recomendado) o exporte el certificado de cada iLO y use una instancia del
recolector por certificado.

### 5.4 Configuración

```sh
cp ilo_thermal.conf.example ilo_thermal.conf
chmod 600 ilo_thermal.conf        # el script se niega a arrancar con permisos más amplios
$EDITOR ilo_thermal.conf
```

En `[hosts]`, indique cada servidor como `nombre = IP, nombre DNS del certificado`:

```ini
[hosts]
HP1 = 192.0.2.29, hp1-ilo.example.home.arpa
```

El nombre de la izquierda es solo una etiqueta para gráficos y correos. El nombre DNS debe
coincidir con un Subject Alternative Name del certificado del iLO; no es necesario que se
resuelva en el recolector.

### 5.5 Primera ejecución

```sh
./ilo_thermal.py collect      # muestra lo que guarda / posibles alertas
./ilo_thermal.py report       # escribe report/index.html
./ilo_thermal.py test-mail    # solo si [smtp] está configurado
```

El primer `collect` también fija el cursor IML de cada iLO **sin** notificar entradas antiguas;
de lo contrario, el primer correo contendría todo el historial de eventos de sus servidores.

### 5.6 Programación

```sh
crontab -e
```

```cron
*/10 * * * * $HOME/ilo-thermal/run.sh
@reboot      sleep 120; $HOME/ilo-thermal/run.sh
```

- `run.sh` ejecuta `collect` y después `report`, protegidos con `flock` para que nunca se solapen.
- La línea `@reboot` es importante si el recolector no está siempre encendido: toma una lectura
  poco después del arranque y recupera todo lo que los iLO registraron mientras tanto
  (véase la [sección 8](#8-cuando-el-recolector-no-está-siempre-encendido)).
- La salida va a `cron.log`; el registro propio del script, a `ilo_thermal.log`.

## 6. Referencia de configuración

### `[general]`

| Clave | Por defecto | Significado |
|---|---|---|
| `lang` | `en` | Idioma de las alertas y del informe: `en` o `de`. |
| `keep_days` | `400` | Las lecturas más antiguas se eliminan. 400 días = un año completo para comparar. |
| `remind_hours` | `12` | Una alerta que sigue activa se vuelve a enviar tras estas horas. |
| `iml_ignore_classes` | `Network` | Clases IML (separadas por comas) que nunca generan correo. Los cambios de enlace se registran como «Critical» en cada reinicio y solo serían ruido. |
| `report_copy_to` | – | Opcional. Escribe también el informe en este directorio o archivo, p. ej. en una carpeta compartida Samba/NFS accesible desde su equipo. |

### `[ilo]`

| Clave | Significado |
|---|---|
| `user`, `password` | La cuenta iLO de solo lectura. |
| `ca_file` | Archivo PEM de la CA que firmó los certificados del iLO. Vacío = almacén de certificados del sistema. `~` se expande. |

### `[hosts]`

`etiqueta = IP, nombre DNS del certificado` – una línea por iLO.

### `[thresholds]`

| Clave | Por defecto | Significado |
|---|---|---|
| `inlet_warn` | `30` | Aire de entrada: aviso (°C). |
| `inlet_crit` | `35` | Aire de entrada: crítico (°C). HPE especifica estos servidores para 35 °C ambiente. |
| `spike_degrees` | `4` | Avisar si el aire de entrada supera en este valor el mínimo de la última hora. |
| `baseline_days` | `7` | Los valores «normales» son la mediana de estos días. |
| `rear_delta_rise` | `5` | La diferencia trasera-frontal debe superar su valor normal en esta cantidad … |
| `rear_delta_min` | `8` | … y ser al menos esta, para notificar una acumulación de calor. |
| `fan_rise` | `20` | Ventiladores por encima de lo normal (puntos porcentuales) sin que el aire de entrada esté más caliente. |
| `unreachable_after` | `3` | Consultas fallidas seguidas antes de notificar «iLO inaccesible». |

### `[smtp]`

| Clave | Significado |
|---|---|
| `host` | Servidor SMTP. Vacío = sin correo. |
| `port` | `465` = TLS implícito, cualquier otro = STARTTLS (p. ej. `587`). |
| `user`, `password` | Credenciales SMTP (deje `user` vacío para un relay abierto). |
| `from`, `to` | Remitente y destinatario. `from` toma `user` por defecto. |

## 7. Reglas de alerta en detalle

Cada `collect` evalúa estas reglas por servidor. Las reglas 1–5 tienen **estado**: la alerta se
envía una vez al empezar, se repite tras `remind_hours` mientras dure y, cuando la condición
desaparece, llega un correo de «resuelto». La regla 6 genera eventos puntuales.

| # | Regla | Nivel | Disparador |
|---|---|---|---|
| 1 | Sensor con fallo | CRIT | Un sensor de temperatura o un ventilador informa un estado distinto de `OK`. |
| 2 | Límite de aire de entrada | WARN / CRIT | Aire de entrada ≥ `inlet_warn` / ≥ `inlet_crit`. |
| 3 | Salto de temperatura | WARN | Aire de entrada − mínimo de la última hora ≥ `spike_degrees`. Detecta un aire acondicionado averiado, una puerta cerrada o una estufa. |
| 4 | Acumulación de calor trasera | WARN | Diferencia actual (BMC Zone − Inlet) ≥ diferencia normal + `rear_delta_rise` **y** ≥ `rear_delta_min`. |
| 5 | Ventiladores sin motivo | WARN | Ventilador más rápido ≥ normal + `fan_rise` mientras el aire de entrada supera su normal en 3 °C como máximo. Indica flujo de aire bloqueado o polvo. |
| 6 | Registro de eventos del iLO | WARN / CRIT | Nuevas entradas IML con gravedad Caution/Warning/Critical, salvo clases ignoradas. |
| – | iLO inaccesible | WARN | `unreachable_after` consultas fallidas seguidas. |

Lo «normal» (reglas 4 y 5) es la mediana de los últimos `baseline_days` días. Estas dos reglas
solo se activan cuando el servidor tiene al menos un día de historial y 50 lecturas comparables,
para que una instalación nueva no dé falsas alarmas por ruido.

Los correos se agrupan: un `collect` envía como máximo un correo con todo lo nuevo, repetido o
resuelto. El asunto indica el nivel más grave y los servidores afectados.

## 8. Cuando el recolector no está siempre encendido

El iLO **no guarda historial de temperaturas**, así que las lecturas del periodo en que el
recolector estuvo apagado no se pueden recuperar. El informe muestra esos periodos como huecos:
las líneas se cortan cuando dos lecturas están separadas más de tres horas, en lugar de trazar
una recta engañosa.

Lo que *sí* se puede recuperar es el **Integrated Management Log (IML)** del iLO. Cada evento de
hardware (sobrecalentamiento, fallo de ventilador, corte de corriente, errores de memoria o PCIe)
se guarda allí con su marca de tiempo, también mientras el recolector está apagado.
`ilo-thermal` recuerda el `EventNumber` más alto visto por cada iLO. En cada ejecución tras una
interrupción (y si no, cada hora) lee el IML y notifica todo lo más reciente, con la hora original
del evento:

```
IML  HP2  CRIT  iLO log 2026-07-23 20:29:31 UTC [PCI Bus]: Uncorrectable PCI Express Error Detected …
```

Junto con la línea `@reboot` de cron significa: encienda el recolector por la mañana y, unos
minutos después, sabrá si ocurrió algo durante la noche.

## 9. El informe HTML

![Informe con una semana de datos de demostración](report-demo.png)

*Captura con una semana de datos de demostración sintéticos, incluida una acumulación simulada en la trasera de HP2.*

- **Banner:** alertas activas, o «todo en orden».
- **Fichas:** aire de entrada, aire trasero, diferencia, ventilador y hora de la última lectura por servidor.
- **Gráficos:** aire de entrada (con la línea de aviso), diferencia trasera-frontal, ventiladores.
  Periodo 24 h / 7 / 30 / 90 días; al pasar el ratón se ven todos los servidores en ese momento.
- **Historial de alertas:** los últimos 30 eventos, incluidas resoluciones y recuperaciones del IML.
- Los modos claro y oscuro siguen la configuración del sistema de quien lo mira.

El informe es un único archivo sin dependencias externas. Ábralo en local, cópielo a una carpeta
compartida o sírvalo con cualquier servidor web. Datos: valores cada 10 minutos de los últimos
7 días y medias horarias hasta 90 días.

## 10. Archivos, datos y retención

| Archivo | Contenido | ¿En git? |
|---|---|---|
| `ilo_thermal.py` | el script | sí |
| `run.sh` | envoltorio de cron con `flock` | sí |
| `ilo_thermal.conf.example` | ejemplo de configuración comentado | sí |
| `ilo_thermal.conf` | su configuración con credenciales (modo 600) | **nunca** |
| `ilo_thermal.db` | SQLite: lecturas, alertas activas, historial, cursores IML | no |
| `ilo_thermal.log`, `cron.log` | registros | no |
| `report/index.html` | el informe | no |

Tamaño: unos 15 sensores por servidor cada 10 minutos ≈ 2200 filas por servidor y día; tres
servidores durante 400 días ocupan unos 100–150 MB. Consultas útiles:

```sh
sqlite3 ilo_thermal.db "SELECT datetime(ts,'unixepoch','localtime'), host, value
  FROM reading WHERE sensor LIKE '%Inlet%' ORDER BY ts DESC LIMIT 20;"
sqlite3 ilo_thermal.db "SELECT * FROM alert;"          -- alertas activas
sqlite3 ilo_thermal.db "SELECT * FROM meta;"           -- cursores IML
```

## 11. Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| `must not be readable by group/others` | `chmod 600 ilo_thermal.conf` |
| `CERTIFICATE_VERIFY_FAILED … Hostname mismatch` | El nombre DNS de `[hosts]` no está en el certificado. Compruébelo con `openssl s_client -connect IP:443 </dev/null \| openssl x509 -noout -ext subjectAltName`. |
| `CERTIFICATE_VERIFY_FAILED … unable to get local issuer` | `ca_file` es incorrecto o no contiene la CA que firmó el certificado del iLO. |
| `HTTP 401` | Usuario/contraseña del iLO incorrectos, o cuenta bloqueada. |
| No llegan correos | `[smtp] host`/`to` vacíos → solo registro. Ejecute `./ilo_thermal.py test-mail` y revise `ilo_thermal.log`. |
| Las reglas 4/5 nunca saltan | Es lo previsto durante el primer día (poco historial). |
| El informe muestra «No data yet» para un periodo | No hay lecturas en ese periodo (recolector apagado). |
| Ejecuciones bloqueadas | `run.sh` usa `flock -n`; una ejecución colgada bloquea la siguiente. Revise `cron.log` y borre un `.lock` huérfano solo si no hay ninguna ejecución activa. |

Para empezar de cero: detenga cron, borre `ilo_thermal.db` y vuelva a ejecutar `collect` (el cursor IML se fija de nuevo).

## 12. Seguridad

- Use una cuenta iLO dedicada **solo con el privilegio Login**. El script solo lee.
- La configuración contiene credenciales: modo 600, nunca la suba a git (está en `.gitignore`).
- La verificación TLS no se puede desactivar a propósito. Corrija los certificados.
- El informe contiene nombres de servidores y temperaturas. Trátelo como interno si sus etiquetas
  revelan más de lo que quiere compartir.

## 13. Limitaciones y adaptación a otros modelos

- Escrito y probado para **iLO 5**. El iLO 6 expone las mismas rutas Redfish, pero los nombres de
  los sensores pueden variar; no está probado.
- Los sensores se identifican por nombre con dos expresiones regulares al principio del script:
  `INLET = "Inlet Ambient"` y `REAR_ZONE = "BMC Zone"`. Para otros modelos, consulte la lista de
  sensores (y sus posiciones x/y) en el iLO y adapte esas dos líneas.
- Redfish solo expone valores actuales; los huecos no se pueden rellenar a posteriori.
- Un único proceso consulta los iLO uno tras otro (~1–2 s cada uno). Para decenas de servidores,
  mejor una solución de monitorización completa (Prometheus + redfish_exporter, Icinga…).

## 14. Exención de responsabilidad

Este es un proyecto personal, ofrecido **tal cual**, sin ninguna garantía – véase [LICENSE](../LICENSE).
El autor **no asume ninguna responsabilidad** por su correcto funcionamiento, por alertas no
emitidas o erróneas, ni por daños en el hardware, los datos o cualquier otra cosa derivados de su
uso. No sustituye los mecanismos de protección de sus servidores ni una solución de monitorización
profesional. Compruebe en el iLO los valores notificados antes de actuar.
