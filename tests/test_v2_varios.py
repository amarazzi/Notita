"""Lo demás que pide el brief: calendario, menús, recurrencias, «che» y sin Gemini."""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from notita import calendario, cb, config, db, handlers, views
from notita.dates import Recurrencia, hoy

from .conftest import CHAT
from .test_v2_captura import botones, click, item, mensaje, responde, textos


# --------------------------------------------------------------------------
# Calendario
# --------------------------------------------------------------------------

def test_el_link_de_calendario_esta_bien_armado(enviados):
    tid = db.crear_tarea(CHAT, "llevar a Milo al veterinario & control (ñoño)",
                         due=date(2026, 10, 1), hora="18:30")

    url = calendario.link(db.obtener(tid))
    partes = parse_qs(urlparse(url).query)

    assert partes["text"] == ["Llevar a Milo al veterinario & control (ñoño)"]
    assert partes["dates"] == ["20261001T183000/20261001T193000"]
    assert partes["ctz"] == ["America/Argentina/Buenos_Aires"]
    # El & y la ñ van escapados en la URL cruda, no cortan la query.
    assert "%26" in url and "%C3%B1" in url


def test_el_ics_es_valido(enviados):
    tid = db.crear_tarea(CHAT, "turno con el dentista; llevar la orden",
                         due=date(2026, 10, 1), hora="09:00")

    texto = calendario.ics(db.obtener(tid))

    assert texto.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in texto and "END:VEVENT" in texto
    assert "DTSTART;TZID=America/Argentina/Buenos_Aires:20261001T090000" in texto
    assert "DTEND;TZID=America/Argentina/Buenos_Aires:20261001T100000" in texto
    assert "TRIGGER:-PT1H" in texto
    assert r"\;" in texto, "el punto y coma se escapa"
    assert texto.endswith("END:VCALENDAR\r\n")


def test_el_boton_de_calendario_aparece_si_hay_hora(enviados, monkeypatch):
    responde(monkeypatch, items=[item("llevar a Milo al veterinario",
                                      fecha_kind="manana", fecha_hora=18,
                                      fecha_minuto=0)])
    handlers.handle_update(mensaje("llevar a Milo al veterinario mañana a las 18"))

    assert any("calendario" in b["text"].lower() and "url" in b
               for b in botones(enviados))


def test_sin_hora_no_hay_boton_de_calendario(enviados, monkeypatch):
    responde(monkeypatch, items=[item("limpiar la heladera", fecha_kind="manana")])
    handlers.handle_update(mensaje("limpiar la heladera mañana"))

    assert not any("calendario" in b["text"].lower() for b in botones(enviados))


def test_con_ics_manda_un_archivo(enviados, monkeypatch):
    monkeypatch.setattr(config, "CALENDARIO", "ics")
    tid = db.crear_tarea(CHAT, "turno", due=hoy(), hora="18:00")

    handlers.handle_update(click(cb.armar("ics", tid)))

    assert any(e["metodo"] == "sendDocument" for e in enviados)


# --------------------------------------------------------------------------
# El menú «⋯»
# --------------------------------------------------------------------------

def test_el_menu_muestra_el_titulo_completo(enviados):
    largo = "llamar al ejército de salvación para que se lleven la cama de arriba"
    tid = db.crear_tarea(CHAT, largo, due=hoy())
    enviados.clear()

    handlers.handle_update(click(cb.armar("m", tid)))

    assert largo.capitalize() in textos(enviados)[0]


def test_mover_a_manana_desde_el_menu(enviados):
    tid = db.crear_tarea(CHAT, "limpiar el horno", due=hoy())

    handlers.handle_update(click(cb.armar("d", tid, "m")))

    assert db.obtener(tid)["due_date"] == (hoy() + timedelta(days=1)).isoformat()


def test_algun_dia_saca_la_fecha(enviados):
    tid = db.crear_tarea(CHAT, "pintar el balcón", due=hoy())
    handlers.handle_update(click(cb.armar("d", tid, "a")))
    assert db.obtener(tid)["due_date"] is None


def test_cambiar_el_responsable(enviados):
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("q", tid, "barbu")))
    assert db.obtener(tid)["responsable"] == "barbu"


