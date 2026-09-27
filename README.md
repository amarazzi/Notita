# Notita 🧲

Bot de Telegram que hace de todolist compartida de la casa. Le escribís en el grupo como
le hablarías a una persona, y él organiza, pregunta y recuerda.

- Lenguaje natural con **Gemini** (capa gratuita), con salida estructurada en JSON.
  Es opcional: sin API key funciona igual, con reglas locales.
- **Las fechas las calcula el código**, nunca el LLM.
- **SQLite**, sin servidor de base de datos.
- **100% gratis**: PythonAnywhere free + Gemini free tier.
- Todo en hora de Buenos Aires (`America/Argentina/Buenos_Aires`).
- Sirve para **cualquier casa**: los nombres y la cantidad de personas salen del `.env`.

---

## Qué hace

| | |
|---|---|
| Carga en lote | «hay que limpiar la heladera, llamar al plomero y comprar focos» → 3 tareas |
| Pregunta la fecha | si no decís cuándo, te pregunta con botones (y también entiende texto libre) |
| Fecha **y hora** | «llevar a Milo al veterinario el jueves a las 18» → guarda la hora y avisa a esa hora ([hace falta que el cron corra seguido](#y-la-hora-exacta)) |
| Responsable | «Barbu tiene que llamar al veterinario» → queda a nombre de Barbu |
| Lista del super | «falta leche» va al super, sin fecha ni recordatorios |
| Recurrentes | «cambiar las piedritas cada semana», «pagar expensas todos los 10» 🔁 |
| Recordatorio 20:00 | «¿sacar la basura? ¿Lo hicieron?» con ✅ / ⏰ / 🗑️ |
| Posponer | mañana · finde · semana que viene · elegir fecha |
| Posponer cargoso | a partir de la 3ª vez te carga un poquito 😅 |
| Resumen semanal | domingos 20:00, agrupado por día + vencidas + «algún día» |
| Recados 💌 | «avisale a Axel que llego en 10» → se lo dice en el momento, mencionándolo. Con fecha («mañana que compre pan»), a las 20:00 |
| No cansa | Si una tarea lleva varias noches sin hacerse, deja de mandar un mensaje por tarea y las junta todas en uno, con «patearlas una semana» |
| Súper de una | Botón «compramos todo» en `/super`, y el resumen del domingo te dice cuántas cosas quedan |
| No duplica | Si anotás algo que ya estaba, te lo dice en vez de guardarlo dos veces |
| Entiende pedidos | «¿qué hay que hacer?», «mostrame el súper», «ya limpié la heladera», «borrá la del plomero» |
| Varias de una | «ya compré la leche y la lavandina» tacha las dos, en un solo mensaje |
| Acciones masivas | «borrá todo lo del súper», «ya compramos todo». Borrar todas las tareas pide confirmación |
| Editar hablando | «pasá lo del horno para el domingo», «lo del veterinario lo hago yo», «cambiá "regar" por "regar el balcón"» |
| Avisa si algo no cierra | «el 31 de febrero no existe 🤔 ¿para cuándo era?» en vez de guardar cualquier cosa |
| Aguanta sin internet | si Gemini falla, interpreta con reglas locales y no pierde la tarea |

### No hace falta aprender comandos

Todo se puede pedir hablando normal; los comandos son un atajo, no el camino principal.

| Le escribís | Hace |
|---|---|
| «¿qué hay que hacer?» | lo mismo que `/todo` |
| «mostrame las de limpieza» | lo mismo que `/todo limpieza` |
| «mostrame la lista del súper» | lo mismo que `/super` |
| «ya limpié la heladera» | la tacha (y si era recurrente, crea la próxima) |
| «borrá la del plomero» | la borra; si hay varias parecidas, pregunta cuál |
| «decile a Axel mañana que lo amo» | se lo dice mañana a las 20:00 |
| «¿cómo funcionás?» | lo mismo que `/ayuda` |

### Comandos

```
/todo            todo lo pendiente, agrupado por cuándo vence
/todo limpieza   filtrado por categoría
/algundia        sólo lo que no tiene fecha
/super           la lista del super, con botones para tachar
/ayuda           cómo usarlo
/chatid          devuelve el chat_id (sirve para configurarlo la primera vez)
/recordatorios   dispara a mano la rutina de las 20:00 (modo prueba)
```

Alias: `/tareas` = `/todo`, `/compras` = `/super`, `/probar` = `/recordatorios`.

`/todo` agrupa por horizonte de tiempo, lo urgente arriba:

```
⚠️ VENCIDAS → HOY → MAÑANA → ESTA SEMANA → MÁS ADELANTE → ALGÚN DÍA → SÚPER
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

## Instalación

Son 4 pasos y unos 15 minutos. **No hace falta saber programar**: el instalador te va
guiando y validando todo.

### 1. Crear el bot (en Telegram)

Hablale a [@BotFather](https://t.me/BotFather) → `/newbot` → elegí nombre y username →
**guardá el token** que te da.

Después, en el mismo BotFather: `/mybots` → tu bot → **Bot Settings** → **Group
Privacy** → **Turn off**. ⚠️ Sin esto el bot sólo lee los comandos, no los mensajes
normales, que es casi todo lo que hace Notita.

### 2. Crear el grupo (en Telegram)

Un grupo con las personas de la casa y el bot adentro. No hace falta que busques el
`chat_id` ni los `user_id`: el instalador los detecta solos.

**¿Lo querés para vos solo?** Escribile por privado al bot y listo, sin grupo. El
instalador también detecta los chats privados.

### 3. Sacar la API key de Gemini (opcional)

En [Google AI Studio](https://aistudio.google.com/apikey) → *Create API key*. Es gratis y
no pide tarjeta. Si no querés, salteala: Notita funciona igual en [modo
local](#modo-local-sin-gemini).

> ⚠️ **Ojo con el modelo.** En la capa gratuita, `gemini-2.5-flash` da sólo **20 mensajes
> por día** (`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`), que no alcanza
> para una casa. Por eso el default es `gemini-flash-lite-latest`, que tiene mucha más.
> Si se agota, `doctor.py` te lo dice con nombre y apellido.

### 4. Instalar

Creá una cuenta gratis en [PythonAnywhere](https://www.pythonanywhere.com). Después, en
este orden:

> Acá se usa Python **3.13**, que es lo que traen las cuentas nuevas. Si la tuya es vieja
> y no lo tiene, corré `ls /usr/bin/python3.*` y usá esa versión en todos los comandos
> (la misma en la consola, en la web app y en la tarea diaria).

**a)** Pestaña *Web* → **Add a new web app** → **Manual configuration** → **Python 3.13**.
No toques nada más ahí: el archivo de configuración lo escribe el instalador.

**b)** Pestaña *Consoles* → **Bash**, y pegá esto:

```bash
git clone https://github.com/amarazzi/Notita.git ~/Notita
cd ~/Notita
pip3.13 install --user -r requirements.txt
python3.13 install.py
```

El instalador hace casi todo solo: te pide el token y la API key, **detecta el grupo y
quién vive en la casa** mirando quién escribe, **te avisa si el privacy mode quedó
encendido**, **sabe cuál es tu dirección** (no tenés que tipearla), escribe la
configuración, **deja lista la web app y la recarga**, y enchufa el webhook.

**c)** Lo único que queda a mano: pestaña *Tasks* → **Daily task** a las **23:00 UTC**
(= 20:00 en Argentina) con este comando, cambiando `TU_USUARIO`:

```
python3.13 /home/TU_USUARIO/Notita/run_reminders.py
```

Ya está: escribí en el grupo «hay que limpiar la heladera el lunes».

Si algo no anduvo, `python3.13 doctor.py` te dice qué falta.

> El instalador se puede volver a correr cuando quieras: lo que ya tenías se ofrece como
> respuesta por defecto (Enter lo deja igual), el `.env` anterior queda copiado en
> `.env.bak` y, si había un webhook andando, lo deja como estaba.

### Probarlo antes, sin tener nada

```bash
python3.13 install.py --demo
```

Recorre el instalador completo con un Telegram y un Gemini **de mentira**: no hace falta
token ni API key, no sale nada a internet y no se toca ninguna configuración. Sirve para
ver cómo es antes de empezar.

Si ya tenés todo y sólo querés ensayar contra tu bot real sin pisar el `.env`:

```bash
python3.13 install.py --env /tmp/prueba.env
```

---

## Paso a paso detallado

Lo de arriba alcanza. Esto es para cuando algo falla, o si preferís hacerlo a mano sin el
instalador.

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
El modelo por defecto es `gemini-flash-lite-latest` (ver el aviso de cuota más arriba).

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

Los recordatorios de vencimiento y el resumen de los domingos salen en la **corrida
principal**, a la hora de `NOTITA_HORA` (20:00 por defecto). Las cuentas gratuitas de
PythonAnywhere permiten una única tarea diaria, y con eso alcanza para todo salvo la
hora exacta (ver abajo).

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

#### ¿Y la hora exacta?

Una tarea con hora («el jueves a las 18») se recuerda **a esa hora**, pero para eso
alguien tiene que llamar a la rutina a esa hora. O sea:

| Cada cuánto corre | Qué pasa con «el jueves a las 18» |
|---|---|
| Una vez al día (opción A) | Se recuerda en la corrida de las 20:00, diciendo «era a las 18:00» |
| Cada 15 o 30 minutos (opción B) | Se recuerda a las 18:00 |

La corrida principal es, además, la red de seguridad: recuerda todo lo que vence ese día
aunque su hora ya haya pasado (o todavía no haya llegado), y nunca manda dos veces lo
mismo el mismo día. Lo mismo vale para los recados con demora («avisale en 10 minutos»):
con la opción A salen en la corrida de la noche.

Si querés la hora exacta, en cron-job.org poné el cronjob **cada 15 minutos** en vez de
una vez al día. Es gratis y la rutina es idempotente: si no hay nada para mandar, no
manda nada.

> Con cualquiera de las dos opciones, acordate de entrar cada 3 meses al botón
> *Run until 3 months from today* de la web app, o PythonAnywhere la desactiva.

### ¿Está corriendo lo último?

La web app dice qué commit tiene cargado, así que se puede chequear desde cualquier lado:

```bash
curl https://USUARIO.pythonanywhere.com/
# {"bot":"notita","ok":true,"version":"9ed9fac"}
```

Es el commit que tiene **cargado el proceso**, no el del archivo: si hacés `git pull` y
te olvidás del reload, sigue diciendo el viejo, que es justo lo que hay que saber.

Si no coincide con `git rev-parse --short HEAD`, falta actualizar:

```bash
cd ~/Notita && git pull && touch /var/www/USUARIO_pythonanywhere_com_wsgi.py
```

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
GEMINI_MODEL=gemini-flash-lite-latest
```

| Variable | Para qué |
|---|---|
| `TELEGRAM_TOKEN` | El token de BotFather |
| `ALLOWED_CHAT_ID` | La única puerta de entrada: el bot ignora cualquier otro chat |
| `TELEGRAM_WEBHOOK_SECRET` | Telegram lo manda en el header `X-Telegram-Bot-Api-Secret-Token`; el webhook rechaza lo que no coincida |
| `NOTITA_PERSONAS` | Quiénes viven en la casa: `Nombre:user_id` separados por coma |
| `NOTITA_CONTEXTO` | Opcional: dato libre de la casa para que el LLM acierte mejor |
| `NOTITA_TZ` | Opcional: zona horaria. Por defecto `America/Argentina/Buenos_Aires` |
| `NOTITA_HORA` | Opcional: a qué hora corre la rutina. Por defecto `20:00` |
| `CRON_SECRET` | Habilita `/cron/recordatorios` (opción B). **Vacío = ruta apagada** |
| `GEMINI_API_KEY` | La key de Google AI Studio |
| `GEMINI_MODEL` | Por defecto `gemini-flash-lite-latest` |
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
| «Lo anoté a mano, no me salió pensar» | Gemini falló y entró el modo local. Casi siempre es la **cuota del modelo**: `doctor.py` te dice cuál |
| Anota bien pero no entiende pedidos | Estás en modo local (sin key, o la cuota agotada): ahí los pedidos se reconocen por palabras clave, no siempre |
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
pytest                 # 520 tests, sin red ni API keys
python app.py          # http://localhost:5000
```

> Si te olvidás del `source .venv/bin/activate`, los scripts te avisan y te dicen qué
> python usar, en vez de tirarte un `ModuleNotFoundError`.

Para probar el webhook local podés usar cualquier túnel HTTPS y apuntarlo con
`set_webhook.py`.

---

## Plan B: en tu propio servidor (VPS, Oracle Always Free, una Raspberry)

Notita es una app Flask normal, así que corre en cualquier lado. Dos requisitos que
Telegram impone y conviene tener claros:

1. **El webhook necesita HTTPS con certificado válido.** No hay forma de usar `http://`
   ni una IP pelada. La vía más simple y gratis es [Caddy](https://caddyserver.com), que
   saca el certificado de Let's Encrypt solo, con un `Caddyfile` de dos líneas:

   ```
   notita.tudominio.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```

   Si no tenés dominio, un túnel tipo Cloudflare Tunnel o ngrok también sirve.

2. **La app tiene que estar siempre viva.** Con systemd:

   ```ini
   # /etc/systemd/system/notita.service
   [Service]
   WorkingDirectory=/opt/notita
   ExecStart=/opt/notita/.venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 app:app
   Restart=always
   [Install]
   WantedBy=multi-user.target
   ```

Después, el webhook y la rutina diaria:

```bash
python3 set_webhook.py https://notita.tudominio.com/telegram

# cron de la VM. Si la VM está en la zona de tu casa, poné la hora tal cual;
# si está en UTC, convertila (el default de Argentina son las 23:00 UTC).
0 20 * * *  cd /opt/notita && /opt/notita/.venv/bin/python run_reminders.py
```

Acá no hace falta `CRON_SECRET` ni la ruta `/cron/recordatorios`: con cron propio se
llama directo al script. Y `python3 doctor.py` sirve igual para verificar todo.

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
  deps.py               avisa qué falta instalar, en castellano
  demo.py               Telegram y Gemini de mentira para install.py --demo
  pythonanywhere.py     detecta la web app, escribe el WSGI y la recarga
  dates.py              fechas y recurrencias — módulo puro, con tests
  db.py                 SQLite
  llm.py                Gemini con responseSchema
  heuristica.py         interpretación sin LLM (modo local y fallback)
  telegram.py           cliente de la Bot API
  handlers.py           mensajes, comandos y botones
  reminders.py          recordatorios diarios y resumen semanal
  views.py              textos y teclados
tests/                  fechas, recurrencias, personas, LLM, instalador y flujo completo
docs/demo/              arma un GIF de demo actuando la conversación de verdad
.github/workflows/      CI: corre los tests en Python 3.10 y 3.13
```

Hay un generador de GIF de demostración (no se usa en el README). Corre la
conversación con el código real y la dibuja como un chat de Telegram; hace falta Google
Chrome y ffmpeg:

```bash
python3 docs/demo/generar_gif.py
```

## ¿Lo puedo usar en mi casa?

Sí. Forkealo o clonalo, corré `install.py` y listo. Se configura sin tocar código:

| Variable | Qué cambia |
|---|---|
| `NOTITA_PERSONAS` | Quiénes viven en la casa, cualquier cantidad |
| `NOTITA_TZ` | Zona horaria, ej. `Europe/Madrid`. Por defecto Buenos Aires |
| `NOTITA_HORA` | A qué hora se manda todo, ej. `09:30`. Por defecto `20:00` |
| `NOTITA_CONTEXTO` | Datos de la casa para que el LLM acierte mejor |

`NOTITA_HORA` **no** programa nada: eso lo hace el cron. Sirve para que los mensajes
digan la hora correcta y para que el instalador y `doctor.py` te calculen el horario en
UTC que hay que poner (`python3 -c "from notita import config; print(config.hora_rutina_en_utc())"`).

Las **categorías** (`limpieza`, `arreglos`, `tramites`, `pagos`, `mascotas`, `compras`,
`otros`) están en `config.CATEGORIAS`. Se pueden cambiar ahí: el LLM las toma de esa
lista, no hay que tocar el prompt.

### Lo que sí requiere trabajo

**Está escrito en español rioplatense, y eso no es una variable de entorno.** Para usarlo
en otro idioma hay que traducir los textos de `views.py`, `handlers.py` y `reminders.py`,
el prompt de `llm.py`, y —lo más laborioso— el parser de fechas de `dates.py` y las
palabras clave de `heuristica.py`, que están en castellano. Es un trabajo de un rato
largo, no de configuración.

La **semana va de lunes a domingo** (relevante para «esta semana» y el resumen) y el
**resumen semanal sale los domingos**; las dos cosas están en el código.

## Fuera del MVP

Audios, WhatsApp y cualquier cosa que cueste plata.

### Licencia

MIT, ver [LICENSE](LICENSE).
