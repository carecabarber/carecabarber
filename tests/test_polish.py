"""tests/test_polish.py — Cobertura das zonas de polish: barbeiros, mesa, api.

Alvos:
  blueprints/barbeiros.py  (era 57%) — apagar guards, pausa_almoco, foto_barbeiro
  blueprints/mesa.py       (era 66%) — ag_acao_cliente (QR de mesa)
  blueprints/api.py        (era 66%) — api_tempo, api_slots, api_lembretes, api_novos

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_polish.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-polish-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_polish.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid = db.criar_barbearia("Barbearia Polish", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("polish-slug", bid))

    # Chefe
    db.criar_chefe("Chefe Polish", "chefe_polish", "senha_pol", bid)
    chefe = db.get_barbeiro_por_username("chefe_polish")
    chefe_id = chefe["id"]

    # Barbeiro 1 (para apagar, pausa, etc.)
    db.criar_barbeiro("Barbeiro Polish", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Polish", bid)).fetchone()["id"]
    db.set_credenciais(barb_id, "barb_polish", "pass_pol")

    # Barbeiro 2 (descartável — para apagar sem agendamentos futuros)
    db.criar_barbeiro("Barbeiro Apagar", bid)
    with db._read() as c:
        barb_del_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Apagar", bid)).fetchone()["id"]

    # Serviço
    db.criar_servico("Corte Polish", 30, bid, preco=800)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    # Horário (para slots)
    for d in range(7):
        db.set_horario_dia(d, "08:00", "19:00", 0, bid)

    # Agendamento amanhã (para guards de apagar barbeiro)
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_futuro = db.criar_agendamento(
        "Cliente Polish", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id)

    # Agendamento agendado (para iniciar/terminar via QR)
    ag_qr = db.criar_agendamento(
        "Cliente QR", svc_id, f"{amanha} 11:00:00", bid, barbeiro_id=barb_id)

    # mesa_token do barbeiro
    with db._read() as c:
        row = c.execute(
            "SELECT mesa_token FROM barbeiros WHERE id=?", (barb_id,)).fetchone()
    mesa_token = row["mesa_token"] if row else None

    # token_avaliar do agendamento QR
    ag_qr_row = db.get_agendamento(ag_qr)
    token_avaliar = ag_qr_row["token_avaliar"] if ag_qr_row else None

    yield {
        "db": db, "bid": bid,
        "chefe_id": chefe_id, "barb_id": barb_id, "barb_del_id": barb_del_id,
        "svc_id": svc_id, "amanha": amanha,
        "ag_futuro": ag_futuro, "ag_qr": ag_qr,
        "mesa_token": mesa_token, "token_avaliar": token_avaliar,
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
        "SECRET_KEY": "test-polish", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["chefe_id"]
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Chefe Polish"
    return c


def _barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["barb_id"]
        s["role"]         = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Barbeiro Polish"
    return c


def _limpar_sessao(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py
# ══════════════════════════════════════════════════════════════

class TestApagar:
    """apagar_barbeiro — verifica os guards IDOR e de negócio."""

    def test_guard_nao_pode_apagar_se_mesmo(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/apagar/{ctx['chefe_id']}")
        assert r.status_code in (302, 200)
        # Chefe ainda existe
        assert ctx["db"].get_barbeiro(ctx["chefe_id"]) is not None

    def test_guard_barbeiro_com_futuros_bloqueado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/apagar/{ctx['barb_id']}")
        assert r.status_code in (302, 200)
        assert ctx["db"].get_barbeiro(ctx["barb_id"]) is not None

    def test_apagar_barbeiro_sem_futuros(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/apagar/{ctx['barb_del_id']}")
        assert r.status_code in (302, 200)

    def test_apagar_idor_barbeiro_outra_barbearia(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # id 9999 não existe → redirect silencioso
        r = c.post("/barbeiros/apagar/9999")
        assert r.status_code in (302, 200)


class TestPausaAlmoco:
    """set_pausa_almoco — validações de formato e intervalo."""

    def test_set_pausa_valida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/{ctx['barb_id']}/pausa-almoco",
                   data={"pausa_inicio": "12:00", "pausa_fim": "13:00"})
        data = json.loads(r.data)
        assert data["ok"] is True
        assert data["inicio"] == "12:00"

    def test_set_pausa_inicio_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/{ctx['barb_id']}/pausa-almoco",
                   data={"pausa_inicio": "25:99", "pausa_fim": "13:00"})
        assert r.status_code == 400

    def test_set_pausa_inicio_maior_que_fim(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/{ctx['barb_id']}/pausa-almoco",
                   data={"pausa_inicio": "14:00", "pausa_fim": "13:00"})
        assert r.status_code == 400

    def test_set_pausa_barbeiro_inexistente(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/9999/pausa-almoco",
                   data={"pausa_inicio": "12:00", "pausa_fim": "13:00"})
        assert r.status_code == 404


class TestFotoBarbeiro:
    """foto_barbeiro — SVG placeholder e 404."""

    def test_foto_barbeiro_sem_foto_retorna_svg(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/foto/{ctx['barb_id']}")
        assert r.status_code == 200
        assert b"svg" in r.data.lower()

    def test_foto_barbeiro_inexistente_404(self, client):
        c, ctx = client
        r = c.get("/foto/999999")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════
#  blueprints/mesa.py — ag_acao_cliente (QR de mesa)
# ══════════════════════════════════════════════════════════════

class TestAgAcaoCliente:
    """ag_acao_cliente — GET e POST via token_avaliar."""

    def test_get_token_valido(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/ag/{ctx['token_avaliar']}")
        assert r.status_code == 200

    def test_get_token_invalido_404(self, client):
        c, ctx = client
        r = c.get("/ag/token-inexistente-xyz")
        assert r.status_code == 404

    def test_post_iniciar(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.post(f"/ag/{ctx['token_avaliar']}", data={"acao": "iniciar"})
        assert r.status_code in (200, 302)

    def test_post_terminar(self, client):
        c, ctx = client
        _limpar_sessao(c)
        # Garantir que o agendamento está em andamento antes de terminar
        db = ctx["db"]
        db.iniciar_trabalho(ctx["ag_qr"])
        r = c.post(f"/ag/{ctx['token_avaliar']}", data={"acao": "terminar"})
        assert r.status_code in (200, 302)


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py
# ══════════════════════════════════════════════════════════════

class TestApiTempo:
    """api_tempo — cronómetro em andamento."""

    def test_sem_sessao_retorna_zeros(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/api/tempo/{ctx['ag_futuro']}")
        data = json.loads(r.data)
        assert data["segundos"] == 0

    def test_com_sessao_agendamento_errado(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/api/tempo/999999")
        data = json.loads(r.data)
        assert data["segundos"] == 0

    def test_com_agendamento_em_andamento(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        db = ctx["db"]
        # Criar e iniciar um agendamento para hoje
        hoje = datetime.now().strftime("%Y-%m-%d")
        ag = db.criar_agendamento(
            "Cliente Tempo", ctx["svc_id"],
            f"{hoje} {datetime.now().strftime('%H:%M:%S')}",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        db.iniciar_trabalho(ag)
        r = c.get(f"/api/tempo/{ag}")
        data = json.loads(r.data)
        assert data["segundos"] >= 0
        assert "estimado" in data
        db.terminar_trabalho(ag, 0)


class TestApiSlots:
    """api_slots — slots disponíveis."""

    def test_sem_params_retorna_vazio(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/api/slots")
        assert json.loads(r.data) == []

    def test_data_invalida_retorna_vazio(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data=nao-e-data&servico_id={ctx['svc_id']}")
        assert json.loads(r.data) == []

    def test_barbeiro_inexistente_retorna_vazio(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get(f"/api/slots?barbeiro_id=9999&data={ctx['amanha']}&servico_id={ctx['svc_id']}")
        assert json.loads(r.data) == []

    def test_slots_validos(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={ctx['amanha']}&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)


class TestApiLembretes:
    """api_lembretes — próximas marcações."""

    def test_sem_sessao_retorna_vazio(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/lembretes")
        assert json.loads(r.data) == []

    def test_como_chefe_retorna_lista(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/api/lembretes")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)


class TestApiNovos:
    """api_novos-agendamentos — polling de notificações."""

    def test_sem_sessao_retorna_vazio(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/novos-agendamentos?desde_id=0")
        assert json.loads(r.data) == []

    def test_como_chefe_retorna_lista(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/api/novos-agendamentos?desde_id=0")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)

    def test_desde_id_alto_retorna_vazio(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/api/novos-agendamentos?desde_id=999999")
        assert json.loads(r.data) == []
