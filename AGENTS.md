# Trabajar en Notita

Guía para quien toque este código: persona o modelo. El README explica cómo **usar**
Notita; esto explica cómo **está hecha** y con qué hay que tener cuidado.

## Qué es, en cinco líneas

Un bot de Telegram que hace de lista de tareas compartida de una casa. Vive en un
grupo. Le escribís (o le mandás un audio) y anota; lo anotado se maneja tocando
botones en un **tablero fijado** que se edita en el lugar. Corre gratis en
PythonAnywhere con SQLite, y usa Gemini (capa gratuita) para entender el lenguaje,
con un modo local por si no hay key o falla.

## Los cinco principios

Son invariantes, no preferencias. Si un cambio rompe alguno, está mal el cambio.
El porqué de cada uno está en [docs/DISEÑO-v2.md](docs/DISEÑO-v2.md).

1. **Hablando se anota; tocando se gestiona.** El texto libre (y el audio) sólo
   CREAN. Nada que ya existe se modifica desde texto.
2. **El lenguaje propone y el toque confirma.** Un pedido de modificación se
   interpreta, se resuelven candidatos y se ofrecen botones. Si el modelo entendió
   mal, no pasó nada.
3. **Nunca bloquear.** No hay estados «esperando respuesta» que se coman el mensaje
   siguiente. Se guarda con la mejor interpretación y se ofrece corregir.
4. **Todo lo que dice es verdad.** Las confirmaciones se arman **leyendo la base
   después de escribir**, nunca con lo que devolvió el LLM. Si la escritura falla, se
   dice.
5. **Un solo momento de reloj por día:** el parte. No hay avisos a hora exacta ni
   nada programado además de eso.

Dos corolarios que se violan sin querti:

- **Hay una sola clase de cosa.** Texto, fecha, hora, responsable, recurrencia y la
  etiqueta `compra` (🛒). La etiqueta sólo filtra: no cambia cómo se guarda ni cómo se
  nombra. Y el texto se guarda **como lo dijeron**: no se le saca ningún verbo.
- **El LLM no decide sobre datos que existen.** Los ids de una propuesta los resuelve
  el servidor contra la base. Y si el modelo devuelve algo que el mensaje no dice
  (por ejemplo, un responsable), se descarta: ver `handlers._responsable`.
- **El tablero nunca navega.** Los menús son mensajes nuevos y temporales. Lo
  comparten dos personas: si uno abriera un submenú ahí, al otro le cambiaría la
  pantalla.

## Mapa

```
app.py                webhook Flask + GET / (versión) + /cron/recordatorios
run_parte.py          el parte a mano (cron, scheduler o prueba)
install.py            instalador interactivo (tiene --demo, sin red)
doctor.py             diagnóstico: entorno, bot, webhook, esquema, tablero, parte
migrar_chat.py        mudar todo si Telegram le cambia el número al grupo
set_webhook.py        alta/consulta/baja del webhook + menú de comandos

notita/
  config.py           .env, personas, zona horaria, VERSION (commit cargado)
  dates.py            TODA la aritmética de fechas y recurrencias
  db.py               SQLite: esquema, migraciones y consultas
  telegram.py         cliente de la Bot API: reintentos, 429, cola de salida
  llm.py              Gemini: schema, prompt, interpretación y transcripción
  heuristica.py       el modo local, sin LLM
  audio.py            notas de voz: tope, aviso y transcripción
  handlers.py         qué hacer con cada mensaje y cada toque
  tablero.py          el mensaje fijado que se edita en el lugar
  menus.py            menús temporales (⋯, secciones, compras)
  propuestas.py       el texto propone, el botón ejecuta
  parte.py            el parte diario
  calendario.py       el botón «Agregar al calendario»
  cb.py               callback_data versionado (64 bytes de límite)
  views.py            textos y formato (acá no se decide nada)
```

Flujo de un mensaje: `app.py` → `handlers.handle_update` → dedupe → audio (si hay) →
`llm`/`heuristica` → crear o proponer → responder leyendo la base → `tablero.actualizar`.

