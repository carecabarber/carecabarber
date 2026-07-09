"""tests/test_railway_prep.py — Preparação da migração para Railway.

Verifica a plumbing gated-por-ambiente que fica DORMENTE em PythonAnywhere e só
ganha efeito quando o Railway injecta as variáveis:

  • LOGOS_DIR  → helpers_booking.LOGOS_DIR aponta para o volume; a app regista a
                 rota /static/logos/<f> a servir de lá (senão os logos perdiam-se
                 no filesystem efémero do Railway).
  • DB_PATH    → honrado (já coberto noutros módulos; aqui via wsgi).
  • CANONICAL_URL → exposto aos templates como cb_canonical (beacon anti-clone).

Import-time gating obriga a testar num subprocesso com o ambiente já definido —
recarregar a app no processo do pytest seria frágil.

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_railway_prep.py -v --tb=short
"""

import os
import sys
import subprocess
import tempfile
import textwrap

RAIZ = os.path.dirname(os.path.dirname(__file__))


def _correr(script: str, extra_env: dict) -> str:
    """Corre um script Python num subprocesso com env extra e devolve o stdout."""
    env = dict(os.environ)
    env.setdefault("SECRET_KEY", "test-railway-prep")
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=RAIZ, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"subprocesso falhou:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    return proc.stdout


def test_logos_dir_honra_env():
    """helpers_booking.LOGOS_DIR usa o valor de LOGOS_DIR quando definido."""
    alvo = tempfile.mkdtemp()
    out = _correr(
        "import helpers_booking, sys; sys.stdout.write(helpers_booking.LOGOS_DIR)",
        {"LOGOS_DIR": alvo},
    )
    assert out.strip() == alvo


def test_logos_dir_inerte_sem_env():
    """Sem LOGOS_DIR, cai no static/logos de sempre (PythonAnywhere byte-idêntico)."""
    out = _correr(
        "import helpers_booking, sys; sys.stdout.write(helpers_booking.LOGOS_DIR)",
        {"LOGOS_DIR": ""},
    )
    assert out.strip().endswith(os.path.join("static", "logos"))


def test_rota_logos_volume_registada_com_env():
    """Com LOGOS_DIR, a app regista a rota de override /static/logos/<path>."""
    alvo = tempfile.mkdtemp()
    out = _correr(
        "import app, sys; "
        "regras=[r.rule for r in app.app.url_map.iter_rules()]; "
        "sys.stdout.write('SIM' if '/static/logos/<path:filename>' in regras else 'NAO')",
        {"LOGOS_DIR": alvo},
    )
    assert out.strip() == "SIM"


def test_rota_logos_volume_ausente_sem_env():
    """Sem LOGOS_DIR, a rota de override NÃO existe (usa o /static nativo do Flask)."""
    out = _correr(
        "import app, sys; "
        "regras=[r.rule for r in app.app.url_map.iter_rules()]; "
        "sys.stdout.write('SIM' if '/static/logos/<path:filename>' in regras else 'NAO')",
        {"LOGOS_DIR": ""},
    )
    assert out.strip() == "NAO"


def test_rota_logos_volume_serve_ficheiro():
    """A rota de override serve realmente um ficheiro a partir do volume."""
    alvo = tempfile.mkdtemp()
    with open(os.path.join(alvo, "teste.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")   # cabeçalho PNG mínimo
    out = _correr(
        "import app; "
        "app.app.config['TESTING']=True; "
        "c=app.app.test_client(); "
        "r=c.get('/static/logos/teste.png'); "
        "import sys; sys.stdout.write(str(r.status_code))",
        {"LOGOS_DIR": alvo},
    )
    assert out.strip() == "200"


def test_canonical_url_default():
    """Sem CANONICAL_URL, cai no domínio PythonAnywhere actual."""
    out = _correr(
        "import app, sys; sys.stdout.write(app._CANONICAL_URL)",
        {"CANONICAL_URL": ""},
    )
    assert out.strip() == "https://carecabarber.pythonanywhere.com"


def test_canonical_url_honra_env():
    """Com CANONICAL_URL, o beacon aponta para o novo domínio."""
    out = _correr(
        "import app, sys; sys.stdout.write(app._CANONICAL_URL)",
        {"CANONICAL_URL": "https://carecabarber.com"},
    )
    assert out.strip() == "https://carecabarber.com"
