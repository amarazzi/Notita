# Notita 🧲

Bot de Telegram que hace de todolist compartida de la casa.

**Hablando se anota; tocando se gestiona.** Le escribís como le hablarías a una persona
y él anota; para manejar lo anotado hay un tablero fijado en el grupo con un botón por
tarea. El lenguaje natural es buenísimo para capturar y malo para modificar, así que
ninguna modificación se ejecuta sin que alguien toque un botón
([por qué](docs/DISEÑO-v2.md)).

- Lenguaje natural con **Gemini** (capa gratuita), con salida estructurada en JSON.
  Es opcional: sin API key funciona igual, con reglas locales.
- **Las fechas las calcula el código**, nunca el LLM.
- **SQLite**, sin servidor de base de datos.
- **100% gratis**: PythonAnywhere free + Gemini free tier.
- Zona horaria configurable; por defecto `America/Argentina/Buenos_Aires`.
- Sirve para **cualquier casa**: los nombres y la cantidad de personas salen del `.env`.

---

## Qué hace

### Anotar: hablando

| | |
|---|---|
| Carga en lote | «hay que limpiar la heladera, llamar al plomero y falta leche» → 3 cosas, cada una en su lugar |
| Fechas relativas | «mañana», «el jueves», «el finde», «todos los 10», «en 3 días» |
| Fecha y hora | «llevar a Milo al veterinario el jueves a las 18» → guarda la hora y te da un botón para pasarlo al calendario |
| Responsable | «Barbu tiene que llamar al veterinario» → queda a nombre de Barbu |
| Lista del súper | «falta leche» va al súper. Un mueble o algo de ferretería es tarea, aunque diga «comprar» |
| Recurrentes | «cambiar las piedritas cada semana», «regar cada 3 días» 🔁 |
| Recados | «decile a Barbu que ya salí» → se lo dice en el momento, mencionándola |
| Madrugada | a las 00:40, «mañana» es hoy (y te da un botón para corregirlo si no) |
| Nunca bloquea | si algo no se entiende o la fecha no existe, lo guarda igual y ofrece corregir |
| Nunca miente | la confirmación se arma leyendo la base. Si no se pudo guardar, lo dice |
| Sin duplicados | si anotás algo que ya estaba, te lo dice en vez de guardarlo dos veces |
| Aguanta sin Gemini | si falla o no hay cuota, interpreta con reglas locales |

### Gestionar: tocando

El **tablero** es un mensaje fijado en el grupo que se edita en el lugar:

```
📋 La casa · lunes 28/9

⚠️ VENCIDAS · 1
1. 🧽 Agarrar sábanas y acolchado · ayer, dom 27

HOY · lun 28 · 2
2. 🐾 Llevar a Milo al veterinario · 🕕 18:00 · Barbu
3. 🔧 Comprar cómoda para la habitación

ESTA SEMANA · 3 ›
🛒 SÚPER · 4 ›

[ ✅ 1. Agarrar sábanas y acolchado ]
[ ✅ 2. Llevar a Milo al veterinario ]
[ ✅ 3. Comprar cómoda para la habitación ]
[ 📂 Esta semana · 3 ]
[ 🛒 Súper · 4 ]
[ ⋯ Cambiar algo ]
```

- **✅** la da por hecha de un toque. Sin mensajes nuevos en el grupo. Se lleva la fila
  entera porque es lo que más se toca: blanco grande y título legible.
- El **número** ata cada botón con su renglón: Telegram pone los botones todos juntos
  abajo, fuera de las secciones.
- **⋯ Cambiar algo** lista las tareas para elegir cuál: mover de día, quién la hace,
  renombrar, mandar al súper, borrar o agregar al calendario.
- Si tachás algo sin querer, aparece **↩️ Deshacer** en el tablero por 10 minutos.
- Cada tarea aparece **en una sola sección**. Las colapsadas se abren en un mensaje
  aparte, así lo que toca uno no le cambia la pantalla al otro.

### Pedir por texto: propone y vos confirmás

Si pedís un cambio hablando, Notita **no lo ejecuta**: te lo propone.

```
vos → ya compré la leche y la lavandina
      ¿Tacho estas 2?
      · Leche
      · Lavandina
      [ ✅ Leche ] [ ✅ Lavandina ]
      [ ✅ Las dos ] [ No ]
```

Funciona igual para borrar, mover («pasá todo lo de mañana para hoy»), renombrar,
cambiar el responsable, vaciar el súper y pausar a Notita. Si el modelo entendió mal,
no pasó nada.

