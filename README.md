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

1. Creá un grupo con Axel, Barbu y Notita.
2. Escribí `/chatid` en el grupo: el bot te contesta el número (es negativo, tipo `-1001234567890`).
   - Para que conteste hace falta que el webhook ya esté puesto (paso 6). Si todavía no,
     usá `https://api.telegram.org/bot<TOKEN>/getUpdates` en el navegador después de
     escribir algo en el grupo.

### 4. Los user_id de cada uno

Hablale a [@userinfobot](https://t.me/userinfobot) desde cada cuenta; te dice el `id`.
Sirven para las menciones de los recordatorios.

### 5. API key de Gemini

En [Google AI Studio](https://aistudio.google.com/apikey) → *Create API key*. Es gratis.
El modelo por defecto es `gemini-2.5-flash`.

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

**Tarea diaria de las 20:00** (*Tasks* → Daily task).
PythonAnywhere programa en **UTC** y Argentina es UTC−3 todo el año, así que:

| Hora Argentina | Hora que ponés en PythonAnywhere |
|---|---|
| 20:00 | **23:00 UTC** |

Comando:

```
python3.13 /home/USUARIO/Notita/run_reminders.py
```

> Las cuentas gratuitas permiten **una** tarea diaria: por eso los recordatorios de
> vencimiento y el resumen de los domingos salen en la misma corrida.
> Acordate también de entrar cada 3 meses al botón *Run until 3 months from today*
> de la web app, o PythonAnywhere la desactiva.

### 7. Variables de entorno

En PythonAnywhere no hay panel de variables, así que se leen del archivo `.env` en la
raíz del proyecto (lo carga `python-dotenv`, tanto la web app como la tarea diaria).

```
TELEGRAM_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=algo-largo-y-random
ALLOWED_CHAT_ID=-1001234567890
AXEL_USER_ID=...
BARBU_USER_ID=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

`ALLOWED_CHAT_ID` es la única puerta de entrada: el bot ignora cualquier otro chat.
`TELEGRAM_WEBHOOK_SECRET` lo manda Telegram en el header `X-Telegram-Bot-Api-Secret-Token`
y el webhook rechaza lo que no coincida.

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
pytest                 # 67 tests, sin red ni API keys
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
app.py                  webhook Flask (lo que sirve PythonAnywhere)
run_reminders.py        rutina de las 20:00 (cron / scheduler / modo prueba)
set_webhook.py          alta, consulta y baja del webhook
notita/
  config.py             variables de entorno, zona horaria, mapeo de usuarios
  dates.py              fechas y recurrencias — módulo puro, con tests
  db.py                 SQLite
  llm.py                Gemini con responseSchema
  telegram.py           cliente de la Bot API
  handlers.py           mensajes, comandos y botones
  reminders.py          recordatorios diarios y resumen semanal
  views.py              textos y teclados
tests/                  fechas, recurrencias y flujo completo (LLM y Telegram simulados)
```

## Fuera del MVP

Audios, WhatsApp y cualquier cosa que cueste plata.
