"""
tests/test_relatorios_db_coverage.py — Cobre funções de relatório em db/relatorios.py
que não eram exercitadas pelos testes de blueprint:

  - analytics_clientes  (linhas 308-378: LTV, freq_dias, serviço favorito, resets, período)
  - visitas_cliente     (linhas 296-305: contagem por telefone, telefone vazio → 0)
  - top_clientes        (ordenação por visitas)

Insere agendamentos directamente na DB (status='concluido'/'walkin') para ter
controlo total sobre valores, datas e telefones.
"""
import os, sys, pytest, tempfile, shutil

os.environ.setdefault("SECRET_KEY", "test-secret-relat-db")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def ctx():
    import database as db_mod
    import db._conn as _c

    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "relat_db.db")
    orig = _c.DB_PATH

    _c.DB_PATH = db_path
    db_mod.DB_PATH = db_path
    _c._CONN = None
    db_mod._CONN = None

    db_mod.init_db()

    bid = db_mod.criar_barbearia("RelatDB", tipo="barbearia")
    db_mod.registar_pagamento(bid, "exp")

    db_mod.criar_servico("Corte", 30, bid, preco=500)
    db_mod.criar_servico("Barba", 15, bid, preco=300)
    with db_mod._read() as c:
        svc_corte = c.execute(
            "SELECT id FROM servicos WHERE nome='Corte' AND barbearia_id=?", (bid,)
        ).fetchone()["id"]
        svc_barba = c.execute(
            "SELECT id FROM servicos WHERE nome='Barba' AND barbearia_id=?", (bid,)
        ).fetchone()["id"]

    def _ag(cliente, telefone, data_hora, status, valor, servico_id):
        with db_mod._write() as c:
            c.execute(
                "INSERT INTO agendamentos "
                "(barbearia_id, cliente, telefone, servico_id, data_hora, status, valor) "
                "VALUES (?,?,?,?,?,?,?)",
                (bid, cliente, telefone, servico_id, data_hora, status, valor))

    # Cliente A (913000001): 3 visitas concluídas, favorito = Corte (2x), LTV = 1300
    _ag("Ana", "913000001", "2026-01-01 10:00:00", "concluido", 500, svc_corte)
    _ag("Ana", "913000001", "2026-01-11 10:00:00", "concluido", 500, svc_corte)
    _ag("Ana", "913000001", "2026-01-21 10:00:00", "walkin",   300, svc_barba)
    # Cliente B (913000002): 1 visita concluída → freq_dias None (visitas<=1)
    _ag("Bruno", "913000002", "2026-02-01 11:00:00", "concluido", 500, svc_corte)
    # Cliente C sem telefone → agrupa por nome
    _ag("Carlos", None, "2026-03-01 09:00:00", "concluido", 500, svc_corte)
    # Agendamento futuro/agendado (não conta como visita)
    _ag("Ana", "913000001", "2026-12-31 10:00:00", "agendado", 0, svc_corte)

    # Reset de fidelidade para cliente A
    with db_mod._write() as c:
        c.execute(
            "INSERT INTO fidelidade_resets (barbearia_id, telefone, resetado_em) "
            "VALUES (?,?,?)", (bid, "913000001", "2026-01-15 00:00:00"))

    yield {"db": db_mod, "bid": bid, "svc_corte": svc_corte, "svc_barba": svc_barba}

    _c._reset_conn()
    db_mod._CONN = None
    _c.DB_PATH = orig
    db_mod.DB_PATH = orig
    shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  visitas_cliente
# ══════════════════════════════════════════════════════════════

class TestVisitasCliente:
    def test_telefone_vazio_retorna_zero(self, ctx):
        assert ctx["db"].visitas_cliente(ctx["bid"], "") == 0

    def test_telefone_none_retorna_zero(self, ctx):
        assert ctx["db"].visitas_cliente(ctx["bid"], None) == 0

    def test_conta_concluidos_e_walkins(self, ctx):
        # Ana: 2 concluido + 1 walkin = 3 (agendado não conta)
        assert ctx["db"].visitas_cliente(ctx["bid"], "913000001") == 3

    def test_cliente_uma_visita(self, ctx):
        assert ctx["db"].visitas_cliente(ctx["bid"], "913000002") == 1

    def test_cliente_inexistente_zero(self, ctx):
        assert ctx["db"].visitas_cliente(ctx["bid"], "999999999") == 0


# ══════════════════════════════════════════════════════════════
#  top_clientes
# ══════════════════════════════════════════════════════════════

class TestTopClientes:
    def test_ordena_por_visitas(self, ctx):
        top = ctx["db"].top_clientes(ctx["bid"], limite=10)
        assert len(top) >= 3
        # Ana tem mais visitas → primeira
        assert top[0]["telefone"] == "913000001"
        assert top[0]["visitas"] == 3

    def test_limite_respeitado(self, ctx):
        top = ctx["db"].top_clientes(ctx["bid"], limite=1)
        assert len(top) == 1


# ══════════════════════════════════════════════════════════════
#  analytics_clientes
# ══════════════════════════════════════════════════════════════

class TestAnalyticsClientes:
    def test_ltv_e_visitas(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        ana = next(r for r in res if r["telefone"] == "913000001")
        assert ana["visitas"] == 3
        assert ana["ltv"] == 1300  # 500 + 500 + 300

    def test_servico_favorito(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        ana = next(r for r in res if r["telefone"] == "913000001")
        assert ana["servico_favorito"] == "Corte"  # 2x Corte vs 1x Barba

    def test_freq_dias_calculada(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        ana = next(r for r in res if r["telefone"] == "913000001")
        # Primeira 01/01, última 21/01 → 20 dias / (3-1) = 10.0
        assert ana["freq_dias"] == 10.0

    def test_freq_dias_none_uma_visita(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        bruno = next(r for r in res if r["telefone"] == "913000002")
        assert bruno["freq_dias"] is None

    def test_resets_feitos(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        ana = next(r for r in res if r["telefone"] == "913000001")
        assert ana["resets_feitos"] == 1

    def test_cliente_sem_telefone_agrupa_por_nome(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"])
        carlos = next((r for r in res if r["chave"] == "Carlos"), None)
        assert carlos is not None
        assert carlos["visitas"] == 1

    def test_periodo_dias_filtra(self, ctx):
        # Janela curta a partir de uma data muito recente → sem visitas antigas (2026-01..03)
        res = ctx["db"].analytics_clientes(ctx["bid"], periodo_dias=1)
        total_visitas = sum(r["visitas"] for r in res)
        assert total_visitas == 0

    def test_limite_respeitado(self, ctx):
        res = ctx["db"].analytics_clientes(ctx["bid"], limite=1)
        assert len(res) == 1
