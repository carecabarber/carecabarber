"""
tests/test_resumo_mensal.py — Cobertura de db.relatorios.resumo_mensal
e do gerador de texto em scripts/relatorio_mensal.py.

resumo_mensal agrega um mês (YYYY-MM) por barbearia: atendidos, receita,
ticket médio, taxa de perdidos, repartição por barbeiro e top serviços.
"""
import os, sys, tempfile, shutil, pytest
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret-resumo")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _conn_mod

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "resumo_test.db")
    orig    = _conn_mod.DB_PATH

    _conn_mod.DB_PATH = tmp_db
    db.DB_PATH        = tmp_db
    _conn_mod._CONN   = None
    db._CONN          = None
    db.init_db()

    bid = db.criar_barbearia("Barbearia Teste", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    db.criar_chefe("Chefe", "chefe_x", "pass123", bid)
    chefe_id = db.get_barbeiro_por_username("chefe_x")["id"]
    db.criar_barbeiro("Barbeiro Dois", bid)
    with db._read() as c:
        barb2 = c.execute(
            "SELECT id FROM barbeiros WHERE nome='Barbeiro Dois' AND barbearia_id=?", (bid,)
        ).fetchone()["id"]

    db.criar_servico("Corte", 30, bid, preco=500)
    db.criar_servico("Barba", 20, bid, preco=300)
    with db._read() as c:
        corte = c.execute("SELECT id FROM servicos WHERE nome='Corte' AND barbearia_id=?", (bid,)).fetchone()["id"]
        barba = c.execute("SELECT id FROM servicos WHERE nome='Barba' AND barbearia_id=?", (bid,)).fetchone()["id"]

    # ── Inserir agendamentos no mês alvo 2026-05 ──────────────
    MES = "2026-05"
    def _ins(cliente, svc, dia, status, valor, barbeiro):
        with db._write_exclusive() as c:
            c.execute(
                "INSERT INTO agendamentos "
                "(barbearia_id, cliente, telefone, servico_id, barbeiro_id, data_hora, status, valor) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (bid, cliente, "238" + str(abs(hash(cliente)) % 10000000),
                 svc, barbeiro, f"{MES}-{dia:02d} 10:00:00", status, valor))

    # 3 concluídos (chefe), 1 walkin (barb2), 1 cancelado, 1 nao_compareceu
    _ins("Ana",   corte, 5,  "concluido",      500, chefe_id)
    _ins("Bruno", corte, 6,  "concluido",      500, chefe_id)
    _ins("Carla", barba, 7,  "concluido",      300, chefe_id)
    _ins("Diogo", corte, 8,  "walkin",         500, barb2)
    _ins("Eva",   corte, 9,  "cancelado",      0,   chefe_id)
    _ins("Fabio", corte, 10, "nao_compareceu", 0,   chefe_id)
    # 1 noutro mês — não deve contar
    _ins("Gil",   corte, 5,  "concluido",      500, chefe_id)  # placeholder, overwrite below
    with db._write_exclusive() as c:
        c.execute("UPDATE agendamentos SET data_hora='2026-04-05 10:00:00' WHERE cliente='Gil'")

    yield {"db": db, "bid": bid, "mes": MES, "chefe_id": chefe_id, "barb2": barb2}

    _conn_mod._reset_conn()
    db._CONN = None
    _conn_mod.DB_PATH = orig
    db.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestResumoMensal:
    def test_totais(self, ctx):
        r = ctx["db"].resumo_mensal(ctx["bid"], ctx["mes"])
        # 3 concluidos + 1 walkin = 4 atendidos
        assert r["atendidos"] == 4
        assert r["walkins"] == 1
        assert r["cancelados"] == 1
        assert r["faltas"] == 1
        # receita = 500+500+300+500 = 1800
        assert r["receita"] == 1800
        assert r["ticket_medio"] == 450  # 1800/4

    def test_taxa_perdidos(self, ctx):
        r = ctx["db"].resumo_mensal(ctx["bid"], ctx["mes"])
        # total do mês = 6 (exclui o de Abril); perdidos = 2 → 33%
        assert r["taxa_perdidos"] == 33

    def test_por_barbeiro(self, ctx):
        r = ctx["db"].resumo_mensal(ctx["bid"], ctx["mes"])
        nomes = {pb["nome"]: pb for pb in r["por_barbeiro"]}
        assert nomes["Chefe"]["atendidos"] == 3
        assert nomes["Chefe"]["receita"] == 1300
        assert nomes["Barbeiro Dois"]["atendidos"] == 1
        assert nomes["Barbeiro Dois"]["receita"] == 500

    def test_top_servicos(self, ctx):
        r = ctx["db"].resumo_mensal(ctx["bid"], ctx["mes"])
        top = {s["nome"]: s for s in r["top_servicos"]}
        assert top["Corte"]["n"] == 3   # Ana, Bruno, Diogo (walkin)
        assert top["Barba"]["n"] == 1

    def test_mes_vazio(self, ctx):
        r = ctx["db"].resumo_mensal(ctx["bid"], "2020-01")
        assert r["atendidos"] == 0
        assert r["receita"] == 0
        assert r["ticket_medio"] == 0
        assert r["taxa_perdidos"] == 0

    def test_outro_mes_isolado(self, ctx):
        # Abril tem só o "Gil" concluído
        r = ctx["db"].resumo_mensal(ctx["bid"], "2026-04")
        assert r["atendidos"] == 1
        assert r["receita"] == 500


class TestGeradorTexto:
    def test_relatorio_inclui_barbearia_e_metricas(self, ctx, monkeypatch):
        import importlib
        rel = importlib.import_module("scripts.relatorio_mensal")
        texto = rel.gerar_relatorio(ctx["mes"], ctx["bid"])
        assert "Barbearia Teste" in texto
        assert "Clientes atendidos : 4" in texto
        assert "Corte" in texto
        assert "Chefe" in texto

    def test_relatorio_mes_sem_actividade(self, ctx):
        import importlib
        rel = importlib.import_module("scripts.relatorio_mensal")
        texto = rel.gerar_relatorio("2019-01", ctx["bid"])
        assert "Sem actividade" in texto

    def test_mes_anterior_formato(self):
        import importlib
        rel = importlib.import_module("scripts.relatorio_mensal")
        m = rel._mes_anterior()
        assert len(m) == 7 and m[4] == "-"