def test_mover_al_super_y_volver(enviados):
    tid = db.crear_tarea(CHAT, "comprar pan", due=hoy(), hora="18:00")

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="a"))
    fila = db.obtener(tid)
    assert fila["tipo"] == "compras"
    assert fila["due_date"] is None and fila["due_hora"] is None

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="b"))
    assert db.obtener(tid)["tipo"] == "casa"


def test_renombrar_pide_respuesta_y_no_pasa_por_el_llm(enviados, monkeypatch):
    from notita import llm

    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("renombrar no usa el LLM"))
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("r", tid)))

    pregunta = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert pregunta["reply_markup"] == {"force_reply": True, "selective": True}

    respuesta = mensaje("regar las plantas del balcón", update_id=50)
    respuesta["message"]["reply_to_message"] = {"from": {"is_bot": True}}
    handlers.handle_update(respuesta)

    assert db.obtener(tid)["texto"] == "regar las plantas del balcón"


def test_un_mensaje_cualquiera_no_se_come_el_force_reply(enviados, monkeypatch):
    """El principio 3: si no es una respuesta, es un mensaje nuevo."""
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("r", tid)))
    responde(monkeypatch, items=[item("comprar pan", fecha_kind="hoy")])

    handlers.handle_update(mensaje("hay que comprar pan", update_id=51))

    assert db.obtener(tid)["texto"] == "regar", "no lo renombró"
    assert any(r["texto"] == "comprar pan" for r in db.pendientes(CHAT))


def test_borrar_ofrece_deshacer(enviados):
    tid = db.crear_tarea(CHAT, "pintar el balcón", due=hoy())
    db.anotar_temporal(CHAT, 55, "menu", 5)      # como si el menú estuviera abierto
    enviados.clear()

    handlers.handle_update(click(cb.armar("x", tid), message_id=55, cq_id="del"))

    assert db.obtener(tid)["estado"] == "borrada"
    editados = [e for e in enviados if e["metodo"] == "editMessageText"]
    etiquetas = [b["text"] for e in editados
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    assert any("Deshacer" in e for e in etiquetas)


def test_una_recurrente_pregunta_si_es_una_o_todas(enviados):
    tid = db.crear_tarea(CHAT, "pagar el ABL", due=hoy(),
                         recurrencia=Recurrencia("mensual", monthday=10))
    handlers.handle_update(click(cb.armar("m", tid), cq_id="menu"))
    enviados.clear()

    handlers.handle_update(click(cb.armar("x", tid), message_id=5, cq_id="del"))

    assert db.obtener(tid)["estado"] == "pendiente", "todavía no borró nada"
    editado = [e for e in enviados if e["metodo"] == "editMessageText"][-1]
    etiquetas = [b["text"] for fila in editado["reply_markup"]["inline_keyboard"]
                 for b in fila]
    assert "Sólo esta vez" in etiquetas and "Todas" in etiquetas


# --------------------------------------------------------------------------
# Recurrencias
# --------------------------------------------------------------------------

def test_cada_tres_dias_arranca_hoy(enviados, monkeypatch):
    responde(monkeypatch, items=[item("regar las plantas", fecha_kind="desconocida",
                                      recur_kind="diaria", recur_interval=3)])
    handlers.handle_update(mensaje("regar las plantas cada 3 días"))

    assert db.pendientes(CHAT)[0]["due_date"] == hoy().isoformat()


def test_al_completarla_la_proxima_sale_de_la_fecha_que_debia(enviados):
    """Sin generar ocurrencias atrasadas: la próxima se cuenta desde el vencimiento."""
    vencia = hoy() - timedelta(days=10)
    tid = db.crear_tarea(CHAT, "regar", due=vencia,
                         recurrencia=Recurrencia("diaria", interval=3))

    nueva = db.marcar_hecha(tid, "axel")

    from notita.dates import de_iso

    assert de_iso(nueva["due_date"]) > hoy(), "la próxima no puede quedar vencida"


def test_el_intervalo_se_ve_en_el_tablero_y_en_el_menu(enviados):
    from notita import tablero

    tid = db.crear_tarea(CHAT, "regar", due=hoy(),
                         recurrencia=Recurrencia("diaria", interval=3))
    texto, _ = tablero.render(CHAT)
    assert "🔁 cada 3 días" in texto, "en el renglón del tablero"

    handlers.handle_update(click(cb.armar("m", tid)))
    assert "cada 3 días" in textos(enviados)[-1], "y también en el menú"


# --------------------------------------------------------------------------
# Textos: nada de «che»
# --------------------------------------------------------------------------

# `llm.py` tiene el "che" de la prohibición en el prompt, y `heuristica.py` lo tiene
# como palabra a IGNORAR de los mensajes que entran. Ninguno se le dice a nadie.
SIN_TEXTOS_AL_USUARIO = {"llm.py", "heuristica.py"}


def test_ningun_texto_al_usuario_dice_che():
    """El brief lo prohíbe. Se recorren los strings de los módulos que hablan."""
    import ast
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    culpables = []
    for archivo in sorted((raiz / "notita").glob("*.py")):
        if archivo.name in SIN_TEXTOS_AL_USUARIO:
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
                if re.search(r"\bche\b", nodo.value, re.IGNORECASE):
                    culpables.append(f"{archivo.name}:{nodo.lineno}")
    assert culpables == [], f"«che» en {culpables}"


def test_el_prompt_prohibe_el_che():
    from notita import llm

    assert 'NUNCA uses "che"' in llm._sistema()


def test_la_ayuda_cuenta_el_modelo_nuevo():
    texto = views.ayuda()
    for pista in ("tablero", "✅", "⋯", "parte", "calendario"):
        assert pista in texto, pista
    assert "horas exactas" in texto


# --------------------------------------------------------------------------
# Sin Gemini, todo lo que no es capturar funciona igual
# --------------------------------------------------------------------------

def test_sin_gemini_el_tablero_los_botones_y_el_parte(enviados, monkeypatch):
    from notita import llm, parte, tablero

    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm, "_call", lambda *a, **k: pytest.fail("ni una llamada al LLM"))

    tid = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    texto, filas = tablero.render(CHAT)
    assert "Sacar la basura" in " ".join(b["text"] for fila in filas for b in fila)

    handlers.handle_update(click(cb.armar("ok", tid)))
    assert db.obtener(tid)["estado"] == "hecha"

    texto_parte, _ = parte.render(CHAT)
    assert texto_parte


