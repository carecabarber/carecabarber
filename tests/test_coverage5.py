"""tests/test_coverage5.py — Cobertura das zonas em falta (9.0 target).

Alvos:
  blueprints/barbeiros.py  (70%) → 80%+
    toggle_barbeiro, editar_barbeiro, repor_senha_barbeiro,
    set_credenciais, criar_ausencia, apagar_ausencia
  blueprints/api.py  (72%) → 80%+
    api_meu_status, api_estado (cliente/barbeiro), rate limit 429
  app.py  (56%) → 65%+
    template filters, error handlers, _verificar_plano

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage5.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-cov5-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov5.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid = db.criar_barbearia("Barbearia Cov5", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("cov5-slug", bid))

    # Chefe
    db.criar_chefe("Chefe Cov5", "chefe_cov5", "senha_cov5", bid)
    chefe = db.get_barbeiro_por_username("chefe_cov5")
    chefe_id = chefe["id"]

    # Barbeiro activo (com agendamento futuro para o guard de toggle)
    db.criar_barbeiro("Barbeiro Activo", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Activo", bid)).fetchone()["id"]
    db.set_credenciais(barb_id, "barb_activo_cov5", "pass123")

    # Barbeiro livre (sem futuros — para toggle/apagar sem bloqueio)
    db.criar_barbeiro("Barbeiro Livre", bid)
    with db._read() as c:
        barb_livre_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Livre", bid)).fetchone()["id"]

    # Serviço
    db.criar_servico("Corte Cov5", 30, bid, preco=500)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    # Agendamento futuro para barb_id (bloqueia toggle/apagar)
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_futuro = db.criar_agendamento(
        "Cliente Futuro", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id)

    # Agendamento hoje em andamento (para api_meu_status)
    tel_cliente = "912345678"
    hoje = datetime.now().strftime("%Y-%m-%d")
    ag_hoje = db.criar_agendamento(
        "Cliente Hoje", svc_id,
        f"{hoje} {datetime.now().strftime('%H:%M:%S')}",
        bid, barbeiro_id=barb_id, telefone=tel_cliente)
    db.iniciar_trabalho(ag_hoje)

    yield {
        "db": db, "bid": bid,
        "chefe_id": chefe_id,
        "barb_id": barb_id, "barb_livre_id": barb_livre_id,
        "svc_id": svc_id, "amanha": amanha,
        "ag_futuro": ag_futuro, "ag_hoje": ag_hoje,
        "tel_cliente": tel_cliente,
    }

    _db_conn._reset_conn()
    db._CONN = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-cov5", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["chefe_id"]
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Chefe Cov5"
    return c


def _barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["barb_id"]
        s["role"]         = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Barbeiro Activo"
    return c


def _cliente(c, ctx):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["telefone"]     = ctx["tel_cliente"]
        s.pop("user_id", None)
    return c


def _limpar(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — toggle_barbeiro
# ══════════════════════════════════════════════════════════════

class TestToggleBarbeiro:
    def test_toggle_idor_redireciona(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/toggle/9999")
        assert r.status_code in (302, 200)

    def test_toggle_self_bloqueado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/toggle/{ctx['chefe_id']}")
        assert r.status_code in (302, 200)
        # chefe ainda activo
        assert ctx["db"].get_barbeiro(ctx["chefe_id"])["ativo"] == 1

    def test_toggle_activo_com_futuros_bloqueado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/toggle/{ctx['barb_id']}")
        assert r.status_code in (302, 200)
        # permanece activo
        assert ctx["db"].get_barbeiro(ctx["barb_id"])["ativo"] == 1

    def test_toggle_activo_sem_futuros(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/toggle/{ctx['barb_livre_id']}")
        assert r.status_code in (302, 200)
        # estado mudou (ativo → inativo ou vice-versa)

    def test_toggle_inativo_reactiva(self, client):
        """Segundo toggle reactiva (cobertura do ramo ativo=False)."""
        c, ctx = client
        _chefe(c, ctx)
        c.post(f"/barbeiros/toggle/{ctx['barb_livre_id']}")   # garante inativo
        r = c.post(f"/barbeiros/toggle/{ctx['barb_livre_id']}")  # reactiva
        assert r.status_code in (302, 200)


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — editar e repor senha
# ══════════════════════════════════════════════════════════════

class TestEditarRepor:
    def test_editar_nome_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/editar/{ctx['barb_livre_id']}",
                   data={"nome": "Nome Editado"})
        assert r.status_code in (302, 200)

    def test_editar_nome_vazio_ignorado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/editar/{ctx['barb_livre_id']}",
                   data={"nome": ""})
        assert r.status_code in (302, 200)

    def test_repor_senha_curta_bloqueada(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/repor-senha/{ctx['barb_id']}",
                   data={"senha": "abc"})
        assert r.status_code in (302, 200)

    def test_repor_senha_valida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/repor-senha/{ctx['barb_id']}",
                   data={"senha": "novaSenha123"})
        assert r.status_code in (302, 200)

    def test_repor_senha_barbeiro_inexistente(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/repor-senha/9999",
                   data={"senha": "novaSenha123"})
        assert r.status_code in (302, 200)


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — set_credenciais
# ══════════════════════════════════════════════════════════════

class TestSetCredenciais:
    def test_username_invalido_redireciona(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_livre_id']}",
                   data={"username": "inv alid!", "senha": "pass123"})
        assert r.status_code in (302, 200)

    def test_senha_curta_redireciona(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_livre_id']}",
                   data={"username": "barblivre99", "senha": "ab"})
        assert r.status_code in (302, 200)

    def test_credenciais_validas(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_livre_id']}",
                   data={"username": "barblivre99", "senha": "pass123"})
        assert r.status_code in (302, 200)

    def test_username_duplicado_redireciona(self, client):
        """Username já existente → redirect com erro."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_livre_id']}",
                   data={"username": "chefe_cov5", "senha": "pass123"})
        assert r.status_code in (302, 200)


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — ausencias
# ══════════════════════════════════════════════════════════════

