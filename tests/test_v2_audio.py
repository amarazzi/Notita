"""Notas de voz: se transcriben y entran por la misma puerta que el texto.

La viabilidad se verificó antes de escribir esto: Gemini transcribe el Ogg/Opus de
Telegram tal cual, sin transcodificar (probado con un audio real de WhatsApp) y
devuelve NO_SE_ENTIENDE con ruido o silencio, sin inventar.
"""
import pytest

from notita import audio, config, db, handlers, llm, telegram

from .conftest import CHAT
from .test_v2_captura import botones, item, responde, textos


def nota_de_voz(segundos=4, file_id="AwACAgEAAx", update_id=1, message_id=7):
    return {"update_id": update_id,
            "message": {"chat": {"id": CHAT, "type": "supergroup"},
                        "from": {"id": 111}, "message_id": message_id,
                        "voice": {"file_id": file_id, "duration": segundos,
                                  "mime_type": "audio/ogg", "file_size": 9000}}}


@pytest.fixture
def escucha(monkeypatch):
    """Telegram entrega el audio y Gemini lo transcribe. Sin red."""
    estado = {"transcripcion": "falta leche", "bajado": [], "transcripto": []}

    def descargar(file_id):
        estado["bajado"].append(file_id)
        return b"OggS\x00fingido"

    def transcribir(crudo, mime="audio/ogg"):
        estado["transcripto"].append((len(crudo), mime))
        return estado["transcripcion"]

    monkeypatch.setattr(telegram, "descargar", descargar)
    monkeypatch.setattr(llm, "transcribir", transcribir)
    return estado


# --------------------------------------------------------------------------
# El camino feliz
# --------------------------------------------------------------------------

def test_un_audio_se_anota_como_si_lo_hubieran_escrito(enviados, escucha, monkeypatch):
    escucha["transcripcion"] = "falta leche y hay que limpiar la heladera el jueves"
    responde(monkeypatch, items=[
        item("Leche", compra=True, fecha_kind="algun_dia"),
        item("limpiar la heladera", categoria="limpieza", fecha_kind="dia_semana",
             fecha_weekday=3)])

    handlers.handle_update(nota_de_voz())

    assert [r["texto"] for r in db.pendientes(CHAT, compra=True)] == ["Leche"]
    assert len(db.pendientes(CHAT, compra=False)) == 1
    confirmacion = [t for t in textos(enviados) if "Anoté" in t][0]
    assert "🎤" in confirmacion, "tiene que mostrar lo que escuchó"
    assert "falta leche y hay que limpiar la heladera el jueves" in confirmacion
    assert any("Deshacer" in b["text"] for b in botones(enviados))


def test_avisa_que_esta_escuchando_y_despues_lo_borra(enviados, escucha, monkeypatch):
    """Transcribir puede tardar 20 segundos: sin aviso parece que se colgó."""
    responde(monkeypatch, items=[item("Leche", compra=True, fecha_kind="algun_dia")])

    handlers.handle_update(nota_de_voz())

    metodos = [e["metodo"] for e in enviados]
    assert "Escuchando el audio" in textos(enviados)[0]
    assert "deleteMessage" in metodos, "el aviso no se queda colgado"
    assert metodos.index("deleteMessage") < len(metodos) - 1, "y la respuesta va después"


def test_lo_que_se_le_manda_a_gemini_es_el_audio_crudo(enviados, escucha, monkeypatch):
    responde(monkeypatch, items=[item("Leche", compra=True, fecha_kind="algun_dia")])

    handlers.handle_update(nota_de_voz())

    assert escucha["bajado"] == ["AwACAgEAAx"]
    assert escucha["transcripto"] == [(len(b"OggS\x00fingido"), "audio/ogg")]


# --------------------------------------------------------------------------
# El tope de duración
# --------------------------------------------------------------------------

def test_un_audio_largo_no_se_descarga_ni_se_manda(enviados, escucha):
    handlers.handle_update(nota_de_voz(segundos=config.AUDIO_SEGUNDOS + 1))

    assert escucha["bajado"] == [], "el tope se chequea ANTES de bajar nada"
    assert escucha["transcripto"] == []
    assert "Muy largo para mí" in textos(enviados)[0]
    assert f"{config.AUDIO_SEGUNDOS} segundos" in textos(enviados)[0]
    assert db.pendientes(CHAT) == []


def test_justo_en_el_tope_pasa(enviados, escucha, monkeypatch):
    responde(monkeypatch, items=[item("Leche", compra=True, fecha_kind="algun_dia")])

    handlers.handle_update(nota_de_voz(segundos=config.AUDIO_SEGUNDOS))

    assert escucha["bajado"], "15 segundos justos entran"


def test_el_tope_es_configurable(monkeypatch, enviados, escucha):
    monkeypatch.setattr(config, "AUDIO_SEGUNDOS", 30)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: None)

    handlers.handle_update(nota_de_voz(segundos=25))

    assert escucha["bajado"], "con el tope en 30, un audio de 25 entra"


# --------------------------------------------------------------------------
# Cuando no se puede escuchar
# --------------------------------------------------------------------------

def test_si_no_se_puede_bajar_el_audio_lo_dice(enviados, escucha, monkeypatch):
    monkeypatch.setattr(telegram, "descargar", lambda file_id: None)

    handlers.handle_update(nota_de_voz())

    assert "No pude escucharlo bien" in textos(enviados)[-1]
    assert db.pendientes(CHAT) == []


def test_si_gemini_no_transcribe_lo_dice(enviados, escucha, monkeypatch):
    monkeypatch.setattr(llm, "transcribir", lambda *a, **k: None)

    handlers.handle_update(nota_de_voz())

    assert "No pude escucharlo bien" in textos(enviados)[-1]
    assert db.pendientes(CHAT) == []