### El parte diario

A las 20:00 (configurable) llega el único mensaje que suena:

```
🌙 Para mañana, lun 28
🕕 18:00 · Llevar a Milo al veterinario · Barbu
📌 Comprar la cómoda · los dos
⚠️ Quedó de hoy: Agarrar sábanas y acolchado
[ ✅ Ya está ] [ ⏰ A mañana ]
🛒 En el súper hay 4 cosas
```

**Nada se mueve solo:** una tarea vencida queda vencida hasta que alguien toque.

### Lo que NO hace

- **No avisa a horas exactas.** Un cron gratuito no da esa garantía, así que para los
  turnos te da un botón **📅 Agregar al calendario** (Google Calendar o un `.ics`).
- **No manda recados para más tarde.** «Decile mañana que compre pan» se convierte en
  una tarea de esa persona para ese día.
- **No cambia el ritmo de una recurrente**: se borra y se anota de nuevo.

### Comandos

```
/tablero   publica el tablero al final del chat y lo fija (también: escribir «tablero»)
/super     la lista del súper, con botones para tachar
/ayuda     cómo usarlo
/parte     manda el parte a mano (modo prueba)
/chatid    devuelve el chat_id (sirve para configurarlo la primera vez)
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
python3.13 /home/TU_USUARIO/Notita/run_parte.py
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

### 7. El cron: una corrida cada 15 minutos

El **parte diario** sale una sola vez por día, a la hora de `NOTITA_HORA` (20:00 por
defecto). Pero conviene que el cron corra cada 15 minutos, porque cada corrida además
limpia los menús que quedaron abiertos y manda lo que no se pudo enviar antes. El parte
no se duplica: la tabla `partes_enviados` tiene la fecha como clave.

#### Opción A — Cron externo (recomendada)

Poné un `CRON_SECRET` en el `.env`, recargá la web app y creá un cronjob gratuito en
[cron-job.org](https://cron-job.org):

- **URL**: `https://USUARIO.pythonanywhere.com/cron/recordatorios`
- **Execution schedule**: *Every 15 minutes* (o custom, minutos `*/15`)
- **Advanced** → **Headers**: `X-Cron-Secret` = el valor de `CRON_SECRET`

Y avisale a Notita cada cuánto corre:

```
NOTITA_CRON_MINUTOS=15
```

La clave va **en el header, nunca en la URL**: las query strings quedan guardadas en el
access log del servidor. Si falta o no coincide, la ruta responde 403.

Para probarla a mano:

```bash
curl -H "X-Cron-Secret: TU_CRON_SECRET" \
     https://USUARIO.pythonanywhere.com/cron/recordatorios
```

#### Opción B — Tarea diaria de PythonAnywhere

Sin depender de nadie más, pero una sola corrida por día. *Tasks* → **Daily task**.
PythonAnywhere programa en **UTC**, y Argentina es UTC−3 todo el año:

| Hora Argentina | Hora que ponés en PythonAnywhere |
|---|---|
| 20:00 | **23:00 UTC** |

```
python3.13 /home/USUARIO/Notita/run_parte.py
```

Con esta opción **dejá `CRON_SECRET` vacío**: así la ruta `/cron/recordatorios` queda
apagada (responde 404) y no hay una puerta extra abierta. La contra es que los menús
vencidos se limpian sólo una vez por día.

#### ¿Y los avisos a la hora exacta?

No existen, a propósito. Un cron gratuito no garantiza el minuto, y prometer un aviso
que no llega es peor que no prometerlo. Cuando una tarea tiene hora, Notita la guarda, la
muestra (`🕕 18:00`) y te da un botón **📅 Agregar al calendario**, que sí sabe avisar.

> Con cualquiera de las dos opciones, acordate de entrar cada 3 meses al botón
> *Run until 3 months from today* de la web app, o PythonAnywhere la desactiva.

### ¿Está corriendo lo último?

La web app dice qué commit tiene cargado, así que se puede chequear desde cualquier lado:

```bash
curl https://USUARIO.pythonanywhere.com/
# {"actualizado":true,"bot":"notita","en_disco":"2b63287","ok":true,"version":"2b63287"}
```

- `version`: el commit que tiene **cargado el proceso**.
- `en_disco`: el que hay en la carpeta.
- `actualizado`: si son el mismo.

Si `actualizado` es `false`, el `git pull` entró pero **falta recargar la web app**.

### Recargar de verdad

