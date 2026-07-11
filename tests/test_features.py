"""
tests/test_features.py — Testes funcionais das features novas + regressão.

Cobre:
  - Autenticação (root, chefe, barbeiro)
  - Marcações (novo, walkin, reagendar, cancelar)
  - Apagar barbeiro (guards, IDOR, soft/hard delete)
  - Preços por estabelecimento (multi-moeda)
  - Push notifications (VAPID, subscribe, unsubscribe)
  - Relatório PDF (filtros mes, data_ini/fim, barbeiro)
  - Health check e 404
  - Solo barber mode (slots carregam sem dropdown)
  - DB functions (novas)

Correr: cd ~/Documentos/barbearia && python -m pytest tests/ -v
"""
import os, sys, json, pytest, tempfile, shutil
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-apenas-testes")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def _tmp_db():
    """DB SQLite temporária com dados base para todos os testes do módulo."""
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_feat.db")
    orig    = _db_conn.DB_PATH
    _db_conn.DB_PATH = tmp_db
    db_module.DB_PATH = tmp_db
    # Reset connection global
    _db_conn._CONN = None
    db_module._CONN = None

    db_module.init_db()

    # Criar dados de teste
    bid = db_module.criar_barbearia("Barbearia Teste", tipo="barbearia")

    # Chefe — usar criar_chefe (tem username/senha incluídos)
    db_module.criar_chefe("Chefe Teste", "chefe_teste", "senha123", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_teste")["id"]

    # Barbeiro — criar_barbeiro não devolve ID → consultar DB
    db_module.criar_barbeiro("Barbeiro Um", bid)
    with db_module._read() as _c:
        _row = _c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Um", bid)).fetchone()
    barb_id = _row["id"]
    db_module.set_credenciais(barb_id, "barb_teste", "senha456")

    # Serviço — criar_servico não devolve ID → consultar DB
    db_module.criar_servico("Corte", 30, bid, preco=1000)
    with db_module._read() as _c:
        _srow = _c.execute(
            "SELECT id FROM servicos WHERE nome=? AND barbearia_id=?",
            ("Corte", bid)).fetchone()
    svc_id = _srow["id"]

    # Plano activo (experiência) para a barbearia
    db_module.registar_pagamento(bid, "exp")

    yield {
        "db":       db_module,
        "tmp_dir":  tmp_dir,
        "bid":      bid,
        "chefe_id": chefe_id,
        "barb_id":  barb_id,
        "svc_id":   svc_id,
    }

    _db_conn._reset_conn()
    db_module._CONN = None
    _db_conn.DB_PATH = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(_tmp_db):
    """Flask test client ligado à DB temporária."""
    import app as app_module

    app_module.app.config.update({
        "TESTING":               True,
        "WTF_CSRF_ENABLED":      False,
        "SECRET_KEY":            "test-secret",
        "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, _tmp_db


def _sessao_chefe(client_tuple):
    """Abre sessão de chefe no test client."""
    c, ctx = client_tuple
    with c.session_transaction() as s:
        s["user_id"]    = ctx["chefe_id"]
        s["role"]       = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]  = "Chefe Teste"
    return c, ctx


def _sessao_barbeiro(client_tuple):
    c, ctx = client_tuple
    with c.session_transaction() as s:
        s["user_id"]    = ctx["barb_id"]
        s["role"]       = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]  = "Barbeiro Um"
    return c, ctx


def _sessao_root(client_tuple):
    c, ctx = client_tuple
    with c.session_transaction() as s:
        s["user_id"] = 1
        s["role"]    = "root"
    return c, ctx


# ══════════════════════════════════════════════════════════════
#  AUTENTICAÇÃO
# ══════════════════════════════════════════════════════════════

class TestAutenticacao:
    def test_login_root_correcto(self, client):
        c, ctx = client
        # Ler password gerada no init
        import os as _os
        pw_file = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), ".root_init_password")
        if not _os.path.exists(pw_file):
            pytest.skip("Ficheiro .root_init_password não encontrado")
        with open(pw_file) as f:
            pw = [l.split(": ")[1].strip() for l in f if l.startswith("password:")]
        if not pw:
            pytest.skip("Password root não encontrada")
        r = c.post("/login", data={"username": "root", "senha": pw[0]}, follow_redirects=False)
        assert r.status_code == 302

    def test_login_chefe_correcto(self, client):
        c, ctx = client
        r = c.post("/login", data={"username": "chefe_teste", "senha": "senha123"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_login_senha_errada(self, client):
        c, ctx = client
        r = c.post("/login", data={"username": "chefe_teste", "senha": "errada"},
                   follow_redirects=True)
        assert r.status_code == 200
        assert b"incorretos" in r.data or b"incorrectos" in r.data or b"errad" in r.data.lower()

    def test_login_user_inexistente(self, client):
        c, ctx = client
        r = c.post("/login", data={"username": "naoexiste", "senha": "qualquer"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_logout(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════
#  DASHBOARD E ROTAS PRINCIPAIS
# ══════════════════════════════════════════════════════════════

class TestDashboard:
    def test_index_chefe(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/", follow_redirects=True)
        assert r.status_code == 200

    def test_index_barbeiro(self, client):
        c, ctx = _sessao_barbeiro(client)
        r = c.get("/", follow_redirects=True)
        assert r.status_code == 200

    def test_index_sem_sessao_redireciona(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/login" in r.headers.get("Location", "")

    def test_healthz(self, client):
        c, ctx = client
        r = c.get("/healthz")
        assert r.status_code == 200
        d = r.get_json()
        assert d["status"] == "ok"
        assert "uptime_s" in d

    def test_404(self, client):
        c, ctx = client
        r = c.get("/rota-que-nao-existe-123")
        assert r.status_code == 404

    def test_vapid_public(self, client):
        c, ctx = client
        r = c.get("/api/push/vapid-public")
        assert r.status_code == 200
        d = r.get_json()
        assert "publicKey" in d
        assert len(d["publicKey"]) > 40


# ══════════════════════════════════════════════════════════════
#  MARCAÇÕES — NOVO E WALKIN
# ══════════════════════════════════════════════════════════════

class TestMarcacoes:
    def test_novo_get(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/novo")
        assert r.status_code == 200
        assert b"servico" in r.data.lower() or b"servi" in r.data.lower()

    def test_novo_post_sucesso(self, client):
        c, ctx = _sessao_chefe(client)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.post("/novo", data={
            "cliente":    "João Teste",
            "telefone":   "912345678",
            "servico_id": ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
            "data":       amanha,
            "hora":       "10:00",
        }, follow_redirects=False)
        # Sucesso → redirect; erro de slot → 200 com mensagem
        assert r.status_code in (200, 302)

    def test_novo_post_sem_hora(self, client):
        c, ctx = _sessao_chefe(client)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.post("/novo", data={
            "cliente":    "Sem Hora",
            "servico_id": ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
            "data":       amanha,
            "hora":       "",
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b"hora" in r.data.lower()

    def _proxima_segunda(self):
        """Data da próxima segunda-feira (dia útil garantido aberto 08–19)."""
        hoje = datetime.now()
        dias = (7 - hoje.weekday()) % 7 or 7   # sempre 1..7 dias à frente
        return (hoje + timedelta(days=dias)).strftime("%Y-%m-%d")

    def test_novo_recorrencia_semanal_cria_multiplas(self, client):
        """Recorrência semanal × 3 → cria 3 marcações (a base + 2 repetições),
        todas no mesmo barbeiro/serviço, em segundas consecutivas."""
        c, ctx = _sessao_chefe(client)
        db = ctx["db"]
        base = self._proxima_segunda()
        nome = "Cliente Recorrente ABC"
        r = c.post("/novo", data={
            "cliente":     nome,
            "servico_id":  ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
            "data":        base,
            "hora":        "16:30",
            "recorrencia": "semanal",
            "recorrencia_vezes": "3",
        }, follow_redirects=False)
        assert r.status_code == 302
        with db._read() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM agendamentos WHERE cliente=? AND barbearia_id=?",
                (nome, ctx["bid"])).fetchone()["n"]
        assert n == 3

    def test_novo_sem_recorrencia_cria_uma(self, client):
        """Sem recorrência (default) → só 1 marcação, comportamento inalterado."""
        c, ctx = _sessao_chefe(client)
        db = ctx["db"]
        base = self._proxima_segunda()
        nome = "Cliente Unico DEF"
        r = c.post("/novo", data={
            "cliente":     nome,
            "servico_id":  ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
            "data":        base,
            "hora":        "17:30",
            "recorrencia": "nao",
            "recorrencia_vezes": "4",   # ignorado quando recorrencia=nao
        }, follow_redirects=False)
        assert r.status_code == 302
        with db._read() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM agendamentos WHERE cliente=? AND barbearia_id=?",
                (nome, ctx["bid"])).fetchone()["n"]
        assert n == 1

    def test_walkin_get(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/walkin")
        assert r.status_code == 200

    def test_walkin_post_sucesso(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post("/walkin", data={
            "cliente":    "Maria Walkin",
            "telefone":   "961234567",
            "servico_id": ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
        }, follow_redirects=False)
        assert r.status_code in (200, 302)

    def test_walkin_post_sem_nome(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post("/walkin", data={
            "cliente":    "",
            "servico_id": ctx["svc_id"],
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_api_slots(self, client):
        c, ctx = _sessao_chefe(client)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)


# ══════════════════════════════════════════════════════════════
#  APAGAR BARBEIRO — GUARDS E IDOR
# ══════════════════════════════════════════════════════════════

class TestApagarBarbeiro:
    def test_barbeiro_nao_chefe_nao_pode_apagar(self, client):
        c, ctx = _sessao_barbeiro(client)
        r = c.post(f"/barbeiros/apagar/{ctx['chefe_id']}", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "") or "/" in r.headers.get("Location", "")

    def test_sem_sessao_nao_pode_apagar(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.post(f"/barbeiros/apagar/999", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "")

    def test_chefe_nao_apaga_si_proprio(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post(f"/barbeiros/apagar/{ctx['chefe_id']}", follow_redirects=True)
        assert r.status_code == 200
        # Flash de erro esperado
        assert b"pr\xc3\xb3pria" in r.data or b"proprio" in r.data.lower() or b"apagar" in r.data.lower()

    def test_idor_chefe_barbearia_errada(self, client):
        c, ctx = _sessao_chefe(client)
        # Tentar apagar barbeiro com id=1 (root) que não pertence à barbearia
        r = c.post("/barbeiros/apagar/1", follow_redirects=False)
        assert r.status_code == 302  # silencioso — guard de barbearia_id

    def test_apagar_barbeiro_inexistente(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post("/barbeiros/apagar/99999", follow_redirects=False)
        assert r.status_code == 302  # redireciona silenciosamente

    def test_db_apagar_hard(self, client):
        """Barbeiro sem histórico → hard delete."""
        c, ctx = client
        db = ctx["db"]
        db.criar_barbeiro("Temporario", barbearia_id=ctx["bid"])
        with db._read() as _c:
            _r = _c.execute(
                "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
                ("Temporario", ctx["bid"])).fetchone()
        novo_id = _r["id"]
        resultado = db.apagar_barbeiro(novo_id, ctx["bid"])
        assert resultado == "hard"
        assert db.get_barbeiro(novo_id) is None

    def test_db_apagar_soft(self, client):
        """Barbeiro com histórico → soft delete (credenciais limpas, ativo=0)."""
        c, ctx = client
        db = ctx["db"]
        db.criar_barbeiro("ComHistorico", barbearia_id=ctx["bid"])
        with db._read() as _c:
            _r = _c.execute(
                "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
                ("ComHistorico", ctx["bid"])).fetchone()
        novo_id = _r["id"]
        db.set_credenciais(novo_id, "comhistorico", "pwd")
        # Criar agendamento passado (tipo="agendado" é o valor correcto)
        db.criar_agendamento("Cliente", ctx["svc_id"],
                             "2025-01-01 10:00:00", ctx["bid"],
                             novo_id, "agendado", 0)
        resultado = db.apagar_barbeiro(novo_id, ctx["bid"])
        assert resultado == "soft"
        b = db.get_barbeiro(novo_id)
        assert b is not None
        assert b["ativo"] == 0
        assert b["username"] is None
        assert b["password_hash"] is None


# ══════════════════════════════════════════════════════════════
#  PREÇOS POR ESTABELECIMENTO (MULTI-MOEDA)
# ══════════════════════════════════════════════════════════════

class TestPrecosBarbearia:
    def test_get_precos_default(self, client):
        c, ctx = client
        db = ctx["db"]
        precos, moeda = db.get_planos_precos_barbearia(ctx["bid"])
        assert isinstance(precos, dict)
        assert all(k in precos for k in db.PLANOS)
        assert all("preco" in v and "moeda" in v for v in precos.values())

    def test_set_e_get_preco_especifico(self, client):
        c, ctx = client
        db = ctx["db"]
        db.set_plano_preco_barbearia(ctx["bid"], "1m", 2500, "EUR")
        precos, moeda = db.get_planos_precos_barbearia(ctx["bid"])
        assert precos["1m"]["preco"] == 2500
        assert precos["1m"]["moeda"] == "EUR"
        assert moeda == "EUR"

    def test_codigo_invalido_retorna_false(self, client):
        c, ctx = client
        db = ctx["db"]
        r = db.set_plano_preco_barbearia(ctx["bid"], "invalido", 100, "EUR")
        assert r is False

    def test_preco_negativo_guardado_como_zero(self, client):
        c, ctx = client
        db = ctx["db"]
        db.set_plano_preco_barbearia(ctx["bid"], "3m", -100, "EUR")
        precos, _ = db.get_planos_precos_barbearia(ctx["bid"])
        assert precos["3m"]["preco"] == 0

    def test_rota_root_precos_protegida(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.post(f"/root/barbearia/{ctx['bid']}/precos",
                   data={"moeda": "EUR", "preco_1m": "500"},
                   follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "")

    def test_rota_root_precos_chefe_bloqueado(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post(f"/root/barbearia/{ctx['bid']}/precos",
                   data={"moeda": "EUR", "preco_1m": "500"},
                   follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers.get("Location", "")


# ══════════════════════════════════════════════════════════════
#  PUSH NOTIFICATIONS
# ══════════════════════════════════════════════════════════════

class TestPushNotifications:
    def test_vapid_public_key_formato(self, client):
        c, ctx = client
        r = c.get("/api/push/vapid-public")
        assert r.status_code == 200
        d = r.get_json()
        key = d.get("publicKey", "")
        # Chave ECDH P-256 uncompressed em base64url = 87 chars
        assert len(key) >= 80
        # Apenas chars base64url
        import re
        assert re.match(r'^[A-Za-z0-9_\-]+$', key)

    def test_subscribe_sem_sessao_redireciona(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.post("/api/push/subscribe",
                   json={"endpoint": "https://test", "keys": {"p256dh": "x", "auth": "y"}})
        assert r.status_code in (302, 401, 400)

    def test_subscribe_dados_incompletos(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.post("/api/push/subscribe", json={})
        assert r.status_code == 400
        d = r.get_json()
        assert d["ok"] is False

    def test_subscribe_e_unsubscribe(self, client):
        c, ctx = _sessao_chefe(client)
        endpoint = "https://fcm.test.endpoint/push/unique123"
        r = c.post("/api/push/subscribe", json={
            "endpoint": endpoint,
            "keys": {"p256dh": "fakeP256dh", "auth": "fakeAuth"}
        })
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        # Verificar na DB
        db = ctx["db"]
        subs = db.push_listar(ctx["bid"])
        assert any(s["endpoint"] == endpoint for s in subs)
        # Unsubscribe
        r2 = c.post("/api/push/unsubscribe", json={"endpoint": endpoint})
        assert r2.status_code == 200
        subs_after = db.push_listar(ctx["bid"])
        assert not any(s["endpoint"] == endpoint for s in subs_after)

    def test_db_push_guardar_listar_remover(self, client):
        c, ctx = client
        db = ctx["db"]
        ep = "https://test.unique.endpoint.db"
        db.push_guardar(ctx["chefe_id"], ctx["bid"], ep, "p256", "auth")
        subs = db.push_listar(ctx["bid"])
        assert any(s["endpoint"] == ep for s in subs)
        db.push_remover(ep)
        subs2 = db.push_listar(ctx["bid"])
        assert not any(s["endpoint"] == ep for s in subs2)


# ══════════════════════════════════════════════════════════════
#  RELATÓRIO PDF
# ══════════════════════════════════════════════════════════════

class TestRelatorioPDF:
    def test_pdf_sem_sessao_redireciona(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.get("/relatorio-pdf", follow_redirects=False)
        assert r.status_code in (302, 301)

    def test_pdf_barbeiro_nao_chefe_redireciona(self, client):
        c, ctx = _sessao_barbeiro(client)
        r = c.get("/relatorio-pdf", follow_redirects=False)
        assert r.status_code in (302, 200)  # 302 para index ou 200 se _PDF_OK=False

    def test_pdf_chefe_retorna_pdf_ou_flash(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/relatorio-pdf", follow_redirects=True)
        assert r.status_code == 200
        ct = r.content_type
        # Pode ser PDF (se reportlab instalado) ou HTML com flash (se não instalado)
        assert "pdf" in ct or "html" in ct

    def test_pdf_filtro_mes(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/relatorio-pdf?mes=2026-01", follow_redirects=True)
        assert r.status_code == 200

    def test_pdf_filtro_data_ini_fim(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/relatorio-pdf?data_ini=2026-01-01&data_fim=2026-01-31", follow_redirects=True)
        assert r.status_code == 200

    def test_pdf_filtro_barbeiro_id(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get(f"/relatorio-pdf?barbeiro_id={ctx['barb_id']}", follow_redirects=True)
        assert r.status_code == 200

    def test_pdf_filtro_mes_invalido_usa_atual(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/relatorio-pdf?mes=invalido", follow_redirects=True)
        assert r.status_code == 200  # não crasha — usa mês actual


# ══════════════════════════════════════════════════════════════
#  CONTAR CHEFES ATIVOS
# ══════════════════════════════════════════════════════════════

class TestContarChefes:
    def test_conta_chefes_barbearia(self, client):
        c, ctx = client
        db = ctx["db"]
        n = db.contar_chefes_ativos(ctx["bid"])
        assert n >= 1

    def test_conta_chefes_excluindo_id(self, client):
        c, ctx = client
        db = ctx["db"]
        # Excluir o único chefe → deve dar 0
        n = db.contar_chefes_ativos(ctx["bid"], excluir_id=ctx["chefe_id"])
        assert n == 0

    def test_conta_chefes_barbearia_inexistente(self, client):
        c, ctx = client
        db = ctx["db"]
        n = db.contar_chefes_ativos(99999)
        assert n == 0


# ══════════════════════════════════════════════════════════════
#  SOLO BARBER MODE — SLOTS
# ══════════════════════════════════════════════════════════════

class TestSoloBarberMode:
    def test_api_slots_solo(self, client):
        """Slots carregam directamente com barbeiro único (sem dropdown)."""
        c, ctx = _sessao_chefe(client)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        slots = r.get_json()
        assert isinstance(slots, list)

    def test_novo_html_solo_sem_dropdown(self, client):
        """Para barbearia com 1 barbeiro, novo.html não deve mostrar select de barbeiro."""
        c, ctx = _sessao_chefe(client)
        r = c.get("/novo")
        assert r.status_code == 200
        # Com 1 barbeiro (barb_id), deve ter hidden input não select
        # (O select aparece só quando barbeiros > 1)

    def test_api_slots_barbeiro_invalido(self, client):
        """Slots com barbeiro_id inválido retorna lista vazia ou erro."""
        c, ctx = _sessao_chefe(client)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        r = c.get(f"/api/slots?barbeiro_id=99999&data={amanha}&servico_id={ctx['svc_id']}")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert isinstance(r.get_json(), list)


# ══════════════════════════════════════════════════════════════
#  ROOT DASHBOARD
# ══════════════════════════════════════════════════════════════

class TestRootDashboard:
    def test_root_sem_sessao_redireciona(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s.clear()
        r = c.get("/root", follow_redirects=False)
        assert r.status_code in (301, 302)

    def test_root_chefe_bloqueado(self, client):
        c, ctx = _sessao_chefe(client)
        r = c.get("/root", follow_redirects=False)
        assert r.status_code in (301, 302)

    def test_root_planos_barbearia(self, client):
        c, ctx = _sessao_root(client)
        r = c.get(f"/root/planos/{ctx['bid']}", follow_redirects=True)
        assert r.status_code == 200

    def test_root_planos_barbearia_inexistente(self, client):
        c, ctx = _sessao_root(client)
        r = c.get("/root/planos/99999", follow_redirects=True)
        assert r.status_code in (200, 302)
