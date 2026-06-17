"""
tests/test_coverage_extra3.py — Testes focados nos gaps de cobertura restantes.

Cobre:
  - blueprints/api.py: exceção datetime em api_tempo, api_slots sem/com barbeiro_id,
                        cache hit em api_lembretes, parse error lembretes,
                        api_cliente_push_subscribe (ok + exceção + dessubscrição),
                        api_spec, listar_barbearias erro
  - blueprints/pwa.py: DB exception em /healthz
  - db/barbeiros.py: criar_barbeiro, criar_chefe, set_credenciais, registar_credencial,
                     get_credenciais_barbeiro, get_credencial_por_id, atualizar_sign_count,
                     apagar_credencial, alterar_senha
  - helpers_booking.py: _val_data paths, _dentro_horario com horário inválido/sem horário,
                         slot_fim > fecho

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage_extra3.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-extra3")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE PARTILHADA
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_extra3.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    bid = db_module.criar_barbearia("Barbearia Extra3", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    db_module.criar_chefe("Chefe X3", "chefe_x3", "senha_x3", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_x3")["id"]

    db_module.criar_barbeiro("Barbeiro X3", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro X3", bid)).fetchone()["id"]
    db_module.set_credenciais(barb_id, "barb_x3", "pass_x3")

    db_module.criar_servico("Corte X3", 30, bid, preco=800)
    with db_module._read() as c:
        rows = c.execute("SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchall()
    svc_id = rows[0]["id"]

    for dia in range(6):
        db_module.set_horario_dia(dia, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(6, "08:00", "19:00", 1, bid)

    hoje   = datetime.now().strftime("%Y-%m-%d")
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    yield {
        "db":       db_module,
        "bid":      bid,
        "chefe_id": chefe_id,
        "barb_id":  barb_id,
        "svc_id":   svc_id,
        "hoje":     hoje,
        "amanha":   amanha,
        "tmp_dir":  tmp_dir,
    }

    _db_conn._reset_conn()
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-extra3", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _sessao_chefe(c, ctx):
    with c.session_transaction() as s:
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_id"]      = ctx["chefe_id"]
        s["user_nome"]    = "Chefe X3"
    return c


def _sessao_barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["role"]         = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_id"]      = ctx["barb_id"]
        s["user_nome"]    = "Barbeiro X3"
    return c


def _sessao_cliente(c, ctx, telefone="912345600"):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["user_id"]      = None
        s["telefone"]     = telefone
        s["user_nome"]    = "Cliente X3"
    return c


def _limpar_sessao(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_tempo: exceção de parse datetime
# ══════════════════════════════════════════════════════════════

class TestApiTempoExcecao:

    def test_inicio_invalido_retorna_zeros(self, client):
        """api_tempo: ag['inicio'] com valor não parseable → retorna segundos=0."""
        c, ctx = client
        _sessao_chefe(c, ctx)

        ag_fake = {
            "id": 999,
            "inicio": "data-invalida",
            "barbearia_id": ctx["bid"],
            "servico_id": ctx["svc_id"],
        }
        with patch.object(__import__("database"), "get_agendamento", return_value=ag_fake), \
             patch.object(__import__("database"), "servico_por_id", return_value={"duracao_min": 30}):
            resp = c.get("/api/tempo/999")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["segundos"] == 0
        assert data["estimado"] == 0


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_slots
# ══════════════════════════════════════════════════════════════

class TestApiSlots:

    def test_slots_sem_parametros_retorna_lista_vazia(self, client):
        """api_slots sem barbeiro_id → retorna []."""
        c, ctx = client
        _limpar_sessao(c)
        resp = c.get("/api/slots")
        assert resp.status_code == 200
        assert json.loads(resp.data) == []

    def test_slots_data_invalida_retorna_lista_vazia(self, client):
        """api_slots com data mal formatada → retorna []."""
        c, ctx = client
        _limpar_sessao(c)
        resp = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data=31-13-2025&servico_id={ctx['svc_id']}")
        assert resp.status_code == 200
        assert json.loads(resp.data) == []

    def test_slots_barbeiro_inexistente(self, client):
        """api_slots com barbeiro que não existe → retorna []."""
        c, ctx = client
        _limpar_sessao(c)
        amanha = ctx["amanha"]
        with patch.object(__import__("database"), "get_barbeiro", return_value=None):
            resp = c.get(f"/api/slots?barbeiro_id=9999&data={amanha}&servico_id={ctx['svc_id']}")
        assert resp.status_code == 200
        assert json.loads(resp.data) == []

    def test_slots_sem_barbearia_id_na_sessao(self, client):
        """api_slots sem barbearia_id na sessão: usa barbearia_id do barbeiro."""
        c, ctx = client
        _limpar_sessao(c)
        amanha = ctx["amanha"]

        barb_fake  = {"id": ctx["barb_id"], "barbearia_id": ctx["bid"]}
        svc_fake   = {"id": ctx["svc_id"],  "barbearia_id": ctx["bid"], "duracao_min": 30}
        slots_fake = [{"hora": "10:00", "tipo": "normal", "espera_min": 0}]

        with patch.object(__import__("database"), "get_barbeiro", return_value=barb_fake), \
             patch.object(__import__("database"), "servico_por_id", return_value=svc_fake), \
             patch.object(__import__("database"), "horarios_disponiveis", return_value=slots_fake):
            resp = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}&servico_id={ctx['svc_id']}")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["hora"] == "10:00"

    def test_slots_barbearia_id_na_sessao_idor_check(self, client):
        """api_slots com barbearia_id na sessão diferente do barbeiro → retorna []."""
        c, ctx = client
        _sessao_chefe(c, ctx)

        barb_fake = {"id": ctx["barb_id"], "barbearia_id": 9999}  # barbearia diferente
        amanha    = ctx["amanha"]

        with patch.object(__import__("database"), "get_barbeiro", return_value=barb_fake):
            resp = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}&servico_id={ctx['svc_id']}")

        assert resp.status_code == 200
        assert json.loads(resp.data) == []

    def test_slots_cliente_marca_minha_marcacao(self, client):
        """api_slots com role=cliente e marcação própria → slot marcado como minha_marcacao."""
        c, ctx = client
        _sessao_cliente(c, ctx, telefone="912000001")
        amanha = ctx["amanha"]

        barb_fake  = {"id": ctx["barb_id"], "barbearia_id": ctx["bid"]}
        svc_fake   = {"id": ctx["svc_id"],  "barbearia_id": ctx["bid"], "duracao_min": 30}
        slots_fake = [{"hora": "10:00", "tipo": "normal", "espera_min": 0}]
        ag_fake    = [{"data_hora": f"{amanha} 10:00:00"}]

        with patch.object(__import__("database"), "get_barbeiro", return_value=barb_fake), \
             patch.object(__import__("database"), "servico_por_id", return_value=svc_fake), \
             patch.object(__import__("database"), "horarios_disponiveis", return_value=slots_fake), \
             patch.object(__import__("database"), "agendamentos_cliente_barbeiro_dia", return_value=ag_fake):
            resp = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}&servico_id={ctx['svc_id']}")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data[0]["tipo"] == "minha_marcacao"


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_lembretes: cache hit e parse error
# ══════════════════════════════════════════════════════════════

class TestApiLembretes:

    def test_lembretes_cache_hit(self, client):
        """api_lembretes: quando cache tem valor, devolve-o sem ir à DB."""
        c, ctx = client
        _sessao_chefe(c, ctx)

        cached_data = [{"id": 1, "cliente": "Cached", "telefone": "912000000",
                        "hora": "10:00", "servico": "Corte", "minutos_ate": 5}]

        import helpers_booking as hb
        ck = f"lemb:{ctx['bid']}:None:30"  # default minutos=30
        hb._pc_set(ck, cached_data, 60)

        try:
            with patch.object(__import__("database"), "proximos_agendamentos") as mock_db:
                resp = c.get("/api/lembretes")
            mock_db.assert_not_called()
        finally:
            hb._pc_del(f"lemb:{ctx['bid']}:")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == cached_data

    def test_lembretes_parse_error_data_hora(self, client):
        """api_lembretes: data_hora inválida → minutos_ate=0 sem excepção."""
        c, ctx = client
        _sessao_chefe(c, ctx)

        ag_fake  = [{"id": 1, "cliente": "Cli", "telefone": "912000000",
                     "data_hora": "invalida", "servico_id": ctx["svc_id"]}]
        svc_fake = [{"id": ctx["svc_id"], "nome": "Corte X3"}]

        import helpers_booking as hb
        ck = f"lemb:{ctx['bid']}:None"
        hb._pc_del(f"lemb:{ctx['bid']}:")

        with patch.object(__import__("database"), "proximos_agendamentos", return_value=ag_fake), \
             patch.object(__import__("database"), "listar_servicos", return_value=svc_fake):
            resp = c.get("/api/lembretes")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data[0]["minutos_ate"] == 0

        hb._pc_del(f"lemb:{ctx['bid']}:")


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_cliente_push_subscribe e unsubscribe
# ══════════════════════════════════════════════════════════════

class TestApiClientePush:

    def test_subscribe_sem_sessao_cliente_retorna_403(self, client):
        """api_cliente_push_subscribe sem role=cliente → 403."""
        c, ctx = client
        _sessao_chefe(c, ctx)
        resp = c.post("/api/cliente-push/subscribe",
                      json={"endpoint": "https://ep/1", "p256dh": "p", "auth": "a"})
        assert resp.status_code == 403

    def test_subscribe_push_nao_disponivel_retorna_503(self, client):
        """api_cliente_push_subscribe com _PUSH_OK=False → 503."""
        c, ctx = client
        _sessao_cliente(c, ctx)
        with patch("helpers._PUSH_OK", False):
            resp = c.post("/api/cliente-push/subscribe",
                          json={"endpoint": "https://ep/1", "p256dh": "p", "auth": "a"})
        assert resp.status_code == 503

    def test_subscribe_dados_incompletos_retorna_400(self, client):
        """api_cliente_push_subscribe sem endpoint → 400."""
        c, ctx = client
        _sessao_cliente(c, ctx)
        with patch("helpers._PUSH_OK", True):
            resp = c.post("/api/cliente-push/subscribe",
                          json={"endpoint": "", "p256dh": "", "auth": ""})
        assert resp.status_code == 400

    def test_subscribe_ok(self, client):
        """api_cliente_push_subscribe com dados válidos → 200 ok=True."""
        c, ctx = client
        _sessao_cliente(c, ctx, telefone="912100001")
        with patch("helpers._PUSH_OK", True), \
             patch.object(__import__("database"), "cliente_push_guardar") as mock_guardar:
            resp = c.post("/api/cliente-push/subscribe",
                          json={"endpoint": "https://ep/ok", "p256dh": "pk", "auth": "ak"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        mock_guardar.assert_called_once()

    def test_subscribe_excepcao_retorna_500(self, client):
        """api_cliente_push_subscribe com exceção em cliente_push_guardar → 500."""
        c, ctx = client
        _sessao_cliente(c, ctx, telefone="912100002")
        with patch("helpers._PUSH_OK", True), \
             patch.object(__import__("database"), "cliente_push_guardar",
                          side_effect=Exception("DB error")):
            resp = c.post("/api/cliente-push/subscribe",
                          json={"endpoint": "https://ep/err", "p256dh": "pk", "auth": "ak"})
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert data["ok"] is False

    def test_unsubscribe_sem_sessao_cliente_retorna_403(self, client):
        """api_cliente_push_unsubscribe sem role=cliente → 403."""
        c, ctx = client
        _sessao_chefe(c, ctx)
        resp = c.post("/api/cliente-push/unsubscribe",
                      json={"endpoint": "https://ep/1"})
        assert resp.status_code == 403

    def test_unsubscribe_com_endpoint_chama_remover(self, client):
        """api_cliente_push_unsubscribe com endpoint → chama cliente_push_remover."""
        c, ctx = client
        _sessao_cliente(c, ctx)
        with patch.object(__import__("database"), "cliente_push_remover") as mock_rem:
            resp = c.post("/api/cliente-push/unsubscribe",
                          json={"endpoint": "https://ep/rem"})
        assert resp.status_code == 200
        mock_rem.assert_called_once_with("https://ep/rem")

    def test_unsubscribe_excepcao_absorvida(self, client):
        """api_cliente_push_unsubscribe com exceção em remover → 200 ok=True (absorvida)."""
        c, ctx = client
        _sessao_cliente(c, ctx)
        with patch.object(__import__("database"), "cliente_push_remover",
                          side_effect=Exception("DB error")):
            resp = c.post("/api/cliente-push/unsubscribe",
                          json={"endpoint": "https://ep/exc"})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True

    def test_unsubscribe_sem_endpoint_nao_chama_remover(self, client):
        """api_cliente_push_unsubscribe sem endpoint → não chama cliente_push_remover."""
        c, ctx = client
        _sessao_cliente(c, ctx)
        with patch.object(__import__("database"), "cliente_push_remover") as mock_rem:
            resp = c.post("/api/cliente-push/unsubscribe", json={})
        assert resp.status_code == 200
        mock_rem.assert_not_called()


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py — api_spec
# ══════════════════════════════════════════════════════════════

class TestApiSpec:

    def test_api_spec_retorna_rotas(self, client):
        """GET /api/spec → 200 com campos version, routes, total_routes."""
        c, ctx = client
        _limpar_sessao(c)
        resp = c.get("/api/spec")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["version"] == "1.0"
        assert "routes" in data
        assert "total_routes" in data
        assert isinstance(data["routes"], list)
        assert len(data["routes"]) > 0

    def test_api_spec_sem_rotas_static(self, client):
        """GET /api/spec → nenhuma rota começa com /static."""
        c, ctx = client
        _limpar_sessao(c)
        resp = c.get("/api/spec")
        data = json.loads(resp.data)
        for route in data["routes"]:
            assert not route["path"].startswith("/static")

    def test_api_spec_campos_rota(self, client):
        """GET /api/spec → cada rota tem path, methods, endpoint, auth."""
        c, ctx = client
        _limpar_sessao(c)
        resp = c.get("/api/spec")
        data = json.loads(resp.data)
        for route in data["routes"]:
            assert "path" in route
            assert "methods" in route
            assert "endpoint" in route
            assert "auth" in route


# ══════════════════════════════════════════════════════════════
#  blueprints/pwa.py — /healthz com DB exception
# ══════════════════════════════════════════════════════════════

class TestHealthzExcecao:

    def test_healthz_db_exception_retorna_200_db_ok_false(self, client):
        """/healthz com DB exception → 200, db=False, db_msg preenchido."""
        c, ctx = client
        _limpar_sessao(c)

        import database as db_module
        with patch.object(db_module, "_read", side_effect=Exception("DB locked")):
            resp = c.get("/healthz")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"
        assert data["db"] is False
        assert data["db_msg"] is not None
        assert "DB locked" in data["db_msg"]


# ══════════════════════════════════════════════════════════════
#  db/barbeiros.py — criar_barbeiro, criar_chefe
# ══════════════════════════════════════════════════════════════

class TestCriarBarbeiros:

    def test_criar_barbeiro_existe_na_db(self, ctx):
        """criar_barbeiro insere barbeiro com role='barbeiro'."""
        db = ctx["db"]
        db.criar_barbeiro("Barbeiro Novo X3", ctx["bid"])
        with db._read() as c:
            row = c.execute(
                "SELECT * FROM barbeiros WHERE nome=? AND barbearia_id=?",
                ("Barbeiro Novo X3", ctx["bid"])).fetchone()
        assert row is not None
        assert row["role"] == "barbeiro"
        assert row["mesa_token"] is not None

    def test_criar_chefe_retorna_true(self, ctx):
        """criar_chefe com username único → retorna True."""
        db = ctx["db"]
        result = db.criar_chefe("Chefe Novo X3", "chefe_novo_x3", "senha999", ctx["bid"])
        assert result is True
        c_obj = db.get_barbeiro_por_username("chefe_novo_x3")
        assert c_obj is not None
        assert c_obj["role"] == "chefe"

    def test_criar_chefe_username_duplicado_retorna_false(self, ctx):
        """criar_chefe com username já existente → retorna False."""
        db = ctx["db"]
        db.criar_chefe("Chefe Dup X3", "chefe_dup_x3", "senha1", ctx["bid"])
        result = db.criar_chefe("Chefe Dup X3 bis", "chefe_dup_x3", "senha2", ctx["bid"])
        assert result is False


# ══════════════════════════════════════════════════════════════
#  db/barbeiros.py — credenciais WebAuthn e ausências
# ══════════════════════════════════════════════════════════════

class TestCredenciaisWebAuthn:

    def test_registar_e_listar_credencial(self, ctx):
        """registar_credencial + get_credenciais_barbeiro → lista com 1 entrada."""
        db  = ctx["db"]
        bid_ = ctx["barb_id"]
        db.registar_credencial(bid_, "cred_id_test123", "pubkey_abc", "Dispositivo Teste")
        creds = db.get_credenciais_barbeiro(bid_)
        encontrada = [c for c in creds if c["credential_id"] == "cred_id_test123"]
        assert len(encontrada) == 1
        assert encontrada[0]["nome_dispositivo"] == "Dispositivo Teste"

    def test_get_credencial_por_id(self, ctx):
        """get_credencial_por_id devolve credencial registada."""
        db = ctx["db"]
        db.registar_credencial(ctx["barb_id"], "cred_por_id_x3", "pk_xyz", "D2")
        cred = db.get_credencial_por_id("cred_por_id_x3")
        assert cred is not None
        assert cred["public_key"] == "pk_xyz"

    def test_get_credencial_por_id_inexistente(self, ctx):
        """get_credencial_por_id com id inexistente → None."""
        db   = ctx["db"]
        cred = db.get_credencial_por_id("inexistente_999")
        assert cred is None

    def test_atualizar_sign_count(self, ctx):
        """atualizar_sign_count actualiza o campo sign_count."""
        db = ctx["db"]
        db.registar_credencial(ctx["barb_id"], "cred_sign_x3", "pk_sign", "D3")
        cred = db.get_credencial_por_id("cred_sign_x3")
        db.atualizar_sign_count(cred["id"], 42)
        cred_atualizada = db.get_credencial_por_id("cred_sign_x3")
        assert cred_atualizada["sign_count"] == 42

    def test_apagar_credencial(self, ctx):
        """apagar_credencial remove a credencial da DB."""
        db = ctx["db"]
        db.registar_credencial(ctx["barb_id"], "cred_apagar_x3", "pk_del", "D4")
        cred = db.get_credencial_por_id("cred_apagar_x3")
        db.apagar_credencial(cred["id"], ctx["barb_id"])
        assert db.get_credencial_por_id("cred_apagar_x3") is None

    def test_alterar_senha(self, ctx):
        """alterar_senha actualiza password_hash do barbeiro."""
        from werkzeug.security import check_password_hash
        db = ctx["db"]
        db.alterar_senha(ctx["barb_id"], "nova_senha_x3")
        barb = db.get_barbeiro(ctx["barb_id"])
        assert check_password_hash(barb["password_hash"], "nova_senha_x3")

    def test_set_credenciais_ok(self, ctx):
        """set_credenciais com username único → retorna True e actualiza DB."""
        db = ctx["db"]
        db.criar_barbeiro("Barb Cred X3", ctx["bid"])
        with db._read() as c:
            barb_id = c.execute(
                "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
                ("Barb Cred X3", ctx["bid"])).fetchone()["id"]
        result = db.set_credenciais(barb_id, "barb_cred_x3", "senha_cred")
        assert result is True
        barb = db.get_barbeiro_por_username("barb_cred_x3")
        assert barb is not None

    def test_set_credenciais_username_duplicado_retorna_false(self, ctx):
        """set_credenciais com username já existente → retorna False."""
        db = ctx["db"]
        result = db.set_credenciais(ctx["barb_id"], "chefe_x3", "qualquer")
        assert result is False


# ══════════════════════════════════════════════════════════════
#  helpers_booking.py — _val_data
# ══════════════════════════════════════════════════════════════

class TestValData:

    def test_val_data_vazia_retorna_false(self):
        """_val_data com string vazia → False."""
        from helpers_booking import _val_data
        assert _val_data("") is False

    def test_val_data_none_retorna_false(self):
        """_val_data com None → False."""
        from helpers_booking import _val_data
        assert _val_data(None) is False

    def test_val_data_formato_invalido_retorna_false(self):
        """_val_data com formato dd-mm-yyyy → False."""
        from helpers_booking import _val_data
        assert _val_data("25-06-2025") is False

    def test_val_data_mes_invalido_retorna_false(self):
        """_val_data com mês 13 → False."""
        from helpers_booking import _val_data
        assert _val_data("2025-13-01") is False

    def test_val_data_valida_retorna_true(self):
        """_val_data com data válida → True."""
        from helpers_booking import _val_data
        assert _val_data("2025-06-15") is True

    def test_val_data_dia_zero_retorna_false(self):
        """_val_data com dia 00 → False."""
        from helpers_booking import _val_data
        assert _val_data("2025-06-00") is False


# ══════════════════════════════════════════════════════════════
#  helpers_booking.py — _dentro_horario
# ══════════════════════════════════════════════════════════════

class TestDentroHorario:

    def _setup_horario(self, db, bid, hora_abertura="08:00", hora_fecho="19:00"):
        for dia in range(6):
            db.set_horario_dia(dia, hora_abertura, hora_fecho, 0, bid)
        db.set_horario_dia(6, hora_abertura, hora_fecho, 1, bid)

    def test_data_invalida_retorna_false(self):
        """_dentro_horario com data não parseable → (False, 'Data inválida.')."""
        from helpers_booking import _dentro_horario
        ok, msg = _dentro_horario("data-invalida", "10:00", 30, 1)
        assert ok is False
        assert msg is not None
        assert "inválida" in msg.lower() or "Data" in msg

    def test_dia_fechado_retorna_false(self, ctx):
        """_dentro_horario em dia marcado como fechado → (False, mensagem)."""
        from helpers_booking import _dentro_horario
        db = ctx["db"]
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=True):
                ok, msg = _dentro_horario(ctx["amanha"], "10:00", 30, ctx["bid"])
        assert ok is False
        assert msg is not None

    def test_horario_dia_fechado_retorna_false(self, ctx):
        """_dentro_horario quando get_horario_dia retorna fechado=True → (False, mensagem)."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": True, "hora_abertura": "08:00", "hora_fecho": "19:00"}):
                ok, msg = _dentro_horario(ctx["amanha"], "10:00", 30, ctx["bid"])
        assert ok is False

    def test_hora_invalida_retorna_false(self, ctx):
        """_dentro_horario com hora_abertura inválida no horário → (False, 'Hora inválida.')."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": False, "hora_abertura": "invalida", "hora_fecho": "19:00"}):
                ok, msg = _dentro_horario(ctx["amanha"], "10:00", 30, ctx["bid"])
        assert ok is False
        assert "inválida" in (msg or "").lower() or "Hora" in (msg or "")

    def test_slot_antes_abertura_retorna_false(self, ctx):
        """_dentro_horario com slot antes de abertura → (False, mensagem de abertura)."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": False, "hora_abertura": "10:00", "hora_fecho": "19:00"}):
                ok, msg = _dentro_horario(ctx["amanha"], "07:00", 30, ctx["bid"])
        assert ok is False
        assert "10:00" in (msg or "")

    def test_slot_apos_fecho_retorna_false(self, ctx):
        """_dentro_horario com slot >= fecho → (False, mensagem de fecho)."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": False, "hora_abertura": "08:00", "hora_fecho": "19:00"}):
                ok, msg = _dentro_horario(ctx["amanha"], "19:00", 30, ctx["bid"])
        assert ok is False
        assert "19:00" in (msg or "")

    def test_slot_fim_ultrapassa_fecho_retorna_false(self, ctx):
        """_dentro_horario: slot_ini OK mas slot_fim > fecho → (False, mensagem ultrapassa)."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": False, "hora_abertura": "08:00", "hora_fecho": "19:00"}):
                # slot_ini=18:45, duracao=30 → slot_fim=19:15 > fecho 19:00
                ok, msg = _dentro_horario(ctx["amanha"], "18:45", 30, ctx["bid"])
        assert ok is False
        assert "19:00" in (msg or "")

    def test_slot_valido_retorna_true(self, ctx):
        """_dentro_horario com slot dentro do horário → (True, None)."""
        from helpers_booking import _dentro_horario
        import app as app_module
        with app_module.app.test_request_context():
            with patch.object(__import__("database"), "dia_esta_fechado", return_value=False), \
                 patch.object(__import__("database"), "get_horario_dia",
                              return_value={"fechado": False, "hora_abertura": "08:00", "hora_fecho": "19:00"}):
                ok, msg = _dentro_horario(ctx["amanha"], "10:00", 30, ctx["bid"])
        assert ok is True
        assert msg is None
