"""
Testes da eliminação em cascata de estabelecimentos — db.apagar_barbearia().

Garantem que:
  1. A barbearia e TODOS os dados ligados (barbearia_id e barbeiro_id) são
     removidos, sem deixar órfãos em nenhuma tabela.
  2. Outras barbearias ficam INTACTAS (isolamento).
  3. apagar_barbearia devolve um resumo correcto e levanta ValueError em id
     inexistente.

Correr: cd ~/Documentos/barbearia && python -m pytest tests/test_apagar_barbearia.py -v
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-apenas-para-testes")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_apagar.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    def _povoar(nome, prefixo):
        bid = db_module.criar_barbearia(nome, tipo="barbearia")
        db_module.registar_pagamento(bid, "exp")
        db_module.criar_chefe(f"Chefe {prefixo}", f"chefe_{prefixo}", "senha123", bid)
        db_module.criar_barbeiro(f"Barbeiro {prefixo}", bid)
        with db_module._read() as c:
            barb_id = c.execute(
                "SELECT id FROM barbeiros WHERE barbearia_id=? ORDER BY id DESC LIMIT 1",
                (bid,)).fetchone()["id"]
        db_module.criar_servico(f"Corte {prefixo}", 30, bid, preco=500)
        with db_module._read() as c:
            svc_id = c.execute(
                "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]
        for d in range(7):
            db_module.set_horario_dia(d, "08:00", "19:00", 0, bid)
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        db_module.criar_agendamento(
            f"Cliente {prefixo}", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id)
        # ausencia ligada ao barbeiro (tabela sem barbearia_id)
        db_module.criar_ausencia(barb_id, amanha, amanha, "folga", "teste")
        return bid, barb_id

    bid_alvo, barb_alvo = _povoar("Estabelecimento A Apagar", "alvo")
    bid_outro, barb_outro = _povoar("Estabelecimento B Preservar", "outro")

    yield {
        "db": db_module,
        "bid_alvo": bid_alvo, "barb_alvo": barb_alvo,
        "bid_outro": bid_outro, "barb_outro": barb_outro,
    }

    _db_conn._reset_conn()
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture()
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-apagar", "SESSION_COOKIE_SECURE": False,
    })
    root = ctx["db"].get_barbeiro_por_username("root")
    with app_module.app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"]   = root["id"]
            s["role"]      = "root"
            s["user_nome"] = "Root"
        yield c, ctx


def _tabelas(db_module):
    with db_module._read() as c:
        return [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def test_apagar_remove_tudo_sem_orfaos(ctx):
    db = ctx["db"]
    bid = ctx["bid_alvo"]

    resumo = db.apagar_barbearia(bid)
    assert resumo.get("barbearias") == 1
    # deve ter apagado barbeiros, servicos, agendamentos, horarios, ausencias, pagamentos
    assert resumo.get("barbeiros", 0) >= 1
    assert resumo.get("agendamentos", 0) >= 1
    assert resumo.get("ausencias", 0) >= 1

    orfaos = []
    with db._read() as c:
        for t in _tabelas(db):
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            if "barbearia_id" in cols:
                n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE barbearia_id=?", (bid,)).fetchone()[0]
                if n:
                    orfaos.append((t, n))
            elif "barbeiro_id" in cols:
                n = c.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE barbeiro_id=?", (ctx["barb_alvo"],)).fetchone()[0]
                if n:
                    orfaos.append((t, n))
    assert orfaos == [], f"órfãos deixados para trás: {orfaos}"

    with db._read() as c:
        assert c.execute("SELECT COUNT(*) FROM barbearias WHERE id=?", (bid,)).fetchone()[0] == 0


def test_apagar_nao_afecta_outra_barbearia(ctx):
    db = ctx["db"]
    db.apagar_barbearia(ctx["bid_alvo"])

    bid_outro = ctx["bid_outro"]
    with db._read() as c:
        assert c.execute("SELECT COUNT(*) FROM barbearias WHERE id=?", (bid_outro,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM barbeiros WHERE barbearia_id=?", (bid_outro,)).fetchone()[0] >= 1
        assert c.execute("SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=?", (bid_outro,)).fetchone()[0] >= 1
        assert c.execute("SELECT COUNT(*) FROM ausencias WHERE barbeiro_id=?", (ctx["barb_outro"],)).fetchone()[0] >= 1


def test_apagar_id_inexistente_levanta(ctx):
    db = ctx["db"]
    with pytest.raises(ValueError):
        db.apagar_barbearia(999999)


# ── Rota root (confirmação por nome + backup automático) ────────────

def test_rota_apagar_nome_errado_cancela(client, tmp_path):
    c, ctx = client
    db = ctx["db"]
    bid = ctx["bid_alvo"]
    r = c.post(f"/root/apagar/{bid}", data={"confirmar": "nome trocado"},
               follow_redirects=False)
    assert r.status_code == 302
    assert "erro" in r.headers["Location"]
    # NÃO apagou
    with db._read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM barbearias WHERE id=?", (bid,)).fetchone()[0] == 1


def test_rota_apagar_nome_certo_elimina_e_faz_backup(client):
    c, ctx = client
    db = ctx["db"]
    bid = ctx["bid_alvo"]
    with db._read() as conn:
        nome = conn.execute("SELECT nome FROM barbearias WHERE id=?", (bid,)).fetchone()["nome"]

    r = c.post(f"/root/apagar/{bid}", data={"confirmar": nome},
               follow_redirects=False)
    assert r.status_code == 302
    assert "ok" in r.headers["Location"]
    # eliminada
    with db._read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM barbearias WHERE id=?", (bid,)).fetchone()[0] == 0
    # backup automático criado ao lado da BD
    base_dir = os.path.dirname(db.DB_PATH) or "."
    bkp_dir = os.path.join(base_dir, "backups")
    backups = [f for f in os.listdir(bkp_dir)] if os.path.isdir(bkp_dir) else []
    assert any(f.startswith(f"antes_apagar_{bid}_") for f in backups), \
        f"backup automático não encontrado em {bkp_dir}: {backups}"


def test_rota_apagar_id_inexistente(client):
    c, ctx = client
    r = c.post("/root/apagar/888888", data={"confirmar": "x"}, follow_redirects=False)
    assert r.status_code == 302
    assert "erro" in r.headers["Location"]


def test_impersonacao_e_eliminacao_deixam_rasto_auditoria(client):
    """Acções sensíveis do root emitem trilho de auditoria (AUDIT ...).

    O logger 'security' tem propagate=False, por isso captura-se com um handler
    ligado directamente a ele (o caplog do pytest só ouve o root logger)."""
    import logging
    c, ctx = client
    db = ctx["db"]
    bid = ctx["bid_alvo"]
    with db._read() as conn:
        nome = conn.execute("SELECT nome FROM barbearias WHERE id=?", (bid,)).fetchone()["nome"]

    registos = []

    class _Cap(logging.Handler):
        def emit(self, record):
            registos.append(record.getMessage())

    slog = logging.getLogger("security")
    h = _Cap()
    slog.addHandler(h)
    try:
        # impersonar (maior lacuna anterior: root a agir como tenant sem rasto)
        c.post(f"/root/gerir/{ctx['bid_outro']}")
        # voltar a root para poder eliminar
        with c.session_transaction() as s:
            root = db.get_barbeiro_por_username("root")
            s["user_id"] = root["id"]; s["role"] = "root"; s.pop("root_gerir", None)
        c.post(f"/root/apagar/{bid}", data={"confirmar": nome})
    finally:
        slog.removeHandler(h)

    msgs = " ".join(registos)
    assert "AUDIT root-impersonar" in msgs
    assert "AUDIT root-eliminar-estabelecimento" in msgs
