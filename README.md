# Notita 🧲

Bot de Telegram que hace de todolist compartida de la casa para **Axel** y **Barbu**.
Le escribís en el grupo como le hablarías a una persona, y él organiza, pregunta y recuerda.

- Lenguaje natural con **Gemini** (capa gratuita), con salida estructurada en JSON.
- **Las fechas las calcula el código**, nunca el LLM.
- **SQLite**, sin servidor de base de datos.
- **100% gratis**: PythonAnywhere free + Gemini free tier.
- Todo en hora de Buenos Aires (`America/Argentina/Buenos_Aires`).

---

## Qué hace

| | |
|---|---|
| Carga en lote | «hay que limpiar la heladera, llamar al plomero y comprar focos» → 3 tareas |
| Pregunta la fecha | si no decís cuándo, te pregunta con botones (y también entiende texto libre) |
| Responsable | «Barbu tiene que llamar al veterinario» → queda a nombre de Barbu |
| Lista del super | «falta leche» va al super, sin fecha ni recordatorios |
| Recurrentes | «cambiar las piedritas cada semana», «pagar expensas todos los 10» 🔁 |
| Recordatorio 20:00 | «¿sacar la basura? ¿Lo hicieron?» con ✅ / ⏰ / 🗑️ |
| Posponer | mañana · finde · semana que viene · elegir fecha |
| Posponer cargoso | a partir de la 3ª vez te carga un poquito 😅 |
| Resumen semanal | domingos 20:00, agrupado por día + vencidas + «algún día» |

### Comandos

```
/todo            todo lo pendiente (vencidas → con fecha → algún día → super)
/todo limpieza   filtrado por categoría
/algundia        sólo lo que no tiene fecha
/super           la lista del super, con botones para tachar
/ayuda           cómo usarlo
/chatid          devuelve el chat_id (sirve para configurarlo la primera vez)
/recordatorios   dispara a mano la rutina de las 20:00 (modo prueba)
```

Categorías: `limpieza`, `arreglos`, `tramites`, `pagos`, `mascotas`, `compras`, `otros`.

---

## Cómo se interpretan las fechas

| Decís | Vence |
|---|---|
| «el lunes» | el **próximo** lunes. Si hoy es lunes, el de la semana que viene |
| «el lunes de la semana que viene» | el lunes de la semana calendario siguiente |
| «mañana» / «pasado» | +1 / +2 días |
| «esta semana» | el **domingo** de esta semana (si hoy es domingo, hoy) |
| «la semana que viene» | el domingo siguiente |
| «el 3 de octubre», «3/10» | esa fecha (si ya pasó, el año que viene) |
| «el 10» | la próxima vez que el calendario marque 10 |
| «algún día», «no sé», «cuando se pueda» | sin vencimiento |

La semana va de **lunes a domingo**. Al LLM siempre se le pasa la fecha y el día de
la semana de hoy, pero él sólo devuelve la *intención* (`dia_semana`, `esta_semana`, …);
la aritmética la hace `notita/dates.py`, que está cubierto por tests.

Las recurrencias mensuales usan el día como **ancla**: un «todos los 31» cae el 28 en
febrero, pero vuelve al 31 en marzo.

---

## Puesta en marcha

### Versión corta (lo que hace la mayoría)

```bash
git clone https://github.com/amarazzi/Notita.git ~/Notita
cd ~/Notita
pip3.13 install --user -r requirements.txt
python3.13 install.py      # te guía y valida todo
```

El instalador te pide el token y la API key, **detecta solo** el grupo y el `user_id` de
cada persona (no hace falta buscarlos), **avisa si el privacy mode está encendido**,
escribe el `.env` y enchufa el webhook. Después:

```bash
python3.13 doctor.py       # chequea que todo esté en orden y te dice qué falta
```

Lo único que hay que hacer a mano antes es crear el bot y apagar el privacy mode
(pasos 1 y 2), y después de instalar, programar la rutina de las 20:00 (paso 7).

El resto de esta sección es el paso a paso detallado, por si algo falla o preferís
hacerlo a mano.

---

### 1. Crear el bot en BotFather

