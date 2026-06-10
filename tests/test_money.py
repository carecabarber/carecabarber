"""tests/test_money.py — Cobertura das zonas de dinheiro e booking atómico.

Alvo: db/agendamentos.py — iniciar_trabalho() e terminar_trabalho().
Estes são os caminhos críticos: registo do valor do serviço e os guards
atómicos que impedem dois serviços em curso no mesmo barbeiro.

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_money.py -v
"""
import os, sys, tempfile, shutil, pytest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-money-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_money.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()
    bid = db.criar_barbearia("Barbearia Money", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    db.criar_barbeiro("Barbeiro Money", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Money", bid)).fetchone()["id"]
    db.criar_servico("Corte Money", 30, bid, preco=500)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    def novo(hora="10:00:00"):
        return db.criar_agendamento(
            "Cliente Money", svc_id, f"{amanha} {hora}", bid, barbeiro_id=barb_id)

    yield {"db": db, "bid": bid, "barb_id": barb_id, "svc_id": svc_id, "novo": novo}

    _db_conn._CONN  = None
    db._CONN        = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ── iniciar_trabalho ────────────────────────────────────────────

def test_iniciar_inexistente_retorna_false(ctx):
    assert ctx["db"].iniciar_trabalho(999999) is False


def test_iniciar_marca_em_andamento(ctx):
    ag = ctx["novo"]("10:00:00")
    assert ctx["db"].iniciar_trabalho(ag) is True
    assert ctx["db"].get_agendamento(ag)["status"] == "em_andamento"


def test_iniciar_segundo_servico_mesmo_barbeiro_bloqueado(ctx):
    db = ctx["db"]
    a1 = ctx["novo"]("10:00:00")
    a2 = ctx["novo"]("11:00:00")
    assert db.iniciar_trabalho(a1) is True
    # barbeiro já tem um em curso → segundo é rejeitado
    assert db.iniciar_trabalho(a2) is False
    assert db.get_agendamento(a2)["status"] != "em_andamento"


def test_iniciar_duas_vezes_mesmo_agendamento(ctx):
    db = ctx["db"]
    ag = ctx["novo"]("10:00:00")
    assert db.iniciar_trabalho(ag) is True
    # já não está em 'agendado'/'walk-in' → não muda nada
    assert db.iniciar_trabalho(ag) is False


# ── terminar_trabalho ───────────────────────────────────────────

def test_terminar_regista_valor(ctx):
    db = ctx["db"]
    ag = ctx["novo"]("10:00:00")
    db.iniciar_trabalho(ag)
    db.terminar_trabalho(ag, 750)
    row = db.get_agendamento(ag)
    assert row["status"] == "concluido"
    assert row["valor"] == 750
    assert row["fim"]


def test_terminar_valor_none_vira_zero(ctx):
    db = ctx["db"]
    ag = ctx["novo"]("10:00:00")
    db.iniciar_trabalho(ag)
    db.terminar_trabalho(ag, None)
    assert db.get_agendamento(ag)["valor"] == 0


def test_terminar_so_afeta_em_andamento(ctx):
    db = ctx["db"]
    ag = ctx["novo"]("10:00:00")
    # ainda 'agendado' — terminar não deve concluir
    db.terminar_trabalho(ag, 500)
    assert db.get_agendamento(ag)["status"] != "concluido"


def test_terminar_inexistente_nao_rebenta(ctx):
    # id inexistente → barbearia_id_cache None, sem invalidação, sem excepção
    ctx["db"].terminar_trabalho(999999, 100)


def test_ciclo_completo_liberta_barbeiro(ctx):
    db = ctx["db"]
    a1 = ctx["novo"]("10:00:00")
    a2 = ctx["novo"]("11:00:00")
    db.iniciar_trabalho(a1)
    db.terminar_trabalho(a1, 500)
    # depois de terminar o primeiro, o barbeiro fica livre para o segundo
    assert db.iniciar_trabalho(a2) is True
