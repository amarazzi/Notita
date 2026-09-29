# Notita v2 — diseño

> Este documento se escribió **antes** de codear. Si el código y esto no coinciden,
> es un bug de alguno de los dos.

## Por qué cambia el modelo

En v1 el LLM interpretaba texto libre para todo: anotar, borrar, completar, mover,
renombrar y acciones en bloque. Cada día aparecía una familia nueva de bugs, y casi
todos tenían la misma forma: **el modelo entendió algo distinto y el bot lo ejecutó**.
Peor: a veces confirmaba algo que no había guardado.

La conclusión no es "mejorar el prompt". Es que el lenguaje natural es excelente para
**capturar** y malo para **modificar**, porque modificar necesita certeza sobre *cuál*
de las cosas que existen. Y la precisión horaria no es confiable con un cron gratuito.

## Los cinco principios

1. **Hablando se anota; tocando se gestiona.** El texto libre sólo crea.
2. **El lenguaje propone y el toque confirma.** Un pedido de modificación se interpreta
   y se ofrece con botones; no se ejecuta solo.
3. **Nunca bloquear.** Notita siempre guarda con su mejor interpretación y ofrece
   corregir. No hay estados "esperando respuesta" que traguen el próximo mensaje.
4. **Todo lo que dice es verdad.** Las confirmaciones se arman leyendo de la base
   *después* de escribir. Si la escritura falla, lo dice.
5. **Un solo momento de reloj por día.** El parte diario. Nada más.

El principio 3 mata una familia entera de bugs de v1 (el `pending` que se comía el
mensaje siguiente). El principio 4 mata otra (confirmar lo que el LLM devolvió, no lo
que se guardó: la cómoda que se anunció "a compras" y no estaba en ningún lado).

## Máquina de estados por tipo de mensaje

### Mensaje de texto en el grupo autorizado

```
texto
 ├─ es respuesta a un force_reply nuestro (renombrar / escribir fecha)
 │    └─ se aplica a ESE ítem, sin pasar por el LLM  → actualizar tablero
 ├─ es comando (/tablero, /todo, /ayuda, /chatid, ...)
 │    └─ responder
 └─ interpretar (LLM, o heurística local si no hay Gemini)
      ├─ intención = crear      → guardar TODO en una transacción
      │                           → releer de la base
      │                           → confirmar + [✏️ Corregir] [↩️ Deshacer]
      │                           → actualizar tablero
      ├─ intención = modificar  → resolver candidatos (fuzzy, en el servidor)
      │                           → guardar propuesta (TTL 30')
      │                           → mostrar botones. NO ejecuta nada.
      ├─ intención = recado     → mandar el mensaje al instante, mencionando
      │                           (si tiene fecha futura: es una tarea, no un recado)
      └─ intención = charla     → respuesta corta, no crea nada
```

No hay ningún camino en el que un texto modifique algo existente.

### El tablero, como quedó

**El texto es para leer; los botones, sólo para hacer.** La versión anterior mostraba
las secciones como texto **y** como botón, y el deshacer como renglón **y** como
botón: el doble de alto para la misma información, y las tareas —lo único que
importa— quedaban abajo, escondidas.

- El teclado de Telegram va **abajo del texto**, no intercalado, así que el **número**
  es lo único que ata un renglón con su botón. Por eso el botón es sólo `✅ 3`: el
  texto ya dice qué es, con la fecha, la hora, el responsable y la recurrencia.
- **Es UNA lista de cosas para hacer, no una agenda.** Sin encabezados por día: con
  ellos se leía como un calendario y lo que no tenía fecha parecía de otra categoría,
  cuando es una cosa para hacer como cualquier otra. Cada renglón lleva su fecha **si
  la tiene**; a lo que no tiene no le falta nada, así que no dice «sin fecha».
- **El orden no es el del calendario: es cuánto te está pidiendo atención.** Vencidas,
  hoy, mañana, **sin fecha**, esta semana, más adelante.
