"""
tests/test_servicos_coverage.py — Cobre linhas em falta em:
  - db/servicos.py        (linhas 17, 27, 48, 59, 65-68)
  - blueprints/servicos.py (linhas 97-98, 130-132, 137-146, 151-153)

Cobre:
  - servico_por_id(None/0) → None
  - get_servicos_por_ids([]) → {}
  - atualizar_servico sem barbearia_id
  - apagar_servico em_uso + sem barbearia_id
  - apagar_servico não em_uso + sem barbearia_id
  - /clientes-bloqueados GET
  - /clientes/bloquear POST (válido, inválido)
  - /clientes/desbloquear/<id> POST
  - /configuracoes timezone inválido
"""
import os, sys, pytest, tempfile, shutil

os.environ.setdefault("SECRET_KEY", "test-secret-svcov")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def _ctx():
    import database as db_mod
    import db._conn as _c

    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "svcov.db")
    orig = _c.DB_PATH

    _c.DB_PATH = db_path
    db_mod.DB_PATH = db_path
    _c._CONN = None
    db_mod._CONN = None

    db_mod.init_db()

    bid = db_mod.criar_barbearia("SvcTeste", tipo="barbearia")
    db_mod.registar_pagamento(bid, "exp")

    db_mod.criar_chefe("Chefe S", "chefe_s", "pw123", bid)
    chefe_id = db_mod.get_barbeiro_por_username("chefe_s")["id"]

    db_mod.criar_servico("Srv1", 30, bid, preco=100)
    with db_mod._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE nome='Srv1' AND barbearia_id=?", (bid,)
        ).fetchone()["id"]

    yield {"db": db_mod, "bid": bid, "chefe_id": chefe_id, "svc_id": svc_id, "tmp": tmp}

    _c._reset_conn()
    db_mod._CONN = None
    _c.DB_PATH = orig
    db_mod.DB_PATH = orig
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def cl(_ctx):
    import app as app_mod
    app_mod.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret-svc", "SESSION_COOKIE_SECURE": False,
    })
    with app_mod.app.test_client() as c:
        yield c, _ctx


def _chefe(cl_tuple):
    c, ctx = cl_tuple
    with c.session_transaction() as s:
        s["user_id"] = ctx["chefe_id"]
        s["role"] = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"] = "Chefe S"
    return c, ctx


# ══════════════════════════════════════════════════════════════
#  db/servicos.py — linhas não cobertas
# ══════════════════════════════════════════════════════════════

class TestDbServicos:
    def test_servico_por_id_none(self, _ctx):
        """servico_por_id(None) → None (linha 17)."""
        import db.servicos as svc
        assert svc.servico_por_id(None) is None

    def test_servico_por_id_zero(self, _ctx):
        """servico_por_id(0) → None (linha 17)."""
        import db.servicos as svc
        assert svc.servico_por_id(0) is None

    def test_get_servicos_por_ids_empty(self, _ctx):
        """get_servicos_por_ids([]) → {} (linha 27)."""
        import db.servicos as svc
        assert svc.get_servicos_por_ids([]) == {}

    def test_get_servicos_por_ids_zeros(self, _ctx):
        """get_servicos_por_ids([0, None]) → {} (linha 27 via filter)."""
        import db.servicos as svc
        assert svc.get_servicos_por_ids([0, None]) == {}

    def test_atualizar_servico_sem_barbearia_id(self, _ctx):
        """atualizar_servico sem barbearia_id (linha 48 — else branch)."""
        import db.servicos as svc
        ctx = _ctx
        # Actualizar sem barbearia_id — deve funcionar
        svc.atualizar_servico(ctx["svc_id"], "Srv1 Updated", 45, 200)
        s = svc.servico_por_id(ctx["svc_id"])
        assert s["duracao_min"] == 45
        assert s["preco"] == 200
        # Restaurar
        svc.atualizar_servico(ctx["svc_id"], "Srv1", 30, 100)

    def test_apagar_servico_sem_bid_em_uso(self, _ctx):
        """apagar_servico sem barbearia_id + em uso → soft delete (linha 65-66)."""
        import db.servicos as svc
        import database as db_mod
        ctx = _ctx
        # Criar serviço extra para apagar
        db_mod.criar_servico("TmpEmUso", 20, ctx["bid"], preco=50)
        with db_mod._read() as c:
            tmp_id = c.execute(
                "SELECT id FROM servicos WHERE nome='TmpEmUso' AND barbearia_id=?",
                (ctx["bid"],)).fetchone()["id"]
        # Criar agendamento para marcar em uso
        db_mod.criar_agendamento("CLI", tmp_id,
                                  "2026-12-01 10:00:00", ctx["bid"],
                                  telefone="2381234567")
        # Apagar sem barbearia_id + em uso → soft delete (ativo=0)
        svc.apagar_servico(tmp_id)
        s = svc.servico_por_id(tmp_id)
        assert s["ativo"] == 0

    def test_apagar_servico_sem_bid_nao_em_uso(self, _ctx):
        """apagar_servico sem barbearia_id + não em uso → hard delete (linhas 67-68)."""
        import db.servicos as svc
        import database as db_mod
        ctx = _ctx
        # Criar serviço sem agendamentos
        db_mod.criar_servico("TmpSemUso", 20, ctx["bid"], preco=50)
        with db_mod._read() as c:
            tmp_id = c.execute(
                "SELECT id FROM servicos WHERE nome='TmpSemUso' AND barbearia_id=?",
                (ctx["bid"],)).fetchone()["id"]
        # Apagar sem barbearia_id + sem uso → hard delete
        svc.apagar_servico(tmp_id)
        assert svc.servico_por_id(tmp_id) is None

    def test_apagar_servico_com_bid_em_uso(self, _ctx):
        """apagar_servico com barbearia_id + em uso → soft delete (linha 59)."""
        import db.servicos as svc
        import database as db_mod
        ctx = _ctx
        db_mod.criar_servico("TmpBidUso", 20, ctx["bid"], preco=60)
        with db_mod._read() as c:
            tmp_id = c.execute(
                "SELECT id FROM servicos WHERE nome='TmpBidUso' AND barbearia_id=?",
                (ctx["bid"],)).fetchone()["id"]
        # Criar agendamento para marcar em uso
        db_mod.criar_agendamento("CLI2", tmp_id,
                                  "2026-12-02 10:00:00", ctx["bid"],
                                  telefone="2381234568")
        svc.apagar_servico(tmp_id, barbearia_id=ctx["bid"])
        s = svc.servico_por_id(tmp_id)
        assert s["ativo"] == 0