def test_si_el_audio_es_ruido_no_inventa_nada(enviados, escucha, monkeypatch):
    """Probado contra la API real: con ruido o silencio devuelve NO_SE_ENTIENDE."""
    monkeypatch.setattr(llm, "transcribir", lambda *a, **k: llm.INDESCIFRABLE)

    handlers.handle_update(nota_de_voz())

    assert "No pude escucharlo bien" in textos(enviados)[-1]
    assert db.pendientes(CHAT) == []


def test_sin_gemini_no_hay_audios(enviados, escucha, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")

    handlers.handle_update(nota_de_voz())

    assert "audios ni fotos" in textos(enviados)[0]
    assert escucha["bajado"] == []


def test_se_pueden_apagar(enviados, escucha, monkeypatch):
    monkeypatch.setattr(config, "AUDIOS", False)

    handlers.handle_update(nota_de_voz())

    assert "audios ni fotos" in textos(enviados)[0]
    assert escucha["bajado"] == []


# --------------------------------------------------------------------------
# Un pedido por audio se PROPONE, no se ejecuta
# --------------------------------------------------------------------------

def test_un_pedido_de_modificacion_por_audio_solo_propone(enviados, escucha, monkeypatch):
    """Una transcripción puede equivocarse: con más razón acá no se ejecuta nada."""
    db.crear_tarea(CHAT, "leche", compra=True, categoria="compras")
    escucha["transcripcion"] = "ya compré la leche"
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche"])

    handlers.handle_update(nota_de_voz())

    assert len(db.pendientes(CHAT, compra=True)) == 1, "no se toca nada"
    propuesta = [t for t in textos(enviados) if "¿Tacho" in t][0]
    assert "🎤" in propuesta and "ya compré la leche" in propuesta


def test_un_recado_por_audio_muestra_lo_que_escucho(enviados, escucha, monkeypatch):
    escucha["transcripcion"] = "decile a Barbu que ya salí"
    responde(monkeypatch, intencion="recado", recado_para="barbu",
             recado_mensaje="ya salí")

    handlers.handle_update(nota_de_voz())

    entrega = [t for t in textos(enviados) if "te manda a decir" in t][0]
    assert "🎤" in entrega
    assert "decile a Barbu que ya salí" in entrega


def test_una_charla_por_audio_no_crea_nada(enviados, escucha, monkeypatch):
    escucha["transcripcion"] = "hola notita cómo andás"
    responde(monkeypatch, intencion="charla", comentario="¡Todo bien! 🤍")

    handlers.handle_update(nota_de_voz())

    assert db.pendientes(CHAT) == []
    charla = textos(enviados)[-1]
    assert "🎤" in charla and "¡Todo bien!" in charla


# --------------------------------------------------------------------------
# Idempotencia y modo local
# --------------------------------------------------------------------------

def test_un_reintento_del_webhook_no_anota_dos_veces(enviados, escucha, monkeypatch):
    """Telegram reenvía si tarda en responder, y transcribir tarda."""
    responde(monkeypatch, items=[item("Leche", compra=True, fecha_kind="algun_dia")])

    handlers.handle_update(nota_de_voz(update_id=77))
    handlers.handle_update(nota_de_voz(update_id=77))

    assert len(db.pendientes(CHAT, compra=True)) == 1
    assert len([t for t in textos(enviados) if "Anoté" in t]) == 1
    assert escucha["transcripto"] == [(len(b"OggS\x00fingido"), "audio/ogg")], \
        "tampoco se transcribe dos veces (cuesta cuota y tiempo)"


def test_si_falla_la_interpretacion_el_modo_local_salva_la_tarea(enviados, escucha,
                                                                 monkeypatch):
    escucha["transcripcion"] = "hay que limpiar la heladera el lunes"
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: None)

    handlers.handle_update(nota_de_voz())

    guardada = db.pendientes(CHAT, compra=False)[0]
    assert guardada["texto"] == "limpiar la heladera"
    assert "🎤" in [t for t in textos(enviados) if "Anoté" in t][0]


# --------------------------------------------------------------------------
# Detalles
# --------------------------------------------------------------------------

def test_el_prefijo_escapa_el_html():
    assert "&lt;b&gt;" in audio.prefijo("mandá <b>esto</b>")


def test_un_audio_sin_file_id_no_rompe(enviados, escucha):
    update = nota_de_voz()
    del update["message"]["voice"]["file_id"]

    handlers.handle_update(update)

    assert "No pude escucharlo bien" in textos(enviados)[-1]


def test_el_timeout_de_audio_es_mas_largo_que_el_normal():
    """Transcribir 14 segundos tardó 20 en las pruebas, más que el TIMEOUT de 25."""
    assert llm.TIMEOUT_AUDIO > llm.TIMEOUT
    assert llm.INTENTOS_AUDIO < llm.INTENTOS, "reintentar 20 segundos es carísimo"


def test_la_ayuda_cuenta_los_audios_con_el_tope_real(monkeypatch):
    from notita import views

    monkeypatch.setattr(config, "AUDIO_SEGUNDOS", 15)
    texto = views.ayuda()
    assert "🎤" in texto and "audio" in texto
    assert "15 segundos" in texto
    assert "{segundos}" not in texto, "quedó el placeholder sin reemplazar"


def test_el_readme_avisa_que_el_audio_va_a_google():
    import pathlib

    readme = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert "## Privacidad" in readme
    # Lo que pidió Axel: que se diga sin vueltas, y para texto Y audio.
    assert "capa gratuita" in readme
    assert "mejorar sus productos" in readme
    assert "NOTITA_AUDIOS=0" in readme
