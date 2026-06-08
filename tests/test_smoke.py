"""
Testes de smoke — verificam que as rotas principais respondem correctamente.
Correr: cd ~/Documentos/barbearia && python -m pytest tests/ -v

Não requerem BD real: usam SQLite em memória criada no fixture.
"""
import os
import sys
import pytest

# Garantir que o módulo app é importado com BD temporária
os.environ.setdefault("SECRET_KEY", "test-secret-key-apenas-para-testes")

# Adicionar o directório raiz ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def client():
    """Flask test client com BD em memória."""
    import database as db_module
    import db._conn as _db_conn
    import tempfile, shutil

    # BD temporária para testes
    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test.db")
    original_db = _db_conn.DB_PATH
    _db_conn.DB_PATH = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN = None

    # Criar schema
    db_module.init_db()

    import app as app_module
    app_module.app.config["TESTING"]             = True
    app_module.app.config["WTF_CSRF_ENABLED"]    = False
    app_module.app.config["SECRET_KEY"]          = "test-secret"
    app_module.app.config["SESSION_COOKIE_SECURE"] = False

    with app_module.app.test_client() as c:
        yield c

    _db_conn.DB_PATH = original_db
    db_module.DB_PATH = original_db
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Rotas públicas ──────────────────────────────────────────

def test_login_get_200(client):
    """GET /login retorna 200."""
    r = client.get("/login")
    assert r.status_code == 200

def test_login_post_credenciais_erradas(client):
    """POST /login com credenciais erradas retorna 200 (não 500, não redirect)."""
    r = client.post("/login", data={"username": "naoexiste", "senha": "errada"})
    assert r.status_code == 200
    assert b"incorretos" in r.data or b"Utilizador" in r.data

def test_offline_page(client):
    """GET /offline retorna 200 (PWA fallback)."""
    r = client.get("/offline")
    assert r.status_code == 200

def test_healthz(client):
    """GET /healthz retorna 200 com JSON status=ok."""
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"

def test_manifest_json(client):
    """GET /manifest.json retorna 200 com content-type correcto."""
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert "manifest" in r.content_type or "json" in r.content_type


# ── Rotas protegidas (sem sessão → redirect para login) ─────

def test_dashboard_sem_sessao_redireciona(client):
    """GET / sem sessão redireciona para /login."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 301)
    assert "/login" in r.headers.get("Location", "")

def test_historico_sem_sessao_redireciona(client):
    """GET /historico sem sessão redireciona para /login."""
    r = client.get("/historico", follow_redirects=False)
    assert r.status_code in (302, 301)

def test_api_estado_sem_sessao(client):
    """GET /api/estado sem sessão retorna 200 (polling público, sem dados sensíveis)."""
    r = client.get("/api/estado")
    # Pode retornar 200 (hash vazio) ou 401 conforme implementação
    assert r.status_code in (200, 401, 302)

def test_rate_limit_login(client):
    """Múltiplas tentativas de login erradas activam rate limiting."""
    for _ in range(12):
        client.post("/login", data={"username": "x", "senha": "errada"},
                    environ_base={"REMOTE_ADDR": "10.0.0.99"})
    # A 12ª tentativa deve ser bloqueada (rate limit >=10)
    r = client.post("/login", data={"username": "x", "senha": "errada"},
                    environ_base={"REMOTE_ADDR": "10.0.0.99"})
    assert r.status_code == 200
    assert b"tentativas" in r.data or b"Aguarda" in r.data


# ── Segurança básica ────────────────────────────────────────

def test_security_headers(client):
    """Respostas incluem headers de segurança obrigatórios."""
    r = client.get("/login")
    assert "X-Content-Type-Options" in r.headers
    assert "Strict-Transport-Security" in r.headers
    assert "Content-Security-Policy" in r.headers

def test_sem_header_server(client):
    """Header 'Server' não deve ser exposto (esconde stack tecnológico)."""
    r = client.get("/login")
    assert "Server" not in r.headers or r.headers.get("Server") == ""
