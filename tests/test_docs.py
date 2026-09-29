"""La documentación se pudre rápido: en un día quedó describiendo otro tablero.

Estos tests no revisan que esté bien escrita, sino que no prometa cosas que el código
no tiene y que no se olvide de las que sí.
"""
import ast
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
README = (RAIZ / "README.md").read_text(encoding="utf-8")
AGENTS = (RAIZ / "AGENTS.md").read_text(encoding="utf-8")
DISENO = (RAIZ / "docs" / "DISEÑO-v2.md").read_text(encoding="utf-8")
PROBAR = (RAIZ / "docs" / "PROBAR.md").read_text(encoding="utf-8")

DOCS = {"README.md": README, "AGENTS.md": AGENTS,
        "docs/DISEÑO-v2.md": DISENO, "docs/PROBAR.md": PROBAR}


@pytest.mark.parametrize("nombre", list(DOCS))
def test_los_links_internos_no_estan_rotos(nombre):
    for destino in re.findall(r"\]\((?!http|#)([^)]+)\)", DOCS[nombre]):
        ruta = RAIZ / destino.split("#")[0]
        assert ruta.exists(), f"{nombre} apunta a {destino}, que no existe"


@pytest.mark.parametrize("nombre", list(DOCS))
def test_no_se_menciona_codigo_que_ya_no_existe(nombre):
    """Se borró `docs/demo/` justo por esto: apuntaba a `notita.reminders`."""
    muertos = ("reminders.py", "notita.reminders", "run_reminders",
               "correr_rutina_diaria", "/algundia`", "resumen semanal")
    for muerto in muertos:
        if muerto in DOCS[nombre]:
            # Sólo vale nombrarlo para decir que ya no existe.
            contexto = DOCS[nombre][max(0, DOCS[nombre].index(muerto) - 200):]
            assert re.search(r"ya no existe|se va|borrad|jubilad|no existe más|v1|viejo",
                             contexto[:400], re.I), \
                f"{nombre} menciona {muerto!r} como si todavía anduviera"


def test_todos_los_botones_estan_en_el_diseno():
    """Cada acción de callback tiene que estar documentada: son la API del tablero."""
    acciones = set()
    for archivo in (RAIZ / "notita").glob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                    and nodo.func.attr == "armar" and nodo.args
                    and isinstance(nodo.args[0], ast.Constant)):
                acciones.add(nodo.args[0].value)

    assert acciones, "no encontré ningún cb.armar(): ¿cambió la forma de armarlos?"
    faltan = [a for a in sorted(acciones) if f"|{a}" not in DISENO]
    assert faltan == [], f"sin documentar en DISEÑO-v2.md: {faltan}"


def test_todas_las_variables_de_entorno_estan_en_el_readme():
    config = (RAIZ / "notita" / "config.py").read_text(encoding="utf-8")
    variables = set(re.findall(r'os\.getenv\("([A-Z_]+)"', config))
    # Las que son alias viejos o detalles internos no hace falta documentarlas.
    internas = {"AXEL_USER_ID", "BARBU_USER_ID", "HOME", "PYTHONANYWHERE_DOMAIN"}
    faltan = [v for v in sorted(variables - internas) if v not in README]
    assert faltan == [], f"sin documentar en el README: {faltan}"


def test_el_mapa_de_agents_nombra_todos_los_modulos():
    modulos = {f.name for f in (RAIZ / "notita").glob("*.py")
               if f.name not in ("__init__.py", "demo.py", "deps.py",
                                 "pythonanywhere.py")}
    faltan = [m for m in sorted(modulos) if m not in AGENTS]
    assert faltan == [], f"AGENTS.md no nombra: {faltan}"


def test_agents_tiene_los_cinco_principios():
    for principio in ("Hablando se anota", "propone y el toque confirma",
                      "Nunca bloquear", "es verdad", "momento de reloj"):
        assert principio in AGENTS, principio


def test_el_guion_de_prueba_cubre_lo_importante():
    for tema in ("tablero", "Deshacer", "Cambiar algo", "🎤", "parte",
                 "Compramos todo", "propone", "privado"):
        assert tema in PROBAR, f"el guion no prueba: {tema}"


def test_el_readme_manda_al_guion_y_a_la_guia():
    assert "PROBAR.md" in README or "PROBAR.md" in AGENTS
    assert "AGENTS.md" in README
    assert "DISEÑO-v2.md" in README


def test_la_cantidad_de_tests_del_readme_es_plausible():
    """Si dice «N tests», que no sea de hace diez commits."""
    declarados = int(re.search(r"pytest\s+#\s*(\d+) tests", README).group(1))
    reales = sum(len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.M))
                 for f in (RAIZ / "tests").glob("test_*.py"))
    # Los parametrize inflan el número real, así que sólo se exige el orden correcto.
    assert reales <= declarados <= reales * 3, \
        f"el README dice {declarados} y hay {reales} funciones de test"


def test_el_ci_revisa_todos_los_scripts_y_ninguno_que_no_exista():
    """El CI corría pyflakes sobre `docs/demo/`, que se borró, y fallaba entero.

    Y no revisaba `migrar_chat.py`, que se agregó después.
    """
    ci = (RAIZ / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    linea = [x for x in ci.splitlines() if "-m pyflakes" in x][0]
    revisados = [x for x in linea.split() if x.endswith(".py") or x.endswith("/")]

    for destino in revisados:
        assert (RAIZ / destino).exists(), f"el CI revisa {destino}, que no existe"
    for script in RAIZ.glob("*.py"):
        assert script.name in revisados, f"el CI no revisa {script.name}"


def test_los_respaldos_de_la_base_estan_ignorados():
    """El repo es público y los respaldos tienen las tareas de la casa adentro.

    `*.db` no alcanza: el nombre termina en `.bak`
    (`notita.db.antes-de-v3.2026-09-29.bak`).
    """
    import fnmatch

    from notita import config, db

    patrones = [linea.strip() for linea in
                (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
                if linea.strip() and not linea.startswith("#")]

    nombres = [db._nombre_de_respaldo(pathlib.Path(config.DB_PATH), "v9").name,
               db._nombre_de_respaldo(pathlib.Path(config.DB_PATH), "limpiar").name,
               "notita.db.v1.bak",
               "notita.db.antes-de-mudarse.bak",
               "notita.db"]
    for nombre in nombres:
        assert any(fnmatch.fnmatch(nombre, p) for p in patrones), nombre