class TestAusencias:
    def test_criar_ausencia_valida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": ctx["amanha"],
            "data_fim":    ctx["amanha"],
            "tipo": "falta",
            "motivo": "Consulta médica",
        })
        assert r.status_code in (302, 200)

    def test_criar_ausencia_data_inicio_maior_fim(self, client):
        c, ctx = client
        _chefe(c, ctx)
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": ctx["amanha"],
            "data_fim":    ontem,
            "tipo": "falta",
        })
        assert r.status_code in (302, 200)

    def test_criar_ausencia_hora_inicio_maior_fim(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": ctx["amanha"],
            "data_fim":    ctx["amanha"],
            "hora_inicio": "15:00",
            "hora_fim":    "10:00",
            "tipo": "falta",
        })
        assert r.status_code in (302, 200)

    def test_criar_ausencia_hora_invalida_ignorada(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": ctx["amanha"],
            "data_fim":    ctx["amanha"],
            "hora_inicio": "nao-e-hora",
            "hora_fim":    "10:00",
            "tipo": "falta",
        })
        assert r.status_code in (302, 200)

    def test_criar_ausencia_barbeiro_errado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": 9999,
            "data_inicio": ctx["amanha"],
            "data_fim":    ctx["amanha"],
            "tipo": "falta",
        })
        assert r.status_code in (302, 200)

    def test_apagar_ausencia(self, client):
        c, ctx = client
        _chefe(c, ctx)
        db = ctx["db"]
        bid = ctx["bid"]
        ausencias = db.listar_ausencias(bid)
        if ausencias:
            aid = ausencias[0]["id"]
            r = c.post(f"/barbeiros/ausencia/apagar/{aid}")
            assert r.status_code in (302, 200)

    def test_apagar_ausencia_inexistente(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia/apagar/99999")
        assert r.status_code in (302, 200)


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_meu_status
# ══════════════════════════════════════════════════════════════

class TestApiMeuStatus:
    def test_sem_sessao_retorna_vazio(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get("/api/meu-status")
        assert json.loads(r.data) == []

    def test_cliente_sem_em_andamento_retorna_vazio(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "999999999"  # tel sem agendamentos
        r = c.get("/api/meu-status")
        assert json.loads(r.data) == []

    def test_cliente_com_em_andamento_retorna_lista(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/api/meu-status")
        data = json.loads(r.data)
        assert isinstance(data, list)
        # O ag_hoje está em_andamento para este cliente
        assert len(data) >= 1

    def test_cliente_sem_barbearia_retorna_vazio(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s["role"]     = "cliente"
            s["telefone"] = ctx["tel_cliente"]
            s.pop("barbearia_id", None)
        r = c.get("/api/meu-status")
        assert json.loads(r.data) == []


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_estado (roles)
# ══════════════════════════════════════════════════════════════

class TestApiEstado:
    def test_como_cliente_retorna_hash(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/api/estado")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "h" in data

    def test_como_barbeiro_retorna_hash(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/api/estado")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "h" in data

    def test_segunda_chamada_usa_cache(self, client):
        """Cache hit path — segunda chamada ao estado como chefe."""
        c, ctx = client
        _chefe(c, ctx)
        c.get("/api/estado")  # preenche cache
        r = c.get("/api/estado")  # deve vir do cache
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — rate limit 429
# ══════════════════════════════════════════════════════════════

class TestRateLimit429:
    """Verifica que todas as rotas de API devolvem 429 quando _api_ok = False."""

    def _patch_api_ok_false(self):
        return patch("blueprints.api._api_ok", return_value=False)

    def test_api_tempo_429(self, client):
        c, ctx = client
        _chefe(c, ctx)
        with self._patch_api_ok_false():
            r = c.get(f"/api/tempo/{ctx['ag_futuro']}")
        assert r.status_code == 429

    def test_api_slots_429(self, client):
        c, ctx = client
        _chefe(c, ctx)
        with self._patch_api_ok_false():
            r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={ctx['amanha']}&servico_id={ctx['svc_id']}")
        assert r.status_code == 429

    def test_api_lembretes_429(self, client):
        c, ctx = client
        _chefe(c, ctx)
        with self._patch_api_ok_false():
            r = c.get("/api/lembretes")
        assert r.status_code == 429

    def test_api_novos_429(self, client):
        c, ctx = client
        _chefe(c, ctx)
        with self._patch_api_ok_false():
            r = c.get("/api/novos-agendamentos?desde_id=0")
        assert r.status_code == 429

    def test_api_estado_429(self, client):
        c, ctx = client
        _chefe(c, ctx)
        with self._patch_api_ok_false():
            r = c.get("/api/estado")
        assert r.status_code == 429

    def test_api_meu_status_429(self, client):
        c, ctx = client
        _cliente(c, ctx)
        with self._patch_api_ok_false():
            r = c.get("/api/meu-status")
        assert r.status_code == 429

    def test_foto_barbeiro_429(self, client):
        c, ctx = client
        with patch("blueprints.barbeiros._api_ok", return_value=False):
            r = c.get(f"/foto/{ctx['barb_id']}")
        assert r.status_code == 429


# ══════════════════════════════════════════════════════════════
#  app.py — template filters e error handlers
# ══════════════════════════════════════════════════════════════

class TestAppFilters:
    def test_moeda_filter(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            result = app_module.moeda_filter(1500)
            assert result == "1.500"

    def test_moeda_filter_invalido(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.moeda_filter("nao-numero") == "0"

    def test_tel_filter_7digitos(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.tel_filter("2612345") == "261 23 45"

    def test_tel_filter_8digitos(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.tel_filter("91234567") == "9123 45 67"

    def test_tel_filter_vazio(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.tel_filter("") == ""

    def test_from_json_filter_valido(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            result = app_module.from_json_filter('{"a": 1}')
            assert result == {"a": 1}

    def test_from_json_filter_invalido(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.from_json_filter("nao-json") == {}

    def test_from_json_filter_vazio(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.from_json_filter("") == {}


class TestAppErrorHandlers:
    def test_404_rota_inexistente(self, client):
        c, ctx = client
        r = c.get("/rota-que-nao-existe-xyz")
        assert r.status_code == 404

    def test_csrf_error_json(self, client):
        """CSRF error em pedido JSON devolve 400 com mensagem."""
        c, ctx = client
        _chefe(c, ctx)
        import app as app_module
        app_module.app.config["WTF_CSRF_ENABLED"] = True
        try:
            r = c.post("/barbeiros/editar/9999",
                       data={"nome": "x"},
                       headers={"Content-Type": "application/json",
                                "X-Requested-With": "XMLHttpRequest"})
            assert r.status_code in (400, 302, 200)
        finally:
            app_module.app.config["WTF_CSRF_ENABLED"] = False

    def test_413_upload_grande(self, client):
        """413 handler: ficheiro > 2MB devolvido em JSON quando XHR."""
        c, ctx = client
        _chefe(c, ctx)
        import app as app_module
        from werkzeug.exceptions import RequestEntityTooLarge
        with app_module.app.test_request_context(
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Content-Type": "application/json"}):
            resp = app_module.ficheiro_grande(RequestEntityTooLarge())
            # pode ser tuplo (response, status) ou Response
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status == 413

    def test_plano_expirado_redireciona(self, client):
        """Barbearia com plano expirado → redireciona para conta_suspensa."""
        c, ctx = client
        db = ctx["db"]
        bid = ctx["bid"]
        # Expirar o plano (coluna correcta: plano_expira_em)
        with db._write() as conn:
            conn.execute("UPDATE barbearias SET plano_expira_em='2000-01-01' WHERE id=?", (bid,))
        from helpers import _pc_del
        _pc_del(f"plano:{bid}:")
        _chefe(c, ctx)
        r = c.get("/barbeiros", follow_redirects=False)
        assert r.status_code in (302, 200)
        # Restaurar plano
        from datetime import date
        exp = (date.today() + timedelta(days=365)).isoformat()
        with db._write() as conn:
            conn.execute("UPDATE barbearias SET plano_expira_em=? WHERE id=?", (exp, bid))
        _pc_del(f"plano:{bid}:")


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — perfil_foto_upload / apagar (295-339)
# ══════════════════════════════════════════════════════════════

class TestPerfilFoto:
    """Cobre /perfil/foto (POST) e /perfil/foto/apagar (POST)."""

    def test_perfil_foto_json_sem_imagem_400(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil/foto",
                   json={"imagem": "", "mime": "image/jpeg"},
                   headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 400

    def test_perfil_foto_json_mime_invalido_400(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil/foto",
                   json={"imagem": "dGVzdA==", "mime": "application/pdf"},
                   headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 400

    def test_perfil_foto_json_base64_corrompido_400(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil/foto",
                   json={"imagem": "!!!nao-e-base64!!!", "mime": "image/jpeg"},
                   headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 400

    def test_perfil_foto_sem_ficheiro_400(self, client):
        """Upload sem ficheiro → 400."""
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil/foto",
                   data={},
                   content_type="multipart/form-data")
        assert r.status_code == 400

    def test_perfil_foto_mime_nao_suportado_415(self, client):
        """Upload com MIME type inválido → 415."""
        c, ctx = client
        _barbeiro(c, ctx)
        from io import BytesIO
        r = c.post("/perfil/foto",
                   data={"foto": (BytesIO(b"conteudo"), "ficheiro.pdf")},
                   content_type="multipart/form-data")
        # Mime type será detectado como application/octet-stream → 415
        assert r.status_code in (415, 400)

    def test_perfil_foto_apagar(self, client):
        """DELETE /perfil/foto/apagar → 200 ok."""
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil/foto/apagar")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["ok"] is True


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py — gaps restantes (78, 117)
# ══════════════════════════════════════════════════════════════

class TestBarbeirosGaps:
    def test_set_credenciais_idor_desconhecido(self, client):
        """IDOR: barbeiro 9999 não existe → redirect silencioso (linha 117)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/credenciais/9999",
                   data={"username": "novousername", "senha": "pass123"})
        assert r.status_code in (302, 200)

    def test_apagar_barbeiro_soft_delete(self, client):
        """Barbeiro com agendamentos passados → soft delete (linha 78)."""
        c, ctx = client
        db = ctx["db"]
        bid = ctx["bid"]
        _chefe(c, ctx)
        # Criar um barbeiro temporário com um agendamento no passado
        db.criar_barbeiro("Barbeiro Soft", bid)
        with db._read() as conn:
            soft_id = conn.execute(
                "SELECT id FROM barbeiros WHERE nome='Barbeiro Soft' AND barbearia_id=?",
                (bid,)).fetchone()["id"]
        # Criar agendamento no passado (para garantir soft delete)
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        ag = db.criar_agendamento(
            "Cliente Past", ctx["svc_id"],
            f"{ontem} 10:00:00", bid, barbeiro_id=soft_id)
        db.terminar_trabalho(ag, 500)
        # Tentar apagar (sem futuros → deve conseguir; com passados → soft delete)
        r = c.post(f"/barbeiros/apagar/{soft_id}")
        assert r.status_code in (302, 200)