def test_sin_gemini_se_puede_capturar(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("hay que limpiar la heladera el lunes"))

    assert db.pendientes(CHAT)[0]["texto"] == "limpiar la heladera"


# --------------------------------------------------------------------------
# Bienvenida
# --------------------------------------------------------------------------

def test_la_bienvenida_sale_una_sola_vez(enviados, casa_nueva, monkeypatch):
    responde(monkeypatch, intencion="charla", comentario="hola 🤍")

    handlers.handle_update(mensaje("hola", update_id=1))
    handlers.handle_update(mensaje("hola", update_id=2))

    bienvenidas = [t for t in textos(enviados) if "Notita cambió" in t]
    assert len(bienvenidas) == 1
    assert db.tablero_actual(CHAT)["message_id"] is not None


def test_la_bienvenida_cuenta_los_recados_descartados(enviados, casa_nueva, monkeypatch):
    db.ajuste("recados_descartados", "2")
    responde(monkeypatch, intencion="charla", comentario="hola")

    handlers.handle_update(mensaje("hola"))

    bienvenida = [t for t in textos(enviados) if "Notita cambió" in t][0]
    assert "2 recados" in bienvenida


# --------------------------------------------------------------------------
# Cuando Telegram convierte el grupo en supergrupo
# --------------------------------------------------------------------------

NUEVO_CHAT = -1003993284076


def aviso_de_mudanza(nuevo=NUEVO_CHAT):
    return {"update_id": 300,
            "message": {"chat": {"id": CHAT, "type": "group"}, "from": {"id": 111},
                        "message_id": 1, "migrate_to_chat_id": nuevo}}


def test_al_convertirse_el_grupo_se_muda_con_las_tareas(enviados, monkeypatch, tmp_path):
    """Lo que pasó de verdad: hacerlo admin convirtió el grupo.

    Si sólo se cambiara el ALLOWED_CHAT_ID, las tareas quedarían guardadas con el
    número viejo y el bot arrancaría vacío.
    """
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text(f"TELEGRAM_TOKEN=x\nALLOWED_CHAT_ID={CHAT}\n")
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    handlers.handle_update(aviso_de_mudanza())

    assert [r["texto"] for r in db.pendientes(NUEVO_CHAT)] == ["sacar la basura", "leche"]
    assert db.pendientes(CHAT) == [], "no quedó nada en el viejo"
    assert config.ALLOWED_CHAT_ID == NUEVO_CHAT, "sigue contestando sin esperar el reload"
    assert "ALLOWED_CHAT_ID" in (tmp_path / ".env").read_text()
    assert f"ALLOWED_CHAT_ID={NUEVO_CHAT}" in (tmp_path / ".env").read_text()


