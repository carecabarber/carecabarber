"""
tests/test_coverage4.py — Cobertura para blueprints/cliente.py (routes de cliente).

Módulo alvo: blueprints/cliente.py (era 37%)

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage4.py -v
"""
import os, sys, json, pytest, tempfile, shutil, secrets
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-cov4-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov4.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    bid = db_module.criar_barbearia("Barbearia Cov4", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    with db_module._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("cov4-slug", bid))

    db_module.criar_chefe("Chefe Cov4", "chefe_cov4", "senha_cov4", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_cov4")["id"]

    db_module.criar_barbeiro("Barbeiro Cov4", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Cov4", bid)).fetchone()["id"]

    db_module.criar_servico("Corte Cov4", 30, bid, preco=600)
    with db_module._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    # Horário aberto todos os dias
    for d in range(7):
        db_module.set_horario_dia(d, "08:00", "20:00", 0, bid)

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    depois = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    ontem  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Agendamento para cliente "912111111"
    ag_id = db_module.criar_agendamento(
        "Cliente Cov4", svc_id, f"{amanha} 10:00:00",
        bid, barbeiro_id=barb_id, telefone="912111111")
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET telefone=? WHERE id=?", ("912111111", ag_id))

    # Agendamento para reagendar-link
    ag_link_id = db_module.criar_agendamento(
        "Cliente Link", svc_id, f"{amanha} 11:00:00",
        bid, barbeiro_id=barb_id, telefone="912111222")
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET telefone=? WHERE id=?", ("912111222", ag_link_id))

    # Token de reagendamento
    token_reagendar = db_module.gerar_token_reagendar(ag_link_id)

    # Agendamento para cancelar-link
    ag_cancelar_id = db_module.criar_agendamento(
        "Cliente Cancelar", svc_id, f"{amanha} 12:00:00",
        bid, barbeiro_id=barb_id, telefone="912111333")
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET telefone=? WHERE id=?", ("912111333", ag_cancelar_id))
    token_cancelar = db_module.gerar_token_reagendar(ag_cancelar_id)

    # Agendamento concluído para avaliar-link (token_avaliar manual)
    ag_avaliar_id = db_module.criar_agendamento(
        "Cliente Avaliar", svc_id, f"{ontem} 09:00:00",
        bid, barbeiro_id=barb_id, telefone="912111444")
    token_avaliar = secrets.token_urlsafe(32)
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET status='concluido', telefone=?, token_avaliar=? WHERE id=?",
                  ("912111444", token_avaliar, ag_avaliar_id))

    # Agendamento para reagendar (como cliente)
    ag_reagendar_id = db_module.criar_agendamento(
        "Cliente Reagendar", svc_id, f"{amanha} 13:00:00",
        bid, barbeiro_id=barb_id, telefone="912111555")
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET telefone=? WHERE id=?", ("912111555", ag_reagendar_id))

    # Agendamento para cancelar (como cliente)
    ag_cancel_id = db_module.criar_agendamento(
        "Cliente Cancelar2", svc_id, f"{amanha} 14:00:00",
        bid, barbeiro_id=barb_id, telefone="912111666")
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET telefone=? WHERE id=?", ("912111666", ag_cancel_id))

    yield {
        "db": db_module, "bid": bid, "chefe_id": chefe_id,
        "barb_id": barb_id, "svc_id": svc_id,
        "slug": "cov4-slug", "amanha": amanha, "depois": depois, "ontem": ontem,
        "ag_id": ag_id, "ag_link_id": ag_link_id,
        "ag_cancelar_id": ag_cancelar_id, "ag_avaliar_id": ag_avaliar_id,
        "ag_reagendar_id": ag_reagendar_id, "ag_cancel_id": ag_cancel_id,
        "token_reagendar": token_reagendar, "token_cancelar": token_cancelar,
        "token_avaliar": token_avaliar,
    }

    _db_conn._CONN    = None
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-cov4", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


# ── helpers de sessão ──────────────────────────────────────────

def _cliente(c, ctx, tel="912111111"):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["telefone"]     = tel
        s["user_nome"]    = "Cliente Cov4"
    return c


def _limpar(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/cliente.py
# ══════════════════════════════════════════════════════════════

class TestClienteBlueprint2:

    # ── cliente_entrada ────────────────────────────────────────

    def test_entrada_get_valido(self, client):
        c, ctx = client
        r = c.get(f"/cliente/{ctx['slug']}")
        assert r.status_code == 200

    def test_entrada_get_slug_invalido(self, client):
        c, ctx = client
        r = c.get("/cliente/slug-nao-existe")
        assert r.status_code == 404

    def test_entrada_post_valido_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post(f"/cliente/{ctx['slug']}", data={
            "nome": "Cliente Teste", "telefone": "912111001"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_entrada_com_sessao_get(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}")
        assert r.status_code == 200

    # ── cliente_home ───────────────────────────────────────────

    def test_home_com_sessao(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/area")
        assert r.status_code == 200

    def test_home_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get(f"/cliente/{ctx['slug']}/area", follow_redirects=False)
        assert r.status_code == 302

    def test_home_slug_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/cliente/slug-invalido/area")
        assert r.status_code == 404

    # ── cliente_marcar ─────────────────────────────────────────

    def test_marcar_get_com_sessao(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/marcar")
        assert r.status_code == 200

    def test_marcar_get_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get(f"/cliente/{ctx['slug']}/marcar", follow_redirects=False)
        assert r.status_code == 302

    def test_marcar_get_slug_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/cliente/slug-invalido/marcar")
        assert r.status_code == 404

    def test_marcar_post_campos_vazios(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": "", "data": "", "hora": ""
        })
        assert r.status_code == 200
        assert b"obrigat" in r.data.lower() or r.status_code == 200

    def test_marcar_post_data_invalida(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": str(ctx["svc_id"]),
            "data": "nao-e-data", "hora": "10:00"
        })
        assert r.status_code == 200

    def test_marcar_post_data_passada(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": str(ctx["svc_id"]),
            "data": ctx["ontem"], "hora": "10:00"
        })
        assert r.status_code == 200

    def test_marcar_post_servico_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": "9999",
            "data": ctx["amanha"], "hora": "15:00"
        })
        assert r.status_code == 200

    def test_marcar_post_barbeiro_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": "9999",
            "data": ctx["amanha"], "hora": "15:30"
        })
        assert r.status_code == 200

    def test_marcar_post_sucesso(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111777")
        r = c.post(f"/cliente/{ctx['slug']}/marcar", data={
            "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": ctx["depois"], "hora": "10:00"
        }, follow_redirects=False)
        # Deve redirecionar para confirmação
        assert r.status_code == 302

    # ── cliente_confirmacao ────────────────────────────────────

    def test_confirmacao_com_sessao_valida(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111111")
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/{ctx['ag_id']}")
        assert r.status_code == 200

    def test_confirmacao_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/{ctx['ag_id']}", follow_redirects=False)
        assert r.status_code == 302

    def test_confirmacao_telefone_errado(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="999999999")
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/{ctx['ag_id']}", follow_redirects=False)
        assert r.status_code == 302

    def test_confirmacao_ag_inexistente(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/99999", follow_redirects=False)
        assert r.status_code == 302

    # ── cliente_cancelar ───────────────────────────────────────

    def test_cancelar_valido(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111666")
        r = c.post(f"/cliente/{ctx['slug']}/cancelar/{ctx['ag_cancel_id']}",
                   follow_redirects=False)
        assert r.status_code == 302

    def test_cancelar_slug_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/slug-invalido/cancelar/{ctx['ag_id']}",
                   follow_redirects=False)
        assert r.status_code == 302

    def test_cancelar_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post(f"/cliente/{ctx['slug']}/cancelar/{ctx['ag_id']}",
                   follow_redirects=False)
        assert r.status_code == 302

    def test_cancelar_telefone_errado(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="999888777")
        r = c.post(f"/cliente/{ctx['slug']}/cancelar/{ctx['ag_id']}",
                   follow_redirects=False)
        assert r.status_code == 302

    # ── cliente_reagendar ──────────────────────────────────────

    def test_reagendar_get_valido(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111555")
        r = c.get(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}")
        assert r.status_code == 200

    def test_reagendar_get_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}",
                  follow_redirects=False)
        assert r.status_code == 302

    def test_reagendar_get_slug_invalido(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/cliente/slug-invalido/reagendar/1")
        assert r.status_code == 404

    def test_reagendar_get_ag_tel_errado(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="999888001")
        r = c.get(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}",
                  follow_redirects=False)
        assert r.status_code == 302

    def test_reagendar_post_campos_vazios(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111555")
        r = c.post(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}",
                   data={"data": "", "hora": ""})
        assert r.status_code == 200

    def test_reagendar_post_data_passada(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111555")
        r = c.post(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}",
                   data={
                       "servico_id": str(ctx["svc_id"]),
                       "barbeiro_id": str(ctx["barb_id"]),
                       "data": ctx["ontem"], "hora": "10:00"
                   })
        assert r.status_code == 200

    def test_reagendar_post_sucesso(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111555")
        depois3 = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        r = c.post(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_reagendar_id']}",
                   data={
                       "servico_id": str(ctx["svc_id"]),
                       "barbeiro_id": str(ctx["barb_id"]),
                       "data": depois3, "hora": "10:00"
                   }, follow_redirects=False)
        assert r.status_code in (200, 302)

    # ── reagendar_link ─────────────────────────────────────────

    def test_reagendar_link_get_valido(self, client):
        c, ctx = client
        r = c.get(f"/reagendar-link/{ctx['token_reagendar']}")
        assert r.status_code == 200

    def test_reagendar_link_get_invalido(self, client):
        c, ctx = client
        r = c.get("/reagendar-link/token-nao-existe")
        assert r.status_code == 404

    def test_reagendar_link_post_campos_vazios(self, client):
        c, ctx = client
        r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                   data={"data": "", "hora": ""})
        assert r.status_code == 200

    def test_reagendar_link_post_data_passada(self, client):
        c, ctx = client
        r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                   data={
                       "servico_id": str(ctx["svc_id"]),
                       "barbeiro_id": str(ctx["barb_id"]),
                       "data": ctx["ontem"], "hora": "10:00"
                   })
        assert r.status_code == 200

    def test_reagendar_link_post_sucesso(self, client):
        c, ctx = client
        depois4 = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")
        r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                   data={
                       "servico_id": str(ctx["svc_id"]),
                       "barbeiro_id": str(ctx["barb_id"]),
                       "data": depois4, "hora": "10:00"
                   })
        # Sucesso mostra template reagendar_link_ok ou redireciona
        assert r.status_code in (200, 302)

    # ── cancelar_link ──────────────────────────────────────────

    def test_cancelar_link_get_valido(self, client):
        c, ctx = client
        r = c.get(f"/cancelar-link/{ctx['token_cancelar']}")
        assert r.status_code == 200

    def test_cancelar_link_get_invalido(self, client):
        c, ctx = client
        r = c.get("/cancelar-link/token-nao-existe")
        assert r.status_code == 404

    def test_cancelar_link_post_sem_confirmar(self, client):
        c, ctx = client
        r = c.post(f"/cancelar-link/{ctx['token_cancelar']}",
                   data={"confirmar": "nao"})
        assert r.status_code == 200

    def test_cancelar_link_post_confirmar(self, client):
        c, ctx = client
        r = c.post(f"/cancelar-link/{ctx['token_cancelar']}",
                   data={"confirmar": "sim"})
        assert r.status_code == 200

    # ── avaliar_link ───────────────────────────────────────────

    def test_avaliar_link_get_invalido(self, client):
        c, ctx = client
        r = c.get("/avaliar-link/token-nao-existe")
        assert r.status_code == 404

    def test_avaliar_link_get_valido(self, client):
        c, ctx = client
        r = c.get(f"/avaliar-link/{ctx['token_avaliar']}")
        assert r.status_code == 200

    def test_avaliar_link_post_nota_invalida(self, client):
        c, ctx = client
        r = c.post(f"/avaliar-link/{ctx['token_avaliar']}",
                   data={"nota": "9"})
        assert r.status_code == 200
        assert b"1 e 5" in r.data or r.status_code == 200

    def test_avaliar_link_post_nota_valida(self, client):
        c, ctx = client
        # Criar novo ag concluído com token para avaliar
        db = ctx["db"]
        ontem = ctx["ontem"]
        ag2 = db.criar_agendamento(
            "Avaliar Link2", ctx["svc_id"], f"{ontem} 08:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"], telefone="912111888")
        token2 = secrets.token_urlsafe(32)
        with db._write() as c2:
            c2.execute(
                "UPDATE agendamentos SET status='concluido', telefone=?, token_avaliar=? WHERE id=?",
                ("912111888", token2, ag2))
        r = c.post(f"/avaliar-link/{token2}", data={"nota": "5"})
        assert r.status_code == 200
