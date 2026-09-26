#!/usr/bin/env python3
"""Arma el GIF de Notita funcionando.

    python3 docs/demo/generar_gif.py

Los mensajes no están escritos a mano: los produce el código real de Notita
(ver guion.py). Acá sólo se dibuja el chat y se arma la animación:

    guion.py  ->  HTML por cuadro  ->  capturas con Chrome  ->  GIF con ffmpeg

Hace falta Google Chrome y ffmpeg.
"""
from __future__ import annotations

import html
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SALIDA = RAIZ / "docs" / "notita.gif"
ANCHO, ALTO = 700, 640
# Un miércoles cualquiera: así el GIF sale igual siempre y "el finde" cae en sábado.
HOY = date(2026, 10, 7)

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  width: %(ancho)spx; height: %(alto)spx; overflow: hidden;
  background: #0e1621;
  font: 17px/1.45 -apple-system, "Helvetica Neue", sans-serif;
  color: #fff;
}
.tel { display: flex; flex-direction: column; height: 100%%; }
.barra {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 18px; background: #17212b; border-bottom: 1px solid #0b1219;
}
.avatar {
  width: 38px; height: 38px; border-radius: 50%%; background: #e8d5f2;
  display: flex; align-items: center; justify-content: center; font-size: 20px;
}
.titulo { font-weight: 600; }
.sub { font-size: 13px; color: #6d8ba1; }
.chat {
  flex: 1; display: flex; flex-direction: column; justify-content: flex-end;
  gap: 8px; padding: 16px 18px 20px; overflow: hidden;
}
.fila { display: flex; }
.fila.out { justify-content: flex-end; }
.burbuja {
  max-width: 78%%; padding: 8px 12px 6px; border-radius: 14px;
  background: #182533; position: relative;
}
.out .burbuja { background: #2b5278; }
.autor { font-size: 14px; font-weight: 600; color: #eb6f9a; margin-bottom: 2px; }
.texto { white-space: pre-wrap; word-wrap: break-word; }
.hora { font-size: 11px; color: #6d8ba1; text-align: right; margin-top: 2px; }
.out .hora { color: #8badc9; }
b { font-weight: 600; }
i { color: #9db4c7; font-style: normal; font-size: 15px; }
s { color: #6d8ba1; }
a { color: #6ab3f3; text-decoration: none; }
.botones { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.boton {
  flex: 1 1 40%%; text-align: center; padding: 7px 8px; border-radius: 8px;
  background: #22384e; color: #6ab3f3; font-size: 15px; white-space: nowrap;
}
.separador { text-align: center; margin: 10px 0 4px; }
.separador span {
  background: #17212b90; color: #a8c5dd; font-size: 13px;
  padding: 4px 12px; border-radius: 12px;
}
"""


def render(eventos, hasta: int) -> str:
    """El HTML del chat con los primeros `hasta` eventos aplicados."""
    burbujas, piezas = [], []
    for evento in eventos[:hasta]:
        if evento.tipo == "separador":
            burbujas.append(("separador", evento.texto))
        elif evento.tipo == "burbuja":
            burbujas.append(("burbuja", evento.burbuja))
        elif evento.tipo == "edicion":
            # Una edición reemplaza el texto de una burbuja que ya está en pantalla.
            for i in range(len(burbujas) - 1, -1, -1):
                tipo, b = burbujas[i]
                if tipo == "burbuja" and b.lado == "in" and b.botones:
                    b.html, b.botones = evento.texto, []
                    break

    for tipo, item in burbujas:
        if tipo == "separador":
            piezas.append(f'<div class="separador"><span>{html.escape(item)}</span></div>')
            continue
        autor = '<div class="autor">Notita</div>' if item.lado == "in" else ""
        botones = ""
        if item.botones:
            chips = "".join(f'<div class="boton">{html.escape(t)}</div>'
                            for fila in item.botones for t, _ in fila)
            botones = f'<div class="botones">{chips}</div>'
        piezas.append(
            f'<div class="fila {item.lado}"><div class="burbuja">{autor}'
            f'<div class="texto">{_telegram_a_html(item.html)}</div>{botones}'
            f'<div class="hora">{item.hora}</div></div></div>')

    return f"""<!doctype html><html lang="es"><meta charset="utf-8">
<style>{CSS % {"ancho": ANCHO, "alto": ALTO}}</style>
<div class="tel">
  <div class="barra">
    <div class="avatar">🧲</div>
    <div><div class="titulo">Casita</div><div class="sub">Axel, Barbu, Notita</div></div>
  </div>
  <div class="chat">{"".join(piezas)}</div>
</div>"""


def _telegram_a_html(texto: str) -> str:
    """El bot manda HTML de Telegram; acá sólo hay que arreglar las menciones."""
    return re.sub(r'<a href="tg://user\?id=\d+">([^<]+)</a>', r'<a>\1</a>', texto)


def capturar(eventos, carpeta: Path) -> list[Path]:
    if not Path(CHROME).exists():
        raise SystemExit(f"No encuentro Google Chrome en {CHROME}")
    cuadros = []
    for i in range(1, len(eventos) + 1):
        pagina = carpeta / f"paso{i:02d}.html"
        pagina.write_text(render(eventos, i), encoding="utf-8")
        png = carpeta / f"paso{i:02d}.png"
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={ANCHO},{ALTO}", f"--screenshot={png}", pagina.as_uri()],
            check=True, capture_output=True)
        cuadros.append(png)
        print(f"  cuadro {i}/{len(eventos)}")
    return cuadros


def armar_gif(cuadros: list[Path], eventos, carpeta: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("Falta ffmpeg (brew install ffmpeg)")

    # Cuánto se queda quieto cada cuadro: los mensajes largos necesitan más.
    lista = []
    for cuadro, evento in zip(cuadros, eventos):
        if evento.tipo == "separador":
            segundos = 0.7
        elif evento.tipo == "burbuja" and evento.burbuja.lado == "out":
            segundos = 1.1          # lo que escribimos nosotros se lee rápido
        else:
            largo = len(getattr(evento.burbuja, "html", evento.texto))
            segundos = min(3.0, 1.2 + largo / 130)
        lista.append(f"file '{cuadro.name}'\nduration {segundos:.2f}")
    lista.append(f"file '{cuadros[-1].name}'\nduration 2.5")
    lista.append(f"file '{cuadros[-1].name}'")   # el concat pide repetir el último
    (carpeta / "lista.txt").write_text("\n".join(lista), encoding="utf-8")

    filtros = ("fps=10,scale=680:-1:flags=lanczos,"
               "split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-i", "lista.txt",
         "-vf", filtros, "-loop", "0", str(SALIDA)],
        cwd=carpeta, check=True, capture_output=True)


def main() -> None:
    random.seed(7)          # Notita elige la pregunta al azar: que salga siempre igual
    import guion

    guion.fijar_hoy(HOY)
    print("Actuando la conversación con el código real…")
    eventos = guion.actuar()
    print(f"  {len(eventos)} momentos")

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        print("Sacando capturas con Chrome…")
        cuadros = capturar(eventos, carpeta)
        print("Armando el GIF…")
        armar_gif(cuadros, eventos, carpeta)

    peso = SALIDA.stat().st_size / 1024
    print(f"\nListo: {SALIDA}  ({peso:.0f} KB)")


if __name__ == "__main__":
    main()