- **Lo sin fecha va antes de lo que tiene día futuro**, y esa es la parte menos obvia:
  algo con fecha **va a aparecer solo** cuando llegue su día; algo sin fecha no aparece
  nunca si no se lista. Reducido a un número, se podre ahí para siempre.
- Se listan hasta **15** cosas. **Con la casa tranquila eso es todo**: cinco pendientes
  son cinco renglones y cinco botones. Cuando se desborda, el resto se cuenta en una
  línea («y 3 más: Esta semana 2 · Más adelante 1»), que es lo único que importa de
  algo que no se está mostrando, y sigue accesible por el `⋯`.
- **Un renglón en blanco entre bloques** (encabezado / lista / el resto), pero no
  dentro de la lista: es una lista, no cinco secciones.
- **La hora va aparte y con el reloj.** Pegada a la fecha quedaba «mañana, mié 30
  18:00», que se lee como un número suelto.
- Lo que no tiene fecha se llama **«Sin fecha»** donde hay que nombrarlo (el menú de
  fechas, el resumen), no «Algún día»: no es una lista de deseos, es el estado de una
  cosa que todavía no tiene día.
- **`⋯ Cambiar algo` lista TODO**, incluidas las compras sin fecha y lo que no entró.
  Ninguna cosa queda inaccesible por no tener botón propio.
- El **`↩️ Deshacer`** es una fila que aparece 5 minutos después de tachar o borrar, y
  se va. Antes vivía en el tablero y también como renglón de texto: algo que se queda
  deja de ser una oportunidad y pasa a ser parte del mueble.

### Notas de voz

Entran por la misma puerta que el texto, y por eso heredan todo lo demás:

```
voice → ¿duration <= NOTITA_AUDIO_SEGUNDOS?   (ANTES de descargar nada)
      → «🎤 Escuchando el audio…»
      → telegram.descargar → llm.transcribir  (Ogg/Opus, sin transcodificar)
      → se borra el aviso
      → el texto transcripto sigue el flujo normal, con el renglón «🎤 «…»» adelante
```

Verificado contra la API real antes de construirlo: Gemini transcribe el Ogg/Opus de
Telegram tal cual, y con ruido o silencio devuelve `NO_SE_ENTIENDE` en vez de
inventar. El tope de 15 segundos **no es por cuota** (son ~400 tokens) sino por la
espera: 14 segundos de audio tardaron 20 en transcribirse.

El aviso se borra y la respuesta se manda nueva, en vez de editar el aviso: así la
respuesta pasa por `telegram.enviar`, que tiene cola de salida. Editando, un fallo la
perdería.

### Callback (toque de botón)

```
callback
 ├─ ¿ya lo procesamos? (callback_query.id en updates_vistos) → answerCallbackQuery y listo
 ├─ acción sobre un ítem (✅, ⋯, fecha, quién, renombrar, borrar, compras)
 │    ├─ el ítem ya no existe o ya está resuelto
 │    │    → answerCallbackQuery("Eso ya estaba resuelto ✨") + refrescar la vista
 │    └─ ejecutar en una transacción → answerCallbackQuery corto → actualizar tablero
 ├─ propuesta (prop:<id>:<opción>)
 │    → validar ítem por ítem; ejecutar los que sigan pendientes
 │    → editar el mensaje de la propuesta con el resultado y sacarle los botones
 ├─ deshacer (und:<id>) → si venció: answerCallbackQuery("Ya no se puede deshacer")
 └─ sección / compras / cerrar → mensaje temporal
```

### Cron (cada 15 minutos)

```
cron
 ├─ vaciar cola de salida (mensajes que no se pudieron mandar)
 ├─ parte diario, si corresponde (ver más abajo)
 ├─ limpiar mensajes temporales vencidos
 ├─ vencer deshacer y propuestas
 └─ flushear el tablero si quedó sucio
```

## Esquema de `callback_data`

