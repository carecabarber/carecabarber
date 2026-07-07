"""tests/test_dominio_proprio.py — Domínio próprio por estabelecimento.

Cobre a fundação (dormente) para cada estabelecimento poder ter o seu próprio
domínio: normalização, resolução Host→estabelecimento (só se verificado),
unicidade, e o middleware que redirecciona a raiz do domínio próprio para a
entrada de cliente — garantindo que o domínio principal se mantém inalterado.

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_dominio_proprio.py -v --tb=short
"""

import os, sys, tempfile, pytest

os.environ.setdefault("SECRET_KEY", "test-dominio-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_dominio.db")

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid_a = db.criar_barbearia("Careca Barber", tipo="barbearia")
    bid_b = db.criar_barbearia("Barbearia do Joao", tipo="barbearia")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("careca-barber", bid_a))
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("barbearia-do-joao", bid_b))

    return {"db": db, "bid_a": bid_a, "bid_b": bid_b,
            "slug_a": "careca-barber", "slug_b": "barbearia-do-joao"}


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-dominio",
        "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx, app_module


# ══════════════════════════════════════════════════════════════
#  normalizar_dominio (pura)
# ══════════════════════════════════════════════════════════════

class TestNormalizar:

    @pytest.mark.parametrize("entrada,esperado", [
        ("https://www.Joao.COM/",   "joao.com"),
        ("joao.com",                "joao.com"),
        (" JOAO.com:443 ",          "joao.com"),
        ("www.joao.com/agenda",     "joao.com"),
        ("http://sub.joao.pt",      "sub.joao.pt"),
        ("",                        None),
        (None,                      None),
        ("   ",                     None),
    ])
    def test_normalizacao(self, ctx, entrada, esperado):
        assert ctx["db"].normalizar_dominio(entrada) == esperado


# ══════════════════════════════════════════════════════════════
#  Camada BD: set / verificar / resolver
# ══════════════════════════════════════════════════════════════

class TestCamadaBD:

    def test_nao_resolve_ate_verificar(self, ctx):
        db = ctx["db"]
        db.set_dominio(ctx["bid_a"], "https://www.CarecaBarber.com/")
        # Guardado mas ainda NÃO verificado → não resolve (segurança)
        assert db.get_barbearia_por_dominio("carecabarber.com") is None
        # Root confirma
        db.verificar_dominio(ctx["bid_a"], True)
        b = db.get_barbearia_por_dominio("carecabarber.com")
        assert b is not None and b["id"] == ctx["bid_a"]
        # Normalização também no lookup (www + esquema)
        assert db.get_barbearia_por_dominio("www.carecabarber.com") is not None

    def test_dominio_unico(self, ctx):
        db = ctx["db"]
        # bid_a já tem carecabarber.com; bid_b não pode reclamá-lo
        with pytest.raises(ValueError):
            db.set_dominio(ctx["bid_b"], "carecabarber.com")

    def test_alterar_dominio_repoe_verificacao(self, ctx):
        db = ctx["db"]
        db.set_dominio(ctx["bid_b"], "joao.com")
        db.verificar_dominio(ctx["bid_b"], True)
        assert db.get_barbearia_por_dominio("joao.com")["id"] == ctx["bid_b"]
        # Mudar o domínio deve repor verificado=0
        db.set_dominio(ctx["bid_b"], "joaobarber.pt")
        assert db.get_barbearia_por_dominio("joaobarber.pt") is None  # por verificar
        assert db.get_barbearia_por_dominio("joao.com") is None       # já não existe

    def test_limpar_dominio(self, ctx):
        db = ctx["db"]
        db.set_dominio(ctx["bid_b"], None)
        b = db.get_barbearia(ctx["bid_b"])
        assert b["dominio"] is None and b["dominio_verificado"] == 0


# ══════════════════════════════════════════════════════════════
#  Middleware de routing por domínio
# ══════════════════════════════════════════════════════════════

class TestMiddleware:

    def test_raiz_dominio_proprio_redirecciona(self, client):
        c, ctx, app_module = client
        db = ctx["db"]
        db.set_dominio(ctx["bid_a"], "carecabarber.com")
        db.verificar_dominio(ctx["bid_a"], True)
        app_module._pcache.clear()   # evitar cache negativo de testes anteriores
        r = c.get("/", base_url="http://carecabarber.com")
        assert r.status_code == 302
        assert f"/cliente/{ctx['slug_a']}" in r.headers["Location"]

    def test_caminho_cliente_funciona_sob_dominio_proprio(self, client):
        c, ctx, app_module = client
        app_module._pcache.clear()
        r = c.get(f"/cliente/{ctx['slug_a']}", base_url="http://carecabarber.com")
        assert r.status_code == 200

    def test_dominio_principal_inalterado(self, client):
        """Host normal (domínio principal) NÃO é redireccionado para /cliente/*."""
        c, ctx, app_module = client
        app_module._pcache.clear()
        r = c.get("/", base_url="http://carecabarber-app.up.railway.app")
        # Comportamento normal da raiz do staff (redirect para login), nunca /cliente/*
        assert "/cliente/" not in r.headers.get("Location", "")

    def test_dominio_por_verificar_nao_redirecciona(self, client):
        c, ctx, app_module = client
        db = ctx["db"]
        db.set_dominio(ctx["bid_b"], "porverificar.com")   # NÃO verificado
        app_module._pcache.clear()
        r = c.get("/", base_url="http://porverificar.com")
        assert "/cliente/" not in r.headers.get("Location", "")