def test_al_mudarse_avisa_y_publica_el_tablero(enviados, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text(f"ALLOWED_CHAT_ID={CHAT}\n")
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    handlers.handle_update(aviso_de_mudanza())

    salida = textos(enviados)
    assert any("Ya me mudé" in t and "1</b> cosas" in t for t in salida)
    assert any("La casa" in t for t in salida), "publica el tablero en el grupo nuevo"
    assert db.tablero_actual(NUEVO_CHAT) is not None


def test_si_no_puede_escribir_el_env_lo_dice(enviados, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)      # sin .env
    handlers.handle_update(aviso_de_mudanza())

    assert any("poné" in t and "ALLOWED_CHAT_ID" in t for t in textos(enviados))


def test_mudarse_no_pisa_el_resto_del_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=secreto\nALLOWED_CHAT_ID=-100111\nCRON_SECRET=abc\n")

    assert config.guardar_chat_id(NUEVO_CHAT) is True

    texto = env.read_text()
    assert "TELEGRAM_TOKEN=secreto" in texto
    assert "CRON_SECRET=abc" in texto
    assert texto.count("ALLOWED_CHAT_ID") == 1
    assert oct(env.stat().st_mode)[-3:] == "600", "el .env sigue siendo privado"


def test_migrar_chat_es_idempotente(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    db.migrar_chat(CHAT, NUEVO_CHAT)
    db.migrar_chat(CHAT, NUEVO_CHAT)      # de nuevo: ya no hay nada que mover

    assert len(db.pendientes(NUEVO_CHAT)) == 1


def test_antes_de_tocar_el_env_hace_una_copia(monkeypatch, tmp_path):
    """Es el archivo con los secretos de la casa: no se reescribe sin copia."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    env = tmp_path / ".env"
    original = "TELEGRAM_TOKEN=secreto\nALLOWED_CHAT_ID=-100111\n"
    env.write_text(original)

    config.guardar_chat_id(NUEVO_CHAT)

    copias = [p for p in tmp_path.iterdir() if p.name.endswith(".bak")]
    assert len(copias) == 1, "tiene que quedar una copia"
    assert copias[0].read_text() == original
    assert oct(copias[0].stat().st_mode)[-3:] == "600"


def test_las_copias_del_env_estan_ignoradas_por_git():
    import pathlib

    ignorados = (pathlib.Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert ".env" in ignorados
    assert ".bak" in ignorados


# --------------------------------------------------------------------------
# Los avisos de Telegram no son mensajes de nadie
# --------------------------------------------------------------------------

def test_el_aviso_de_que_fijo_un_mensaje_no_se_contesta(enviados):
    """Se veían dos «Todavía no entiendo audios ni fotos»: eran los pines.

    Fijar el tablero mete un aviso de servicio en el chat, y esos llegan SIN texto.
    """
    handlers.handle_update({"update_id": 400, "message": {
        "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": 7, "is_bot": True},
        "message_id": 12, "pinned_message": {"message_id": 11, "text": "📋 La casa"}}})

    assert not [t for t in textos(enviados) if "audios" in t]


def test_el_aviso_del_pin_se_borra_del_chat(enviados):
    handlers.handle_update({"update_id": 401, "message": {
        "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": 7, "is_bot": True},
        "message_id": 12, "pinned_message": {"message_id": 11}}})

    borrados = [e for e in enviados if e["metodo"] == "deleteMessage"]
    assert [b["message_id"] for b in borrados] == [12]


def test_si_lo_fijo_una_persona_no_se_borra_nada(enviados):
    handlers.handle_update({"update_id": 402, "message": {
        "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": 111},
        "message_id": 12, "pinned_message": {"message_id": 11}}})

    assert not [e for e in enviados if e["metodo"] == "deleteMessage"]


@pytest.mark.parametrize("clave", ["new_chat_members", "left_chat_member",
                                   "new_chat_title", "supergroup_chat_created",
                                   "migrate_from_chat_id"])
def test_los_demas_avisos_de_servicio_tampoco(enviados, clave):
    handlers.handle_update({"update_id": 403, "message": {
        "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": 111},
        "message_id": 13, clave: [{"id": 1}]}})

    assert textos(enviados) == []
    assert db.pendientes(CHAT) == []


def test_un_audio_de_verdad_si_se_contesta(enviados):
    handlers.handle_update({"update_id": 404, "message": {
        "chat": {"id": CHAT, "type": "supergroup"}, "from": {"id": 111},
        "message_id": 14, "voice": {"file_id": "x"}}})

    assert any("audios" in t for t in textos(enviados))


def test_al_mudarse_no_se_lleva_los_message_id(enviados, monkeypatch, tmp_path):
    """Un message_id es de UN chat: en el nuevo no existe.

    Si se mudara la fila del tablero, Notita intentaría editar un mensaje que no está
    y el grupo nuevo se quedaría sin tablero.
    """
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text(f"ALLOWED_CHAT_ID={CHAT}\n")
    db.guardar_tablero(CHAT, 999)
    db.anotar_temporal(CHAT, 998, "menu", 5)
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    handlers.handle_update(aviso_de_mudanza())

    nuevo = db.tablero_actual(NUEVO_CHAT)
    assert nuevo["message_id"] != 999, "es un tablero nuevo, publicado de cero"
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM mensajes_temporales").fetchone()["n"] == 0
    assert len(db.pendientes(NUEVO_CHAT)) == 1, "pero las tareas sí se mudan"


# --------------------------------------------------------------------------
# La base de v1 y los botones
# --------------------------------------------------------------------------

def base_de_v1(ruta):
    """Una base con la tabla `updates_vistos` como la creaba v1: INTEGER."""
    import sqlite3

    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE updates_vistos (update_id INTEGER PRIMARY KEY, "
              "visto_en TEXT NOT NULL)")
    c.execute("INSERT INTO updates_vistos VALUES (111, '2026-09-28T10:00:00-03:00')")
    c.commit()
    c.close()


def test_sobre_una_base_de_v1_los_botones_funcionan(tmp_path, monkeypatch, enviados):
    """El bug que dejó TODOS los botones muertos en producción.

    En v1 `updates_vistos.update_id` era INTEGER; en v2 guarda también los
    `cb:<id>` de los toques. `CREATE TABLE IF NOT EXISTS` no cambia una tabla que ya
    existe, así que SQLite rechazaba el texto, el error se leía como «esto ya lo
    procesé» y el toque se descartaba sin decir nada.
    """
    ruta = tmp_path / "v1.db"
    base_de_v1(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    db.init_db()
    db.ajuste("bienvenida_v2", "1")
    db.guardar_tablero(CHAT, 1)
    tid = db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    handlers.handle_update(click(cb.armar("ok", tid)))

    assert db.obtener(tid)["estado"] == "hecha", "el toque tiene que llegar"
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert avisos == ["✅ Hecho"]


def test_la_migracion_rehace_la_tabla(tmp_path, monkeypatch):
    ruta = tmp_path / "v1.db"
    base_de_v1(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    with db.conn() as c:
        tipos = {f["name"]: f["type"] for f in c.execute("PRAGMA table_info(updates_vistos)")}
    assert tipos["update_id"] == "TEXT"


def test_un_problema_de_la_base_no_descarta_el_update(monkeypatch):
    """Antes, cualquier IntegrityError se leía como «repetido» y se perdía el update."""
    import sqlite3

    real = db.conn

    class Fingido:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            if sql.startswith("INSERT INTO updates_vistos"):
                raise sqlite3.IntegrityError("datatype mismatch")
            return self._c.execute(sql, *a)

    from contextlib import contextmanager

    @contextmanager
    def fingida():
        with real() as c:
            yield Fingido(c)

    monkeypatch.setattr(db, "conn", fingida)
    assert db.update_nuevo("cb:99") is True, "ante la duda, se procesa"


def test_un_toque_repetido_de_verdad_sigue_descartandose(enviados):
    assert db.update_nuevo("cb:77") is True
    assert db.update_nuevo("cb:77") is False