## Cómo verificar

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest pyflakes
pytest          # ~470 tests, en segundos
pyflakes notita/ tests/ *.py
```

Los tests **no salen a internet**: `tests/conftest.py` corta `Session.request` y
levanta `SalidaAInternet` si algo lo intenta. Eso es a propósito: si un test necesita
red, está mal escrito. Telegram se simula reemplazando `telegram.llamar`, y el LLM
con `monkeypatch` sobre `llm.interpretar_mensaje`.

Para probar contra la API real de Gemini (schema, prompt, transcripción) hay que
hacerlo en un script aparte, con la key, **nunca en los tests**.

## Convenciones

- **Todo en castellano**: nombres, comentarios, docstrings y tests. El producto habla
  rioplatense y el código también.
- **Los comentarios explican el porqué, no el qué.** Muchos cuentan un bug real; no
  los borres sin entender qué cuidaban.
- **Un test por bug**, con el caso real en el docstring. Y antes de darlo por bueno,
  comprobar que **falla** contra el código viejo.
- **Los tests no dependen del día en que corren.** Usá fechas fijas; ya nos rompió
  una suite entera un domingo.

## Trampas que ya nos costaron caro

Todas pasaron de verdad. Están acá para no repetirlas:

| Trampa | Qué pasó |
|---|---|
| `CREATE TABLE IF NOT EXISTS` **no migra** una tabla que ya existe | Cambié el tipo de `updates_vistos.update_id` y en producción siguió siendo el viejo: **todos los botones quedaron muertos, en silencio** |
| `CREATE INDEX IF NOT EXISTS` tampoco redefine un índice | El índice de v1 seguía nombrando `tipo`, y eso **bloqueaba el `DROP COLUMN`**: «error in index idx_tasks_estado after drop column». Hay que borrar el índice y dejar que el esquema lo recree |
| Las migraciones versionadas **saltean** a quien ya migró | El arreglo de esa tabla estaba adentro de la migración a v2, que sale temprano si ya corrió: justo las bases que lo necesitaban no lo recibían. Los arreglos de forma van en `init_db`, sueltos |
| Telegram **corta los botones al medio** | Un botón que comparte fila tiene media pantalla. Lo largo va en el texto; hay un test que recorre todos los botones |
| Un error de SQLite no es siempre «repetido» | Tomar cualquier `IntegrityError` por duplicado hizo que se descartaran todos los toques. Ahora se comprueba si la fila está |
| Tocar el WSGI en PythonAnywhere hace un **reload parcial** | El código nuevo puede no cargarse. Por eso `GET /` devuelve `version` y `en_disco`: si no coinciden, falta el botón **Reload** |
| El proxy de PythonAnywhere falla de a ratos | Por eso existe la cola de salida (`salientes`): un mensaje que no sale queda guardado y se reintenta |
| Postergar la edición del tablero | Un debounce de 3 segundos hacía que el ✅ no se reflejara: se tocaba el botón y «no pasaba nada» |
| Confirmar con lo que devolvió el LLM | Anunció «cómoda a compras» sin haber guardado nada. De ahí el principio 4 |
| Que el tipo cambie **cómo** se guarda algo | A las compras se les sacaba el verbo: rompía los duplicados y perdía palabras («Regalo para mamá») |
| Un `message_id` es de **un** chat | Al mudarse de grupo no se migran: se descartan y el tablero se publica de cero |

## Deploy

`main` es lo que corre. En PythonAnywhere:

```bash
cd ~/Notita && git pull
# pestaña Web → botón Reload (el touch del WSGI no siempre alcanza)
curl -s https://USUARIO.pythonanywhere.com/     # actualizado: true
python3.13 doctor.py                            # tiene que estar todo en ✓
```

### Volver atrás

**El código y el esquema de la base viajan juntos.** Una versión vieja del código no
sabe leer una base migrada: si volvés el código sin restaurar la base, el bot se cae.

```bash
cd ~/Notita
ls -la notita.db.antes-de-*.bak         # 1. mirá qué respaldos hay
mv notita.db notita.db.rota             # 2. NO borres la base actual
cp notita.db.antes-de-v3.AAAA-MM-DD.bak notita.db   # 3. restaurá la del día
git checkout <commit-anterior>          # 4. y recién ahora el código
```
Después **Reload** desde la pestaña Web, y verificá con `curl` que `version` sea el
commit al que volviste.

El respaldo lo hace la propia migración, **antes** de tocar nada, con la fecha en el
nombre y sin pisar ninguno anterior (`_nombre_de_respaldo` en `db.py`). Si el archivo
no está, la migración no corrió.

El nombre dice de qué te salva: `antes-de-v3` es de antes del cambio de esquema (el
que querés para volver a una versión vieja del código) y `antes-de-limpiar`, de antes
de borrar columnas que ya no se usaban.

Para probar la app a mano, seguí [docs/PROBAR.md](docs/PROBAR.md).