```bash
cd ~/Notita && git pull && touch /var/www/USUARIO_pythonanywhere_com_wsgi.py
```

Ojo: tocar el archivo WSGI hace un **reload parcial**, y a veces los workers se
reinician con el código viejo. Si después del `touch` el `curl` sigue diciendo
`actualizado: false`, usá la forma segura: pestaña **Web** → botón **Reload**.

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
| `NOTITA_CRON_MINUTOS` | Opcional: cada cuántos minutos corre el cron. Se recomienda `15` |
| `NOTITA_CALENDARIO` | Opcional: `google` (link) o `ics` (archivo). Por defecto `google` |
| `NOTITA_PARTE_VACIO` | Opcional: `0` para que no mande el parte cuando no hay nada |
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
escribible, si el tablero está fijado y cuándo salió el último parte. Sale con código 1 si encontró
algo roto.

| Síntoma | Causa más común |
|---|---|
| No contesta nada | Falta el Reload, o hay un error en el *Error log* de la pestaña Web |
| Se quedó mudo de golpe | Telegram convirtió el grupo en supergrupo (pasa al hacer admin a alguien) y le cambió el número. Notita se muda sola y te lo dice; si el aviso se perdió: `python3 migrar_chat.py EL_NUMERO_NUEVO` |
| Sólo contesta los comandos | Privacy mode encendido: apagalo y re-agregá el bot al grupo |
| Anota raro o no separa bien | Gemini falló y entró el modo local. Casi siempre es la **cuota del modelo**: `doctor.py` te dice cuál |
| El tablero no queda fijado | El bot no es admin con permiso de fijar. Funciona igual, pero como mensaje suelto |
| Los menús quedan colgados | El cron no está corriendo: es el que limpia los mensajes temporales |
| Mensajes encolados creciendo | La web app está caída y Telegram sigue reintentando |

---

## Modo prueba (sin esperar al parte)

```bash
python3 run_parte.py --forzar                      # dispara ahora mismo
python3 run_parte.py --forzar --fecha 2026-10-04   # simula otro día
```

También desde el grupo: `/parte`.

---

## Desarrollo local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env
pytest                 # 426 tests, sin red ni API keys
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
*/15 * * * *  cd /opt/notita && /opt/notita/.venv/bin/python run_parte.py
```

Acá no hace falta `CRON_SECRET` ni la ruta `/cron/recordatorios`: con cron propio se
llama directo al script. Y `python3 doctor.py` sirve igual para verificar todo.

---

## Estructura

```
install.py              instalador guiado: escribe el .env y enchufa el webhook
doctor.py               diagnóstico: qué está mal y cómo se arregla
app.py                  webhook Flask (lo que sirve PythonAnywhere)
run_parte.py        el parte diario (cron / scheduler / modo prueba)
migrar_chat.py      mudar todo si Telegram le cambia el número al grupo
set_webhook.py          alta, consulta y baja del webhook
notita/
  config.py             .env, personas, categorías, zona horaria
  dates.py              TODA la aritmética de fechas y recurrencias
  db.py                 SQLite: tareas, tablero, propuestas, deshacer, partes
  telegram.py           cliente de la Bot API (con reintentos y cola de salida)
  llm.py                Gemini: schema, prompt y parseo
  heuristica.py         el modo local, sin LLM
  handlers.py           qué hacer con cada mensaje y cada toque
  tablero.py            el mensaje fijado que se edita en el lugar
  menus.py              los menús temporales (⋯, secciones, súper)
  propuestas.py         el texto propone, el botón ejecuta
  parte.py              el parte diario (lo único programado)
  calendario.py         el botón «Agregar al calendario»
  cb.py                 el callback_data versionado
  views.py              textos y formato
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
en otro idioma hay que traducir los textos de `views.py`, `handlers.py`, `menus.py`,
`propuestas.py`, `tablero.py` y `parte.py`,
el prompt de `llm.py`, y —lo más laborioso— el parser de fechas de `dates.py` y las
palabras clave de `heuristica.py`, que están en castellano. Es un trabajo de un rato
largo, no de configuración.

La **semana va de lunes a domingo** (relevante para «esta semana» y para las secciones
del tablero) y eso está en el código.

## Fuera del alcance

Audios, fotos, WhatsApp y cualquier cosa que cueste plata. Y, por decisión de diseño,
los avisos a hora exacta: para eso está el botón de calendario.

### Licencia

MIT, ver [LICENSE](LICENSE).