Telegram limita a **64 bytes**. Formato: `v|acción|arg1|arg2`, con `v=2` para poder
convivir con botones viejos de v1 (que tienen otra forma y se responden con "Ese botón
es de la versión anterior").

| Acción | Forma | Ejemplo | Bytes típicos |
|---|---|---|---|
| Completar | `2\|ok\|<id>` | `2\|ok\|417` | 9 |
| Abrir menú ⋯ | `2\|m\|<id>` | `2\|m\|417` | 8 |
| Mover a día rápido | `2\|d\|<id>\|<h\|m\|s\|l\|a>` | `2\|d\|417\|m` | 10 |
| Submenú de día | `2\|d+\|<id>` | `2\|d+\|417` | 9 |
| Pedir fecha escrita | `2\|df\|<id>` | `2\|df\|417` | 9 |
| Responsable | `2\|q\|<id>\|<slug>` | `2\|q\|417\|barbu` | 15 |
| Renombrar | `2\|r\|<id>` | `2\|r\|417` | 8 |
| Mover a compras / a tareas | `2\|sw\|<id>` | `2\|sw\|417` | 9 |
| Borrar | `2\|x\|<id>` | `2\|x\|417` | 8 |
| Borrar recurrente | `2\|x\|<id>\|<una\|todas>` | `2\|x\|417\|todas` | 14 |
| Sección | `2\|sec\|<clave>` | `2\|sec\|semana` | 12 |
| Compras | `2\|sup` | `2\|sup` | 5 |
| Tachar todo compras | `2\|supx` | `2\|supx` | 6 |
| Propuesta | `2\|p\|<id>\|<opción>` | `2\|p\|93\|todas` | 14 |
| Deshacer | `2\|u\|<id>` | `2\|u\|93` | 7 |
| Madrugada | `2\|mad\|<id>` | `2\|mad\|93` | 9 |
| Cerrar | `2\|c` | `2\|c` | 3 |
| Elegir a cuál abrirle el ⋯ | `2\|elegir` | `2\|elegir` | 9 |
| Corregir lo recién anotado | `2\|fix\|<id>` | `2\|fix\|93` | 9 |
| Submenú de responsable | `2\|q+\|<id>` | `2\|q+\|417` | 9 |
| Publicar el tablero | `2\|tab` | `2\|tab` | 5 |
| Pasar todas a mañana | `2\|pt` | `2\|pt` | 4 |
| Confirmar pausa | `2\|pz\|<id>` | `2\|pz\|93` | 8 |
| Mandar el .ics | `2\|ics\|<id>` | `2\|ics\|417` | 11 |

Los ids son enteros de la base y los slugs vienen de `NOTITA_PERSONAS`. El peor caso
(un slug largo) queda holgadamente bajo 64 bytes; hay un test que lo verifica para
todos los botones que genera el código.

## Datos

Tablas que se agregan:

| Tabla | Para qué |
|---|---|
| `tablero` | `chat_id`, `message_id`, `editado_en`, `sucio` |
| `mensajes_temporales` | menús y vistas que se borran solas (`expira_at`, `tipo`) |
| `propuestas` | lo que el texto propuso, sin ejecutar (`payload` JSON, TTL 30') |
| `deshacer` | qué ítems creó un mensaje (TTL 10') |
| `partes_enviados` | una fila por día: el parte sale una sola vez |
| `pausa` | hasta cuándo Notita no manda el parte |
| `ajustes` | banderitas sueltas (ej. si ya se mandó la bienvenida de v2) |

En `tasks` se agrega `mensaje_origen_id` (para deshacer y para saber de qué mensaje
salió). Ya existían `due_hora`, `created_by`, `completed_by` y `completed_at`.

Se **borran** `recordada_veces` y `last_reminded_on`: eran del auto-posponer y del
recordatorio por tarea, que no existen más.

## El parte diario

Es el único envío programado y el único con notificación.

- Sale a `NOTITA_HORA` (default 20:00) en `NOTITA_TZ`.
- `partes_enviados` tiene la fecha como clave primaria: el cron puede llamar mil veces.
- No sale antes de `NOTITA_HORA`. Si son las 23:59 y no salió, **no se manda tarde**:
  al otro día, la primera corrida manda "Perdón, anoche se me pasó el parte 🙈" y el
  parte de hoy.
- **Nada se mueve solo.** Una vencida queda vencida hasta que alguien toque un botón.

## Decisiones que tomé y no estaban en el brief

1. **La tabla sigue llamándose `tasks` y la base `notita.db`** (el backup es
   `notita.db.v1.bak`). El brief dice `items.db`; renombrar la tabla obligaría a una
   migración de datos con riesgo y cero beneficio.
2. **Los candidatos de una propuesta los resuelve el servidor, no el LLM.** El brief
   dice que el LLM devuelve "candidatos (ids de la base)", pero el modelo no conoce los
   ids: mandárselos sería darle de nuevo el poder de elegir mal. El LLM devuelve las
   *palabras* con las que nombraron cada cosa y el servidor las resuelve contra la base
   con el matcher que ya existe. El resultado visible es el mismo y el modelo no puede
   inventar un id.
3. ~~**El debounce del tablero es por update, no por temporizador.**~~ **Revertido con
   el uso real.** Había un debounce de 3 segundos para no golpear los límites de
   Telegram, y el efecto fue peor que el problema: el tablero se acababa de publicar,
   así que el primer ✅ caía dentro de esos 3 segundos, la edición se posponía y el
   tablero seguía mostrando la tarea que ya estaba hecha. Se tocaba el botón y «no
   pasaba nada». Ahora se edita siempre, al final de cada update: un tablero que miente
   es mucho peor que una llamada de más, y el ritmo de los toques lo pone un dedo. Si
   la edición falla, queda `sucio` y lo reintenta el próximo evento.
4. **`updates_vistos` guarda también los `callback_query.id`**, con el mismo mecanismo
   (el INSERT es la guarda). Son strings, así que la columna pasa a TEXT.
5. **Los force_reply (renombrar, escribir fecha) usan la tabla `pending` que ya existe**,
   con `kind='renombrar'` y `kind='fecha_item'`. Pero a diferencia de v1, **no se
   consumen del mensaje siguiente cualquiera**: se aceptan sólo si el mensaje es una
   *respuesta* (`reply_to_message`) al force_reply. Si no, el mensaje se procesa normal.
   Esto es lo que hace que el principio 3 se cumpla de verdad.
6. **La detección de duplicados es por texto normalizado exacto**, no difusa. En v1 el
   fuzzy tachaba pantuflas cuando comprabas pan.
7. **"Esta semana" llega hasta el domingo inclusive.** Un domingo la sección queda
   vacía y lo del lunes cae en "Mañana".
8. **El menú "⋯" y las secciones son mensajes nuevos, no ediciones del tablero.** El
   brief lo pide ("el tablero nunca navega") y además evita que lo que toca uno le
   cambie la pantalla al otro.
9. **El mensaje de compras renueva su TTL con cada toque** (se usa mientras se compra),
   los demás temporales no.
10. **Sin Gemini, las propuestas también funcionan**: la heurística local reconoce
    "ya compré X" y "borrá Y" y arma la propuesta. No ejecuta nada, así que un error
    del parser local tampoco rompe nada.
11. **La pausa se pide por texto y se confirma con botón**, como cualquier otra
    modificación.
12. **Los recados diferidos que haya pendientes al migrar se descartan** (el brief lo
    pide) y se avisa en el mensaje de bienvenida cuántos eran.
13. **El responsable se descarta si el mensaje no lo dice.** El prompt le cuenta al
    modelo quién escribió, y a veces deduce que esa persona lo hace: «comprar leche y
    yerba» quedó como «Leche · a compras · Axel». Se exige evidencia en el texto (el
    nombre, «los dos», o un «lo hago yo» de quien escribe). Mismo criterio que el
    corolario de que el LLM no decide sobre datos que existen.
14. **Los audios vienen prendidos** cuando hay key de Gemini, y se apagan con
    `NOTITA_AUDIOS=0`. La contrapartida está escrita en la sección de privacidad del
    README, que cubre texto y audio.

## Una sola clase de cosa

Hubo dos intentos antes de este. Primero «compras» era «producto de almacén **sin
fecha**» y la fecha decidía el tipo. Después se separaron tipo y fecha, pero el tipo
seguía cambiando **cómo** se guardaba la cosa: a los ítems de compras se les sacaba el
verbo. De esa asimetría salieron casi todos los bugs de esos días: la cómoda mal
clasificada, «Regalo para mamá» sin el «Comprar», duplicados que no se detectaban
porque un lado tenía el verbo y el otro no, y la fecha que se perdía al mover algo.

Ahora hay **una sola clase de cosa**: texto, fecha opcional, hora opcional,
responsable opcional, recurrencia opcional, y una **etiqueta booleana `compra`** (🛒).

- **La etiqueta no cambia nada.** Ni cómo se guarda, ni cómo se nombra, ni dónde vive.
  Sólo sirve para filtrar: `/compras` muestra lo etiquetado.
- **El texto se guarda como lo dijeron**, con mayúscula inicial y nada más. «comprar
  leche» → «Comprar leche»; «falta leche» → «Falta leche». No se saca ningún verbo en
  ningún caso. Si lo que se guarda es lo que se dijo, no hay forma de que la
  confirmación mienta.
- **Ante la duda, sin 🛒.** Un error de etiqueta no tiene consecuencias: la cosa sigue
  estando en el tablero.

Lo único especial de la etiqueta, y es de visualización: una cosa con 🛒 **y sin
fecha** no se lista una por una en el tablero (serían quince renglones de almacén
tapando las tareas). Con fecha, va en su día como cualquier otra cosa, con el carrito
adelante.

El contador del botón «🛒 Compras · N ›» cuenta **todo lo etiquetado**, tenga fecha o
no, porque tiene que dar lo mismo que `/compras`. Al principio contaba sólo las que no
tienen día y quedaban dos números distintos para la misma cosa: cuando eso pasa, no se
cree ninguno de los dos.

Los duplicados se comparan con **una sola regla**: sin tildes, sin mayúsculas, sin
artículos y sin los arranques de relleno («hay que», «falta», «comprar»…). Eso hace que
«Falta leche» y «Comprar leche» sean lo mismo. Y si lo que llega ya estaba pero **trae
una fecha** que el otro no tenía, se la pone y lo dice: decir «ya estaba» y nada más
tiraba la única información nueva del mensaje.

En la base, `tipo` se reemplazó por la columna `compra`. La migración etiqueta lo que
era del súper sin tocarle el texto, corre **una sola vez** (si alguien saca algo de
compras, no se lo vuelve a poner) y deja `tipo` borrada.

## Lo que el uso real nos hizo cambiar

Estas decisiones estaban escritas acá y las revirtió la realidad. Se dejan anotadas
porque el error es más útil que la conclusión:

| Decisión original | Qué pasó |
|---|---|
| Debounce de 3 segundos en el tablero | El primer ✅ caía dentro de esos 3 segundos y el tablero seguía mostrando la tarea hecha. «Toco y no pasa nada». Ahora se edita siempre |
| Un `⋯` al lado de cada tarea | Telegram le da media fila y cortaba los títulos al medio. Pasó a ser `⋯ Cambiar algo` |
| Botones con toda la info (hora, responsable) | No entra. La info va al texto, el botón va corto |
| Arreglos de esquema dentro de la migración versionada | La migración sale temprano si ya corrió, así que las bases que necesitaban el arreglo no lo recibían. Van sueltos en `init_db` |
| Cambiar la definición de un índice en el esquema | `CREATE INDEX IF NOT EXISTS` no lo redefine: el viejo sobrevive y bloquea el `DROP COLUMN`. Se borra a mano y el esquema lo recrea |
| Que dos cosas distintas usen el mismo emoji | La categoría «compras» y la etiqueta 🛒 compartían el carrito: algo con categoría compras y sin etiqueta se veía con 🛒 y no se contaba en Compras. Un símbolo, un significado |
| Que el tipo de una cosa cambiara **cómo** se guarda | A las compras se les sacaba el verbo, y eso rompía los duplicados y perdía palabras. Ahora 🛒 es sólo una etiqueta |