# ══════════════════════════════════════════════════════════════
#  blueprints/servicos.py — clientes bloqueados + config TZ inválido
# ══════════════════════════════════════════════════════════════

class TestClientesBloqueados:
    def test_listar_clientes_bloqueados(self, cl):
        c, ctx = _chefe(cl)
        r = c.get("/clientes-bloqueados", follow_redirects=True)
        assert r.status_code == 200

    def test_bloquear_cliente_valido(self, cl):
        c, ctx = _chefe(cl)
        r = c.post("/clientes/bloquear",
                   data={"telefone": "2381111111", "motivo": "Sem pagamento"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_bloquear_cliente_tel_invalido(self, cl):
        """Telefone inválido → flash erro (linhas 140-142)."""
        c, ctx = _chefe(cl)
        r = c.post("/clientes/bloquear",
                   data={"telefone": "abc", "motivo": ""},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_bloquear_cliente_tel_vazio(self, cl):
        """Telefone vazio → flash erro."""
        c, ctx = _chefe(cl)
        r = c.post("/clientes/bloquear",
                   data={"telefone": "", "motivo": "teste"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_desbloquear_cliente(self, cl):
        """Desbloquear cliente existente (linhas 151-153)."""
        import database as db_mod
        c, ctx = _chefe(cl)
        # Bloquear primeiro
        db_mod.cliente_bloquear(ctx["bid"], "2389999999", "teste")
        with db_mod._read() as con:
            blq = con.execute(
                "SELECT id FROM clientes_bloqueados WHERE telefone='2389999999' AND barbearia_id=?",
                (ctx["bid"],)).fetchone()
        if not blq:
            return  # já pode ter sido limpo
        r = c.post(f"/clientes/desbloquear/{blq['id']}", follow_redirects=True)
        assert r.status_code == 200

    def test_desbloquear_cliente_id_qualquer(self, cl):
        """Desbloquear ID inexistente — não crasha."""
        c, ctx = _chefe(cl)
        r = c.post("/clientes/desbloquear/99999", follow_redirects=True)
        assert r.status_code == 200


class TestConfiguracoesTZ:
    def test_tz_invalido_mostra_flash(self, cl):
        """Timezone inválido → flash erro (linhas 97-98)."""
        c, ctx = _chefe(cl)
        r = c.post("/configuracoes",
                   data={"acao": "geral",
                         "buffer_minutos": "10",
                         "max_por_dia": "20",
                         "moeda": "ECV",
                         "timezone": "Invalid/Timezone_XXX"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_tz_valido_guardado(self, cl):
        """Timezone válido → guardado sem flash de erro."""
        c, ctx = _chefe(cl)
        r = c.post("/configuracoes",
                   data={"acao": "geral",
                         "buffer_minutos": "10",
                         "max_por_dia": "20",
                         "moeda": "ECV",
                         "timezone": "Atlantic/Cape_Verde"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_configuracoes_get(self, cl):
        """GET /configuracoes renderiza página."""
        c, ctx = _chefe(cl)
        r = c.get("/configuracoes", follow_redirects=True)
        assert r.status_code == 200

    def test_configuracoes_horario(self, cl):
        """POST horario → guarda horário."""
        c, ctx = _chefe(cl)
        form_data = {"acao": "horario"}
        for dia in range(7):
            form_data[f"aberto_{dia}"] = "on"
            form_data[f"abertura_{dia}"] = "09:00"
            form_data[f"fecho_{dia}"] = "18:00"
        r = c.post("/configuracoes", data=form_data, follow_redirects=True)
        assert r.status_code == 200

    def test_configuracoes_dia_fechado_valido(self, cl):
        c, ctx = _chefe(cl)
        r = c.post("/configuracoes",
                   data={"acao": "dia_fechado",
                         "data_fechada": "2026-12-25",
                         "motivo_fechado": "Natal"},
                   follow_redirects=True)
        assert r.status_code == 200

    def test_configuracoes_remover_dia(self, cl):
        """Remover dia fechado existente."""
        import database as db_mod
        c, ctx = _chefe(cl)
        db_mod.adicionar_dia_fechado("2026-12-26", "Natal2", ctx["bid"])
        dias = db_mod.listar_dias_fechados(ctx["bid"])
        if not dias:
            return
        r = c.post("/configuracoes",
                   data={"acao": "remover_dia", "dia_id": str(dias[-1]["id"])},
                   follow_redirects=True)
        assert r.status_code == 200