1. En Telegram, hablale a [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Nombre: `Notita`. Username: el que quieras, terminado en `bot`.
3. Guardá el **token** que te da.

### 2. Desactivar el privacy mode ⚠️

Sin esto el bot **no lee** los mensajes normales del grupo, sólo los comandos.

1. BotFather → `/mybots` → elegí Notita → **Bot Settings** → **Group Privacy** → **Turn off**.
2. Tiene que quedar *"Privacy mode is disabled"*.
3. Si el bot ya estaba en el grupo, **sacalo y volvé a agregarlo** para que tome el cambio.

### 3. Crear el grupo y sacar el chat_id

1. Creá un grupo con las personas de la casa y Notita.
2. Escribí `/chatid` en el grupo: el bot te contesta el número (es negativo, tipo `-1001234567890`).
   - Para que conteste hace falta que el webhook ya esté puesto (paso 6). Si todavía no,
     usá `https://api.telegram.org/bot<TOKEN>/getUpdates` en el navegador después de
     escribir algo en el grupo.

`install.py` hace los pasos 3 y 4 solo, mirando quién escribió en el grupo.

### 4. Quiénes viven en la casa

Notita soporta **cualquier cantidad de personas** (una, dos, cinco). Se configuran en una
sola variable, con el formato `Nombre:user_id` separados por coma:

```
NOTITA_PERSONAS=Axel:11111111,Barbu:22222222
```

El `user_id` lo saca `install.py` solo, o te lo dice [@userinfobot](https://t.me/userinfobot)
si le escribís desde cada cuenta. Sirve para mencionar a la persona en los recordatorios;
si no lo ponés, la persona igual funciona pero sin ping.

De ahí salen los nombres que muestra el bot, los que entiende el LLM cuando le decís
«Barbu tiene que llamar al veterinario», y el «los dos» (que pasa a ser «todos» si son
tres o más).

> Las variables viejas `AXEL_USER_ID` / `BARBU_USER_ID` siguen funcionando, así que un
> `.env` existente no se rompe.

Opcionalmente podés darle contexto de la casa para que acierte mejor:

```
NOTITA_CONTEXTO=Tenemos un gato que se llama Milo. Vivimos en un PH con patio.
```

### 5. API key de Gemini

En [Google AI Studio](https://aistudio.google.com/apikey) → *Create API key*. Es gratis.
El modelo por defecto es `gemini-2.5-flash`.

Es **opcional**: sin key, Notita arranca en **modo local** (ver más abajo).

> Verificado: el allowlist de salida de las cuentas gratuitas de PythonAnywhere incluye
> `.googleapis.com` (cubre `generativelanguage.googleapis.com`) y `api.telegram.org`,
> así que el Plan A funciona sin pagar nada. `requests` sale por el proxy de
> PythonAnywhere automáticamente, no hay que configurar nada.

### 6. Deploy en PythonAnywhere (gratis)

> Usá siempre la **misma versión de Python** en la consola, en la web app y en la
> tarea diaria. En las cuentas nuevas (system image `innit`) hay 3.11, 3.12 y 3.13;
> en las viejas puede ser 3.10. Chequealo con `ls /usr/bin/python3.*`.
> Acá se asume **3.13**.

```bash
# En una consola Bash de PythonAnywhere
git clone <tu-repo> ~/Notita
cd ~/Notita
pip3.13 install --user -r requirements.txt
cp .env.example .env
nano .env     # completá token, chat_id, user_ids y la API key
```

**Web app:**

1. *Web* → **Add a new web app** → **Manual configuration** → Python 3.13.
2. Editá el archivo WSGI (`/var/www/USUARIO_pythonanywhere_com_wsgi.py`) y dejalo así:

```python
import sys

path = "/home/USUARIO/Notita"
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application  # noqa
```

3. **Reload** la web app. Probá `https://USUARIO.pythonanywhere.com/` → `{"bot":"notita","ok":true}`.

**Webhook:**

```bash
cd ~/Notita
python3.13 set_webhook.py https://USUARIO.pythonanywhere.com/telegram
python3.13 set_webhook.py --info   # para chequear
```

### 7. Las 20:00: elegí una de las dos opciones

Los recordatorios de vencimiento y el resumen de los domingos salen en **una sola
corrida** diaria (las cuentas gratuitas de PythonAnywhere permiten una única tarea
diaria, y así tampoco se duplican mensajes).

#### Opción A — Tarea diaria de PythonAnywhere (recomendada)

Sin depender de nadie más. *Tasks* → **Daily task**. PythonAnywhere programa en **UTC**
y Argentina es UTC−3 todo el año:

| Hora Argentina | Hora que ponés en PythonAnywhere |
|---|---|
| 20:00 | **23:00 UTC** |

Comando:

```
python3.13 /home/USUARIO/Notita/run_reminders.py
```

Con esta opción **dejá `CRON_SECRET` vacío**: así la ruta `/cron/recordatorios` queda
apagada (responde 404) y no hay una puerta extra abierta.

#### Opción B — Cron externo (cron-job.org)

Útil si querés otro horario, más de una corrida por día, o si la tarea diaria te quedó
desactivada. Poné un `CRON_SECRET` en el `.env`, recargá la web app y creá un cronjob
gratuito en [cron-job.org](https://cron-job.org):

- **URL**: `https://USUARIO.pythonanywhere.com/cron/recordatorios`
- **Horario**: 23:00 UTC (o 20:00 si configuraste el timezone de la cuenta en Buenos Aires)
- **Advanced** → **Headers**: `X-Cron-Secret` = el valor de `CRON_SECRET`

La clave va **en el header, nunca en la URL**: las query strings quedan guardadas en el
access log del servidor. Si falta o no coincide, la ruta responde 403.

Para probarla a mano:

```bash
curl -H "X-Cron-Secret: TU_CRON_SECRET" \
     https://USUARIO.pythonanywhere.com/cron/recordatorios
```

> Con cualquiera de las dos opciones, acordate de entrar cada 3 meses al botón
> *Run until 3 months from today* de la web app, o PythonAnywhere la desactiva.

### 8. Variables de entorno

En PythonAnywhere no hay panel de variables, así que se leen del archivo `.env` en la
raíz del proyecto (lo carga `python-dotenv`, tanto la web app como la tarea diaria).

```
TELEGRAM_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=algo-largo-y-random
ALLOWED_CHAT_ID=-1001234567890
NOTITA_PERSONAS=Axel:11111111,Barbu:22222222
CRON_SECRET=                      # sólo si usás la opción B
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

| Variable | Para qué |
|---|---|
| `TELEGRAM_TOKEN` | El token de BotFather |
| `ALLOWED_CHAT_ID` | La única puerta de entrada: el bot ignora cualquier otro chat |
| `TELEGRAM_WEBHOOK_SECRET` | Telegram lo manda en el header `X-Telegram-Bot-Api-Secret-Token`; el webhook rechaza lo que no coincida |
| `NOTITA_PERSONAS` | Quiénes viven en la casa: `Nombre:user_id` separados por coma |
| `NOTITA_CONTEXTO` | Opcional: dato libre de la casa para que el LLM acierte mejor |
| `CRON_SECRET` | Habilita `/cron/recordatorios` (opción B). **Vacío = ruta apagada** |
| `GEMINI_API_KEY` | La key de Google AI Studio |
| `GEMINI_MODEL` | Por defecto `gemini-2.5-flash` |
| `NOTITA_DB` | Opcional: ruta del archivo SQLite |

Después de tocar el `.env` hay que hacer **Reload** de la web app.
El `.env` tiene secretos: `install.py` lo escribe con permisos `600` y está en el
`.gitignore`, no lo subas al repo.

---

## Modo local (sin Gemini)

Si dejás `GEMINI_API_KEY` vacío, Notita funciona igual pero interpreta todo con reglas
en Python: no sale **nada** de tu servidor. Es también el **fallback automático** cuando
Gemini falla (503, cuota agotada, sin internet): antes en ese caso la tarea se perdía con
un «se me trabó la cabeza», ahora se guarda lo mejor posible y el bot avisa que la anotó
a mano.

Qué sigue funcionando igual:

- **Todas las fechas**: «el lunes», «esta semana», «el 3 de octubre», «en dos semanas»,
  «algún día»... es el mismo módulo testeado que usa el modo con LLM.
- **Recurrencias**: «cada semana», «todos los martes», «todos los 10».
- **Lista del súper**: por señales como «falta», «comprar», «se acabó».
- **Responsable**: si el mensaje nombra a alguien de `NOTITA_PERSONAS`, o dice «los dos».
- **Categorías**: por palabras clave (`pagar`→pagos, `veterinario`→mascotas, etc.).
- Contesta los saludos y no anota los «jajaja».

Qué se pierde:

- Separa varias tareas **sólo si hay comas** («limpiar la heladera, llamar al plomero y
  comprar focos» → 3 ✓; «limpiar la heladera y llamar al plomero» → 1 ✗). Sin LLM no se
  puede saber si ese «y» separa tareas o es parte de una («hablar con el plomero y el
  electricista»), y preferimos no partir mal.
- Las categorías salen por palabra clave, así que cae más seguido en `otros`.
- No pregunta cuando algo es ambiguo: lo anota y listo.

---

## Cuando algo no anda

```bash
python3 doctor.py              # revisa todo y te dice qué arreglar
python3 doctor.py --mensaje    # además manda un mensaje de prueba al grupo
```

Chequea, en orden: versión de Python y dependencias, `.env`, zona horaria, que el token
sirva, **que el privacy mode esté apagado**, el estado del webhook (incluidos mensajes
encolados y el último error que reportó Telegram), que el bot siga en el grupo, las
personas configuradas, que Gemini responda y devuelva las tildes bien, que la base sea
escribible, y cómo quedó agendada la rutina de las 20:00. Sale con código 1 si encontró
algo roto.

| Síntoma | Causa más común |
|---|---|
| No contesta nada | Falta el Reload, o hay un error en el *Error log* de la pestaña Web |
| Sólo contesta los comandos | Privacy mode encendido: apagalo y re-agregá el bot al grupo |
| «Se me trabó la cabeza un segundo» | Gemini falló: key, modelo o cuota. `doctor.py` te dice cuál |
| Mensajes encolados creciendo | La web app está caída y Telegram sigue reintentando |

---

## Modo prueba (sin esperar a las 20:00)

```bash
python3 run_reminders.py --forzar                      # dispara ahora mismo
python3 run_reminders.py --forzar --fecha 2026-10-04   # simula un domingo (con resumen)
```

También desde el grupo: `/recordatorios`.

---

## Desarrollo local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env
pytest                 # 182 tests, sin red ni API keys
python app.py          # http://localhost:5000
```

Para probar el webhook local podés usar cualquier túnel HTTPS y apuntarlo con
`set_webhook.py`.

---

## Plan B: Oracle Cloud Always Free

La lógica de recordatorios está aislada en `notita/reminders.py::correr_rutina_diaria()`,
así que migrar es sólo cambiar quién la llama:

```bash
# la web app con gunicorn detrás de nginx/caddy
gunicorn -w 2 -b 127.0.0.1:8000 app:app

# y el cron de la VM (poné la VM en America/Argentina/Buenos_Aires)
0 20 * * *  cd /opt/notita && /opt/notita/.venv/bin/python run_reminders.py
```

Si la VM queda en UTC, usá `0 23 * * *`.

---

## Estructura

```
install.py              instalador guiado: escribe el .env y enchufa el webhook
doctor.py               diagnóstico: qué está mal y cómo se arregla
app.py                  webhook Flask (lo que sirve PythonAnywhere)
run_reminders.py        rutina de las 20:00 (cron / scheduler / modo prueba)
set_webhook.py          alta, consulta y baja del webhook
notita/
  config.py             variables de entorno, zona horaria, quiénes viven en la casa
  dates.py              fechas y recurrencias — módulo puro, con tests
  db.py                 SQLite
  llm.py                Gemini con responseSchema
  heuristica.py         interpretación sin LLM (modo local y fallback)
  telegram.py           cliente de la Bot API
  handlers.py           mensajes, comandos y botones
  reminders.py          recordatorios diarios y resumen semanal
  views.py              textos y teclados
tests/                  fechas, recurrencias, personas, LLM y flujo completo
```

## ¿Lo puedo usar en mi casa?

Sí, no hay nada atado a nosotros. Forkealo o clonalo, corré `install.py` y listo:
los nombres, la cantidad de personas y el contexto de la casa salen del `.env`.

Lo que **sí** está fijado en el código, por decisión y no por descuido:

- Zona horaria **America/Argentina/Buenos_Aires** y los textos en español rioplatense.
  Si lo querés en otro lado, cambiá `TZ` en `config.py` y los textos de `views.py`.
- Las **categorías** (`limpieza`, `arreglos`, `tramites`, `pagos`, `mascotas`, `compras`,
  `otros`) están en `config.CATEGORIAS`. Se pueden cambiar ahí: el LLM las toma de esa
  lista, no hay que tocar el prompt.
- La **semana va de lunes a domingo** y el recordatorio es a las **20:00**.

## Fuera del MVP

Audios, WhatsApp y cualquier cosa que cueste plata.
