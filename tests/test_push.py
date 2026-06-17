"""tests/test_push.py — Cobertura de db/push.py + API cliente-push.

Alvos:
  db/push.py                        66% → 95%+
    push_listar(com barbeiro_id)
    push_remover_expiradas(lista não-vazia)
    cliente_push_guardar / remover / listar_por_tel
  blueprints/api.py (rotas cliente push)
    POST /api/cliente-push/subscribe
    POST /api/cliente-push/unsubscribe

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_push.py -v
"""

import os, sys, pytest, tempfile

os.environ.setdefault("SECRET_KEY", "test-push-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_push.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid = db.criar_barbearia("Barbearia Push", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("push-test", bid))

    db.criar_chefe("Chefe Push", "chefe_push", "senha_push", bid)
    chefe = db.get_barbeiro_por_username("chefe_push")

    svc_id = db.criar_servico("Corte", 30, bid, preco=1000)

    yield {
        "bid": bid,
        "chefe_id": chefe["id"],
        "svc_id": svc_id,
        "slug": "push-test",
        "db_path": tmp_db,
    }

    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    _db_conn._CONN   = None

    import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def app(ctx):
    import app as _app_module
    _app = _app_module.app
    _app.config["TESTING"]    = True
    _app.config["SECRET_KEY"] = "test-push-secret"
    _app.config["WTF_CSRF_ENABLED"] = False
    return _app


# ══════════════════════════════════════════════════════════════
#  TESTES db/push.py
# ══════════════════════════════════════════════════════════════

class TestPushStaff:
    """Cobre as funções de push para staff (barbeiros)."""

    def test_push_guardar_e_listar_todos(self, ctx):
        """push_listar sem filtro de barbeiro devolve tudo da barbearia."""
        import db.push as push_mod
        push_mod.push_guardar(ctx["chefe_id"], ctx["bid"],
                               "https://ep1.test/a", "p256dh1", "auth1")
        subs = push_mod.push_listar(ctx["bid"])
        assert any(s["endpoint"] == "https://ep1.test/a" for s in subs)

    def test_push_listar_filtrado_por_barbeiro(self, ctx):
        """push_listar com barbeiro_id filtra correctamente (cobre linha 32)."""
        import db.push as push_mod
        push_mod.push_guardar(ctx["chefe_id"], ctx["bid"],
                               "https://ep2.test/b", "p256dh2", "auth2")
        subs_chefe  = push_mod.push_listar(ctx["bid"], barbeiro_id=ctx["chefe_id"])
        subs_outro  = push_mod.push_listar(ctx["bid"], barbeiro_id=99999)
        assert len(subs_chefe) >= 1
        assert len(subs_outro) == 0

    def test_push_remover(self, ctx):
        """push_remover elimina o endpoint correcto."""
        import db.push as push_mod
        push_mod.push_guardar(ctx["chefe_id"], ctx["bid"],
                               "https://ep-del.test/x", "pk", "ak")
        push_mod.push_remover("https://ep-del.test/x")
        subs = push_mod.push_listar(ctx["bid"])
        assert not any(s["endpoint"] == "https://ep-del.test/x" for s in subs)

    def test_push_remover_expiradas_lista_vazia(self, ctx):
        """push_remover_expiradas com lista vazia retorna imediatamente (guard linha 43)."""
        import db.push as push_mod
        # Não deve lançar erro
        push_mod.push_remover_expiradas([])

    def test_push_remover_expiradas_lista_nao_vazia(self, ctx):
        """push_remover_expiradas com endpoints reais elimina-os (cobre linhas 45-47)."""
        import db.push as push_mod
        push_mod.push_guardar(ctx["chefe_id"], ctx["bid"],
                               "https://ep-exp.test/1", "pk1", "ak1")
        push_mod.push_guardar(ctx["chefe_id"], ctx["bid"],
                               "https://ep-exp.test/2", "pk2", "ak2")
        push_mod.push_remover_expiradas(["https://ep-exp.test/1", "https://ep-exp.test/2"])
        subs = push_mod.push_listar(ctx["bid"])
        endpoints = {s["endpoint"] for s in subs}
        assert "https://ep-exp.test/1" not in endpoints
        assert "https://ep-exp.test/2" not in endpoints

    def test_push_remover_expiradas_endpoint_inexistente(self, ctx):
        """push_remover_expiradas com endpoint que não existe não falha."""
        import db.push as push_mod
        # Não deve lançar erro
        push_mod.push_remover_expiradas(["https://nao-existe.test/xyz"])


class TestPushCliente:
    """Cobre cliente_push_guardar/remover/listar_por_tel (linhas 51-75)."""

    def test_cliente_push_guardar(self, ctx):
        """Guardar subscripção de cliente cobre linhas 51-53."""
        import db.push as push_mod
        push_mod.cliente_push_guardar(
            "9991234", ctx["bid"],
            "https://cliente-ep.test/a", "cpk1", "cak1")
        subs = push_mod.cliente_push_listar_por_tel("9991234", ctx["bid"])
        assert len(subs) == 1
        assert subs[0]["endpoint"] == "https://cliente-ep.test/a"

    def test_cliente_push_guardar_actualiza_existente(self, ctx):
        """Guardar com mesmo endpoint actualiza em vez de duplicar."""
        import db.push as push_mod
        push_mod.cliente_push_guardar(
            "9991234", ctx["bid"],
            "https://cliente-ep.test/a", "cpk1_novo", "cak1_novo")
        subs = push_mod.cliente_push_listar_por_tel("9991234", ctx["bid"])
        # Deve continuar a ser só 1 (upsert por endpoint)
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "cpk1_novo"

    def test_cliente_push_remover(self, ctx):
        """cliente_push_remover elimina o endpoint (cobre linhas 66-67)."""
        import db.push as push_mod
        push_mod.cliente_push_guardar(
            "9991234", ctx["bid"],
            "https://cliente-ep.test/del", "cpk", "cak")
        push_mod.cliente_push_remover("https://cliente-ep.test/del")
        subs = push_mod.cliente_push_listar_por_tel("9991234", ctx["bid"])
        assert not any(s["endpoint"] == "https://cliente-ep.test/del" for s in subs)

    def test_cliente_push_listar_por_tel_vazio(self, ctx):
        """listar_por_tel para telefone sem subscrições devolve []."""
        import db.push as push_mod
        subs = push_mod.cliente_push_listar_por_tel("0000000", ctx["bid"])
        assert subs == []

    def test_cliente_push_listar_isolado_por_barbearia(self, ctx):
        """listar_por_tel não mistura barbearias diferentes."""
        import db.push as push_mod
        import database as db_mod
        bid2 = db_mod.criar_barbearia("Barbearia2 Push", tipo="barbearia")
        push_mod.cliente_push_guardar(
            "9991234", bid2,
            "https://cliente-ep.test/b2", "pk", "ak")
        subs = push_mod.cliente_push_listar_por_tel("9991234", ctx["bid"])
        # Não deve aparecer o da barbearia 2
        assert not any(s["endpoint"] == "https://cliente-ep.test/b2" for s in subs)


# ══════════════════════════════════════════════════════════════
#  TESTES API /api/cliente-push/
# ══════════════════════════════════════════════════════════════

class TestApiClientePush:
    """Cobre os endpoints POST /api/cliente-push/subscribe e /unsubscribe."""

    def _cliente_session(self, c, ctx):
        """Abre sessão como cliente autenticado."""
        with c.session_transaction() as sess:
            sess["role"]         = "cliente"
            sess["barbearia_id"] = ctx["bid"]
            sess["telefone"]     = "9881234"
            sess["user_nome"]    = "Cliente Teste"

    def test_subscribe_sem_sessao_cliente(self, app, ctx):
        """Sem sessão de cliente devolve 403."""
        with app.test_client() as c:
            r = c.post("/api/cliente-push/subscribe",
                       json={"endpoint": "ep", "p256dh": "pk", "auth": "ak"},
                       content_type="application/json")
            assert r.status_code == 403

    def test_subscribe_dados_incompletos(self, app, ctx):
        """Dados em falta devolvem 400."""
        with app.test_client() as c:
            self._cliente_session(c, ctx)
            r = c.post("/api/cliente-push/subscribe",
                       json={"endpoint": "ep"},   # falta p256dh e auth
                       content_type="application/json")
            assert r.status_code in (400, 503)  # 503 se VAPID não configurado

    def test_subscribe_push_nao_disponivel(self, app, ctx):
        """Quando _PUSH_OK=False devolve 503."""
        from unittest.mock import patch
        with app.test_client() as c:
            self._cliente_session(c, ctx)
            with patch("helpers._PUSH_OK", False), \
                 patch("helpers_security._PUSH_OK", False):
                r = c.post("/api/cliente-push/subscribe",
                           json={"endpoint": "ep", "p256dh": "pk", "auth": "ak"},
                           content_type="application/json")
                assert r.status_code == 503

    def test_subscribe_ok(self, app, ctx):
        """Com PUSH_OK=True e dados completos guarda a subscripção."""
        from unittest.mock import patch
        import db.push as push_mod
        with app.test_client() as c:
            self._cliente_session(c, ctx)
            with patch("helpers._PUSH_OK", True), \
                 patch("helpers_security._PUSH_OK", True):
                r = c.post("/api/cliente-push/subscribe",
                           json={"endpoint": "https://api-test.ep/ok",
                                 "p256dh": "testpk", "auth": "testak"},
                           content_type="application/json")
                assert r.status_code == 200
                assert r.get_json()["ok"] is True
        # Confirmar que ficou guardado
        subs = push_mod.cliente_push_listar_por_tel("9881234", ctx["bid"])
        assert any(s["endpoint"] == "https://api-test.ep/ok" for s in subs)

    def test_unsubscribe_sem_sessao_cliente(self, app, ctx):
        """Sem sessão de cliente devolve 403."""
        with app.test_client() as c:
            r = c.post("/api/cliente-push/unsubscribe",
                       json={"endpoint": "ep"},
                       content_type="application/json")
            assert r.status_code == 403

    def test_unsubscribe_ok(self, app, ctx):
        """Unsubscribe com endpoint válido remove a subscripção."""
        import db.push as push_mod
        push_mod.cliente_push_guardar("9881234", ctx["bid"],
                                       "https://api-test.ep/del", "pk", "ak")
        with app.test_client() as c:
            self._cliente_session(c, ctx)
            r = c.post("/api/cliente-push/unsubscribe",
                       json={"endpoint": "https://api-test.ep/del"},
                       content_type="application/json")
            assert r.status_code == 200
            assert r.get_json()["ok"] is True
        subs = push_mod.cliente_push_listar_por_tel("9881234", ctx["bid"])
        assert not any(s["endpoint"] == "https://api-test.ep/del" for s in subs)

    def test_unsubscribe_endpoint_vazio(self, app, ctx):
        """Unsubscribe sem endpoint não falha (endpoint vazio → não faz nada)."""
        with app.test_client() as c:
            self._cliente_session(c, ctx)
            r = c.post("/api/cliente-push/unsubscribe",
                       json={},
                       content_type="application/json")
            assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  TESTES db/agendamentos.py — espera_listar_activa paginação
# ══════════════════════════════════════════════════════════════

class TestEsperaListarActivaPaginacao:
    """Cobre a paginação adicionada a espera_listar_activa."""

    def _criar_entradas(self, ctx, n=5):
        import database as db
        from datetime import datetime, timedelta
        base = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        expira = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(n):
            db.espera_adicionar(ctx["bid"], f"Cliente {i}", f"900000{i}",
                                ctx["svc_id"], None, base)

    def test_listar_devolve_dict_com_paginacao(self, ctx):
        """Resultado tem campos items, total, limit, offset."""
        import database as db
        self._criar_entradas(ctx, n=3)
        result = db.espera_listar_activa(ctx["bid"])
        assert "items"  in result
        assert "total"  in result
        assert "limit"  in result
        assert "offset" in result

    def test_listar_limit_e_offset(self, ctx):
        """limit e offset funcionam correctamente."""
        import database as db
        self._criar_entradas(ctx, n=10)
        p1 = db.espera_listar_activa(ctx["bid"], limit=3, offset=0)
        p2 = db.espera_listar_activa(ctx["bid"], limit=3, offset=3)
        assert len(p1["items"]) <= 3
        assert len(p2["items"]) <= 3
        # Páginas não se sobrepõem (ids diferentes)
        ids1 = {r["id"] for r in p1["items"]}
        ids2 = {r["id"] for r in p2["items"]}
        assert ids1.isdisjoint(ids2)

    def test_listar_limit_maximo_200(self, ctx):
        """Limit máximo é 200 mesmo pedindo mais."""
        import database as db
        result = db.espera_listar_activa(ctx["bid"], limit=9999)
        assert result["limit"] == 200

    def test_listar_total_correcto(self, ctx):
        """Total reflecte todos os registos, não só a página."""
        import database as db
        self._criar_entradas(ctx, n=5)
        result_full = db.espera_listar_activa(ctx["bid"], limit=200)
        result_pag  = db.espera_listar_activa(ctx["bid"], limit=2)
        # total deve ser o mesmo em ambos
        assert result_full["total"] == result_pag["total"]
        assert len(result_pag["items"]) <= 2
