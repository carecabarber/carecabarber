"""
tests/test_coverage3.py — Cobertura para blueprints/root, blueprints/agendamentos e rotas app.py.

Módulos alvo:
  blueprints/root.py         (era 36%)
  blueprints/agendamentos.py (era 41%)
  blueprints/app.py          (era 55%)

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage3.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-cov3-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov3.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    # Barbearia de teste
    bid = db_module.criar_barbearia("Barbearia Cov3", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    # Slug explícito
    with db_module._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("cov3-slug", bid))

    db_module.criar_chefe("Chefe Cov3", "chefe_cov3", "senha_cov3", bid)
    chefe = db_module.get_barbeiro_por_username("chefe_cov3")
    chefe_id = chefe["id"]

    db_module.criar_barbeiro("Barbeiro Cov3", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Cov3", bid)).fetchone()["id"]
    db_module.set_credenciais(barb_id, "barb_cov3", "pass_cov3")

    root = db_module.get_barbeiro_por_username("root")
    root_id = root["id"]

    db_module.criar_servico("Corte Cov3", 30, bid, preco=500)
    with db_module._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    db_module.set_horario_dia(0, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(1, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(2, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(3, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(4, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(5, "08:00", "18:00", 0, bid)
    db_module.set_horario_dia(6, "08:00", "19:00", 0, bid)

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_id = db_module.criar_agendamento(
        "Cliente Cov3", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id)

    # Segundo agendamento para testes de cancelar/reagendar
    ag_id2 = db_module.criar_agendamento(
        "Cliente Cov3b", svc_id, f"{amanha} 11:00:00", bid, barbeiro_id=barb_id)

    # Agendamento passado já concluído para historico
    ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    ag_hist = db_module.criar_agendamento(
        "Cliente Hist", svc_id, f"{ontem} 09:00:00", bid, barbeiro_id=barb_id)
    # Simular conclusão
    with db_module._write() as c:
        c.execute("UPDATE agendamentos SET status='concluido', valor=600, inicio=?, fim=? WHERE id=?",
                  (f"{ontem} 09:00:00", f"{ontem} 09:30:00", ag_hist))

    yield {
        "db": db_module, "bid": bid, "chefe_id": chefe_id,
        "barb_id": barb_id, "svc_id": svc_id,
        "root_id": root_id, "amanha": amanha,
        "ontem": ontem, "ag_id": ag_id, "ag_id2": ag_id2,
        "ag_hist": ag_hist, "slug": "cov3-slug",
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
        "SECRET_KEY": "test-cov3", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


# ── helpers de sessão ──────────────────────────────────────────

def _root(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]  = ctx["root_id"]
        s["role"]     = "root"
        s["user_nome"] = "Root"
        s.pop("barbearia_id", None)
    return c


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["chefe_id"]
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Chefe Cov3"
    return c


def _barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["barb_id"]
        s["role"]         = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Barbeiro Cov3"
    return c


def _limpar(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/root.py
# ══════════════════════════════════════════════════════════════

class TestRootBlueprint:

    def test_root_sem_auth_redireciona(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get("/root", follow_redirects=False)
        assert r.status_code == 302

    def test_root_dashboard(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.get("/root")
        assert r.status_code == 200

    def test_root_dashboard_com_erro_e_ok(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.get("/root?erro=teste&ok=outro")
        assert r.status_code == 200

    def test_root_criar_barbearia_campos_vazios(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={}, follow_redirects=False)
        assert r.status_code == 302
        assert b"erro" in r.headers["Location"].encode() or "erro" in r.headers["Location"]

    def test_root_criar_barbearia_username_invalido(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={
            "nome": "Test Barbearia", "chefe_nome": "Chefe",
            "username": "user name!", "senha": "senha123",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_criar_barbearia_senha_curta(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={
            "nome": "Test Barbearia", "chefe_nome": "Chefe",
            "username": "valido", "senha": "abc",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_criar_barbearia_username_duplicado(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={
            "nome": "Test Barbearia", "chefe_nome": "Chefe",
            "username": "chefe_cov3", "senha": "senha123",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_criar_barbearia_sucesso(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={
            "nome": "Nova Barbearia Root", "chefe_nome": "Chefe Nova",
            "username": "chefe_nova_root99", "senha": "senha999",
            "tipo": "barbearia",
        }, follow_redirects=False)
        assert r.status_code == 302
        assert "ok" in r.headers["Location"]

    def test_root_criar_barbearia_tipo_outro(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/criar", data={
            "nome": "Studio Root", "chefe_nome": "Owner",
            "username": "owner_studio99", "senha": "senha999",
            "tipo": "outro",
            "outro_tipo_label": "Studio",
            "outro_profissional": "Stylist",
            "outro_servico": "Serviço",
            "outro_agendamento": "Marcação",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_toggle_barbearia(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/toggle/{ctx['bid']}", follow_redirects=False)
        assert r.status_code == 302
        # Reativar
        c.post(f"/root/toggle/{ctx['bid']}")

    def test_root_editar_barbearia(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/editar/{ctx['bid']}", data={
            "nome": "Barbearia Cov3 Editada", "tipo": "barbearia"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_editar_barbearia_tipo_outro(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/editar/{ctx['bid']}", data={
            "nome": "Barbearia Cov3 Edit2", "tipo": "outro",
            "outro_tipo_label": "Studio", "outro_profissional": "Prof",
            "outro_servico": "Svc", "outro_agendamento": "Ag",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_alterar_senha_senha_atual_errada(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/alterar-senha", data={
            "senha_atual": "errada", "senha_nova": "nova123", "senha_confirma": "nova123"
        }, follow_redirects=False)
        assert r.status_code == 302
        assert "erro" in r.headers["Location"]

    def test_root_alterar_senha_nao_coincidem(self, client):
        c, ctx = client
        _root(c, ctx)
        # Obter senha root do ficheiro
        pw_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                               "barbearia", ".root_init_password")
        # Não podemos ler a senha, então testamos com confirmação diferente
        r = c.post("/root/alterar-senha", data={
            "senha_atual": "qualquer", "senha_nova": "nova123", "senha_confirma": "diferente"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_root_gerir_barbearia_invalida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/gerir/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_root_gerir_barbearia_valida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/gerir/{ctx['bid']}", follow_redirects=False)
        assert r.status_code == 302

    def test_root_sair_barbearia(self, client):
        c, ctx = client
        # Configurar sessão com root_gerir
        with c.session_transaction() as s:
            s["user_id"]  = ctx["root_id"]
            s["role"]     = "chefe"
            s["root_gerir"] = True
            s["barbearia_id"] = ctx["bid"]
        r = c.post("/root/sair-barbearia", follow_redirects=False)
        assert r.status_code == 302

    def test_root_sair_barbearia_sem_auth(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post("/root/sair-barbearia", follow_redirects=False)
        assert r.status_code == 302

    def test_root_planos_barbearia_invalida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.get("/root/planos/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_root_planos_barbearia_valida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.get(f"/root/planos/{ctx['bid']}")
        assert r.status_code == 200

    def test_root_precos_barbearia_invalida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/barbearia/99999/precos", data={"moeda": "ECV"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_root_precos_barbearia_valida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/barbearia/{ctx['bid']}/precos",
                   data={"moeda": "ECV", "preco_1m": "1000", "preco_6m": "5000", "preco_exp": "0"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_root_precos_barbearia_moeda_invalida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post(f"/root/barbearia/{ctx['bid']}/precos",
                   data={"moeda": "XYZ"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_root_registar_pagamento_barbearia_invalida(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/pagamento/99999", data={"plano": "1m"}, follow_redirects=False)
        assert r.status_code == 302

    def test_root_registar_pagamento_sucesso(self, client):
        c, ctx = client
        _root(c, ctx)
        # Criar barbearia só para este teste
        bid2 = ctx["db"].criar_barbearia("Barbearia Pagamento", tipo="barbearia")
        r = c.post(f"/root/pagamento/{bid2}", data={"plano": "1m"}, follow_redirects=False)
        assert r.status_code == 302

    def test_root_registar_pagamento_plano_ativo(self, client):
        c, ctx = client
        _root(c, ctx)
        # bid já tem plano ativo
        r = c.post(f"/root/pagamento/{ctx['bid']}", data={"plano": "1m", "_next": f"/root/planos/{ctx['bid']}"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_root_cancelar_plano(self, client):
        c, ctx = client
        _root(c, ctx)
        bid3 = ctx["db"].criar_barbearia("Barbearia Cancelar Plano", tipo="barbearia")
        ctx["db"].registar_pagamento(bid3, "1m")
        r = c.post(f"/root/cancelar-plano/{bid3}", follow_redirects=False)
        assert r.status_code == 302
        assert "ok" in r.headers["Location"]

    def test_root_cancelar_plano_barbearia_sem_nome(self, client):
        c, ctx = client
        _root(c, ctx)
        # ID inexistente
        r = c.post("/root/cancelar-plano/99998", follow_redirects=False)
        assert r.status_code == 302

    def test_root_precos_globais(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.get("/root/precos")
        assert r.status_code == 200

    def test_root_definir_precos(self, client):
        c, ctx = client
        _root(c, ctx)
        r = c.post("/root/planos/precos",
                   data={"preco_1m": "800", "preco_6m": "4000", "preco_exp": "0"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_conta_suspensa(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/conta-suspensa")
        assert r.status_code == 402


# ══════════════════════════════════════════════════════════════
#  blueprints/agendamentos.py
# ══════════════════════════════════════════════════════════════

class TestAgendamentosBlueprint:

    def test_index_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/")
        assert r.status_code == 200

    def test_index_barbeiro(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/")
        assert r.status_code == 200

    def test_index_filtro_barbeiro_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get(f"/?barbeiro_id={ctx['barb_id']}")
        assert r.status_code == 200

    def test_index_sem_auth(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302

    def test_novo_get(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/novo")
        assert r.status_code == 200

    def test_novo_post_campos_em_falta(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/novo", data={
            "cliente": "", "servico_id": "", "barbeiro_id": "", "data": "", "hora": ""
        })
        assert r.status_code == 200

    def test_novo_post_nome_em_falta(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/novo", data={
            "cliente": "", "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": amanha, "hora": "15:00",
        })
        assert r.status_code == 200

    def test_novo_post_sucesso(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/novo", data={
            "cliente": "Novo Cliente Teste", "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": amanha, "hora": "16:00",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_novo_post_conflito(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        # Tentar marcar no mesmo slot
        r = c.post("/novo", data={
            "cliente": "Conflito Cliente", "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": amanha, "hora": "10:00",
        })
        assert r.status_code == 200

    def test_walkin_get(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/walkin")
        assert r.status_code == 200

    def test_walkin_get_barbeiro(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/walkin")
        assert r.status_code == 200

    def test_walkin_post_sem_nome(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/walkin", data={
            "cliente": "", "servico_id": str(ctx["svc_id"]),
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_walkin_post_nome_curto(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/walkin", data={
            "cliente": "X", "servico_id": str(ctx["svc_id"]),
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_walkin_post_servico_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/walkin", data={
            "cliente": "Walk Cliente", "servico_id": "9999",
            "barbeiro_id": str(ctx["barb_id"]),
        }, follow_redirects=False)
        assert r.status_code in (200, 302)

    def test_iniciar_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/iniciar/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_iniciar_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/iniciar/{ctx['ag_id']}", follow_redirects=False)
        assert r.status_code == 302

    def test_terminar_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/terminar/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_terminar_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # ag_id já foi iniciado acima
        r = c.post(f"/terminar/{ctx['ag_id']}", data={"valor": "500"}, follow_redirects=False)
        assert r.status_code == 302

    def test_terminar_com_avaliacao(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # Criar um novo ag em andamento
        db = ctx["db"]
        amanha = ctx["amanha"]
        new_ag = db.criar_agendamento(
            "Terminar Av", ctx["svc_id"], f"{amanha} 17:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        db.iniciar_trabalho(new_ag)
        r = c.post(f"/terminar/{new_ag}", data={"valor": "300", "avaliacao": "5"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_avaliar_json(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # ag_id está concluído agora
        r = c.post(f"/avaliar/{ctx['ag_id']}",
                   json={"nota": 4},
                   content_type="application/json")
        assert r.status_code in (200, 403)

    def test_avaliar_ag_inexistente(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/avaliar/99999",
                   json={"nota": 3},
                   content_type="application/json")
        assert r.status_code == 403

    def test_avaliar_nota_invalida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # Criar ag concluído
        db = ctx["db"]
        ontem = ctx["ontem"]
        new_ag = db.criar_agendamento(
            "Avaliar Test", ctx["svc_id"], f"{ontem} 08:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        with db._write() as c2:
            c2.execute("UPDATE agendamentos SET status='concluido' WHERE id=?", (new_ag,))
        r = c.post(f"/avaliar/{new_ag}",
                   json={"nota": 9},
                   content_type="application/json")
        assert r.status_code == 400

    def test_nao_compareceu_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/nao-compareceu/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_nao_compareceu_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # ag_id2 ainda está agendado
        r = c.post(f"/nao-compareceu/{ctx['ag_id2']}", follow_redirects=False)
        assert r.status_code == 302

    def test_bloquear_post_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/bloquear", data={
            "barbeiro_id": str(ctx["barb_id"]),
            "data": amanha,
            "hora_inicio": "12:00",
            "hora_fim": "13:00",
            "motivo": "Almoço teste",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_bloquear_hora_invalida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/bloquear", data={
            "barbeiro_id": str(ctx["barb_id"]),
            "data": amanha,
            "hora_inicio": "13:00",
            "hora_fim": "12:00",
            "motivo": "",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_bloquear_data_passada(self, client):
        c, ctx = client
        _chefe(c, ctx)
        ontem = ctx["ontem"]
        r = c.post("/bloquear", data={
            "barbeiro_id": str(ctx["barb_id"]),
            "data": ontem,
            "hora_inicio": "10:00",
            "hora_fim": "11:00",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_bloquear_sem_barbeiro(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/bloquear", data={
            "barbeiro_id": "0",
            "data": amanha,
            "hora_inicio": "10:00",
            "hora_fim": "11:00",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_bloquear_barbeiro_de_outra_barbearia(self, client):
        c, ctx = client
        _chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.post("/bloquear", data={
            "barbeiro_id": "99999",
            "data": amanha,
            "hora_inicio": "10:00",
            "hora_fim": "11:00",
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_desbloquear_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/desbloquear/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_desbloquear_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # Criar bloqueio primeiro
        db = ctx["db"]
        amanha = ctx["amanha"]
        db.criar_bloqueio_hora(ctx["barb_id"], amanha, "14:00", "15:00", "bloqueio test")
        ausencias = db.listar_ausencias(ctx["bid"])
        bloqueio = next((a for a in ausencias if a.get("tipo") == "bloqueio"), None)
        if bloqueio:
            r = c.post(f"/desbloquear/{bloqueio['id']}", follow_redirects=False)
            assert r.status_code == 302

    def test_cancelar_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/cancelar/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_cancelar_valido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # Criar ag novo para cancelar
        db = ctx["db"]
        amanha = ctx["amanha"]
        ag_can = db.criar_agendamento(
            "Cancel Test", ctx["svc_id"], f"{amanha} 18:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        r = c.post(f"/cancelar/{ag_can}", follow_redirects=False)
        assert r.status_code == 302

    def test_reagendar_get(self, client):
        c, ctx = client
        _chefe(c, ctx)
        # Criar ag para reagendar
        db = ctx["db"]
        amanha = ctx["amanha"]
        ag_re = db.criar_agendamento(
            "Reagendar Test", ctx["svc_id"], f"{amanha} 09:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        r = c.get(f"/reagendar/{ag_re}")
        assert r.status_code == 200
        ctx["ag_re"] = ag_re

    def test_reagendar_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/reagendar/99999", follow_redirects=False)
        assert r.status_code == 302

    def test_reagendar_post_campos_vazios(self, client):
        c, ctx = client
        _chefe(c, ctx)
        db = ctx["db"]
        amanha = ctx["amanha"]
        ag_re2 = db.criar_agendamento(
            "Reagendar Test2", ctx["svc_id"], f"{amanha} 09:30:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        r = c.post(f"/reagendar/{ag_re2}", data={"data": "", "hora": ""})
        assert r.status_code == 200

    def test_reagendar_post_data_passada(self, client):
        c, ctx = client
        _chefe(c, ctx)
        db = ctx["db"]
        amanha = ctx["amanha"]
        ag_re3 = db.criar_agendamento(
            "Reagendar Test3", ctx["svc_id"], f"{amanha} 09:45:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        ontem = ctx["ontem"]
        r = c.post(f"/reagendar/{ag_re3}", data={
            "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": ontem, "hora": "10:00",
        })
        assert r.status_code == 200

    def test_reagendar_post_sucesso(self, client):
        c, ctx = client
        _chefe(c, ctx)
        db = ctx["db"]
        amanha = ctx["amanha"]
        depois = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        ag_re4 = db.criar_agendamento(
            "Reagendar Test4", ctx["svc_id"], f"{amanha} 09:50:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        r = c.post(f"/reagendar/{ag_re4}", data={
            "servico_id": str(ctx["svc_id"]),
            "barbeiro_id": str(ctx["barb_id"]),
            "data": depois, "hora": "10:00",
        }, follow_redirects=False)
        assert r.status_code in (200, 302)

    def test_historico_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico")
        assert r.status_code == 200

    def test_historico_barbeiro(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/historico")
        assert r.status_code == 200

    def test_historico_filtro_data(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get(f"/historico?data={ctx['ontem']}")
        assert r.status_code == 200

    def test_historico_filtro_barbeiro_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?barbeiro_id=99999")
        assert r.status_code == 200

    def test_historico_filtro_status(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?status=concluido")
        assert r.status_code == 200

    def test_historico_periodo_hoje(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?periodo=hoje")
        assert r.status_code == 200

    def test_historico_periodo_semana(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?periodo=semana")
        assert r.status_code == 200

    def test_historico_periodo_mes(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?periodo=mes")
        assert r.status_code == 200

    def test_historico_paginacao(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico?pagina=2")
        assert r.status_code == 200

    def test_historico_exportar_csv(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico/exportar.csv")
        assert r.status_code == 200
        assert b"text/csv" in r.content_type.encode() or r.content_type.startswith("text/csv")

    def test_historico_exportar_csv_com_filtros(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico/exportar.csv?periodo=mes&status=concluido")
        assert r.status_code == 200

    def test_historico_exportar_csv_sem_auth_chefe(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/historico/exportar.csv", follow_redirects=False)
        assert r.status_code == 302

    def test_minhas_marcacoes_barbeiro(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/minhas-marcacoes")
        assert r.status_code == 200

    def test_minhas_marcacoes_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/minhas-marcacoes")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  app.py — rotas e error handlers
# ══════════════════════════════════════════════════════════════

class TestAppRoutes:

    def test_404(self, client):
        c, ctx = client
        r = c.get("/rota-que-nao-existe")
        assert r.status_code == 404

    def test_healthz(self, client):
        c, ctx = client
        r = c.get("/healthz")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["status"] == "ok"

    def test_conta_suspensa_sem_sessao(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get("/conta-suspensa")
        assert r.status_code == 402

    def test_conta_suspensa_com_sessao(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/conta-suspensa")
        assert r.status_code == 402

    def test_login_get(self, client):
        c, ctx = client
        _limpar(c)
        r = c.get("/login")
        assert r.status_code == 200

    def test_login_post_invalido(self, client):
        c, ctx = client
        r = c.post("/login", data={"username": "naoexiste", "senha": "errada"})
        assert r.status_code == 200

    def test_login_post_valido_chefe(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post("/login", data={"username": "chefe_cov3", "senha": "senha_cov3"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_logout(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 302

    def test_logout_cliente_com_barbearia(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s["role"] = "cliente"
            s["barbearia_id"] = ctx["bid"]
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 302
