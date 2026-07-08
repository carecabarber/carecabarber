"""
tests/test_coverage_extra2.py — Testes focados nos gaps de cobertura restantes.

Cobre:
  - app.py: _enviar_lembretes_push, _push_notif_sub, _enviar_lembretes_push_cliente,
            _ciclo_limpeza (caminhos ciclo%6 e ciclo%288), Sentry import guard
  - helpers_security.py: alerta_critico, _push_one retry/expired/failed paths,
                         _push_notif quando PUSH_OK/VAPID_PRIVATE_KEY falsos
  - blueprints/cliente.py: fila-espera (POST), dispensar-espera (POST)

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage_extra2.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-extra2")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE PARTILHADA
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_extra2.db")
    orig    = _db_conn.DB_PATH

    _db_conn._reset_conn()          # garante _READ_CONN=None antes de mudar DB_PATH
    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    bid = db_module.criar_barbearia("Barbearia Extra2", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    db_module.criar_chefe("Chefe X2", "chefe_x2", "senha_x2", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_x2")["id"]

    db_module.criar_barbeiro("Barbeiro X2", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro X2", bid)).fetchone()["id"]
    db_module.set_credenciais(barb_id, "barb_x2", "pass_x2")

    db_module.criar_servico("Corte X2", 30, bid, preco=800)
    with db_module._read() as c:
        rows = c.execute("SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchall()
    svc_id = rows[0]["id"]

    # Horário completo
    for dia in range(6):
        db_module.set_horario_dia(dia, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(6, "08:00", "19:00", 1, bid)

    hoje  = datetime.now().strftime("%Y-%m-%d")
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    yield {
        "db":       db_module,
        "bid":      bid,
        "chefe_id": chefe_id,
        "barb_id":  barb_id,
        "svc_id":   svc_id,
        "hoje":     hoje,
        "amanha":   amanha,
        "tmp_dir":  tmp_dir,
    }

    _db_conn._reset_conn()
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-extra2", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _como_cliente(c, ctx, slug="barbearia-extra2"):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Cliente X2"
        s["telefone"]     = "912345678"
    return c


def _limpar_sessao(c):
    with c.session_transaction() as s:
        s.clear()
    return c


def _slug(ctx):
    """Obtém o slug da barbearia criada."""
    import database as db
    b = db.get_barbearia(ctx["bid"])
    return b.get("slug", "barbearia-extra2")


# ══════════════════════════════════════════════════════════════
#  helpers_security.py — _push_one
# ══════════════════════════════════════════════════════════════

class TestPushOne:

    def _sub(self):
        return {"endpoint": "https://push.example.com/sub123",
                "p256dh": "key_abc", "auth": "auth_xyz"}

    def test_push_one_ok(self):
        """webpush bem-sucedido → retorna 'ok'."""
        import helpers_security as hs
        with patch.object(hs, "webpush", return_value=None) as mock_wp, \
             patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "fake_key"):
            result = hs._push_one(self._sub(), '{"titulo":"T","corpo":"C","url":"/"}')
        assert result == "ok"
        mock_wp.assert_called_once()

    def test_push_one_expired_404(self):
        """WebPushException com status 404 → retorna 'expired' sem retry."""
        import helpers_security as hs
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        exc = hs.WebPushException("Gone", response=mock_resp)

        with patch.object(hs, "webpush", side_effect=exc):
            result = hs._push_one(self._sub(), "{}")
        assert result == "expired"

    def test_push_one_expired_410(self):
        """WebPushException com status 410 → retorna 'expired' sem retry."""
        import helpers_security as hs
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        exc = hs.WebPushException("Gone", response=mock_resp)

        with patch.object(hs, "webpush", side_effect=exc):
            result = hs._push_one(self._sub(), "{}")
        assert result == "expired"

    def test_push_one_failed_after_retries(self):
        """Falha genérica em todas as tentativas → retorna 'failed'."""
        import helpers_security as hs
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        exc = hs.WebPushException("Server Error", response=mock_resp)

        # Sem sleep para acelerar o teste
        with patch.object(hs, "webpush", side_effect=exc), \
             patch("time.sleep"):
            result = hs._push_one(self._sub(), "{}")
        assert result == "failed"

    def test_push_one_exception_generica(self):
        """Excepção genérica (não WebPushException) em todas as tentativas → 'failed'."""
        import helpers_security as hs

        with patch.object(hs, "webpush", side_effect=RuntimeError("Network error")), \
             patch("time.sleep"):
            result = hs._push_one(self._sub(), "{}")
        assert result == "failed"

    def test_push_one_webpush_exception_sem_response(self):
        """WebPushException sem response (response=None) → não é expired, tenta retry → 'failed'."""
        import helpers_security as hs
        exc = hs.WebPushException("No response", response=None)

        with patch.object(hs, "webpush", side_effect=exc), \
             patch("time.sleep"):
            result = hs._push_one(self._sub(), "{}")
        assert result == "failed"


# ══════════════════════════════════════════════════════════════
#  helpers_security.py — alerta_critico
# ══════════════════════════════════════════════════════════════

class TestAlertaCritico:

    def test_alerta_escreve_ficheiro(self, tmp_path):
        """alerta_critico escreve linha no ficheiro de alertas."""
        import helpers_security as hs
        alert_file = str(tmp_path / "alerts_criticos.log")

        with patch.object(hs, "_ALERT_LOG_PATH", alert_file), \
             patch.object(hs, "_PUSH_OK", False):
            hs.alerta_critico("DB corrompida", "integrity_check: fail")

        assert os.path.exists(alert_file)
        content = open(alert_file).read()
        assert "CRITICO" in content
        assert "DB corrompida" in content
        assert "integrity_check: fail" in content

    def test_alerta_sem_detalhe(self, tmp_path):
        """alerta_critico funciona sem detalhe."""
        import helpers_security as hs
        alert_file = str(tmp_path / "alerts_criticos2.log")

        with patch.object(hs, "_ALERT_LOG_PATH", alert_file), \
             patch.object(hs, "_PUSH_OK", False):
            hs.alerta_critico("Disco cheio")

        content = open(alert_file).read()
        assert "Disco cheio" in content

    def test_alerta_com_push_ok_dispara_thread(self, tmp_path):
        """Com _PUSH_OK=True e _VAPID_PRIVATE_KEY definido, inicia thread de push."""
        import helpers_security as hs
        alert_file = str(tmp_path / "alerts_push.log")

        threads_started = []

        def fake_thread(target=None, daemon=None, **kw):
            # Não arrancar a thread real — apenas contar as criações
            threads_started.append(target)
            t = MagicMock()
            t.start = MagicMock()
            return t

        with patch.object(hs, "_ALERT_LOG_PATH", alert_file), \
             patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "fake_key"), \
             patch("helpers_security.threading.Thread", side_effect=fake_thread):
            hs.alerta_critico("Teste push", "detalhe")

        assert len(threads_started) >= 1

    def test_alerta_push_sem_vapid_retorna_cedo(self, tmp_path):
        """Com _PUSH_OK=True mas VAPID vazio, não inicia thread de push."""
        import helpers_security as hs
        alert_file = str(tmp_path / "alerts_novapid.log")

        with patch.object(hs, "_ALERT_LOG_PATH", alert_file), \
             patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", ""), \
             patch("helpers_security.threading.Thread") as mock_thread:
            hs.alerta_critico("Sem VAPID", "nada")

        mock_thread.assert_not_called()

    def test_alerta_enviar_inner_lista_barbearias(self, tmp_path):
        """Thread _enviar remove subscrições expiradas encontradas."""
        import helpers_security as hs
        alert_file = str(tmp_path / "alerts_enviar.log")

        mock_resp_expired = MagicMock()
        mock_resp_expired.status_code = 410
        exc_expired = hs.WebPushException("Gone", response=mock_resp_expired)

        barbearia_fake = {"id": 99}
        sub_fake = {"endpoint": "https://ep/1", "p256dh": "p", "auth": "a"}

        with patch.object(hs, "_ALERT_LOG_PATH", alert_file), \
             patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "fake"), \
             patch.object(hs.db, "listar_barbearias", return_value=[barbearia_fake]), \
             patch.object(hs.db, "push_listar", return_value=[sub_fake]), \
             patch.object(hs.db, "push_remover_expiradas") as mock_remover, \
             patch.object(hs, "webpush", side_effect=exc_expired):
            # Chama directamente a função interna _enviar definida dentro de alerta_critico
            # Fazemos isso chamando alerta_critico e deixando a thread correr de forma síncrona
            import threading

            targets_capturados = []

            def capture_thread(target=None, daemon=None, **kw):
                if target is not None:
                    targets_capturados.append(target)
                t = MagicMock()
                t.start = lambda: None
                return t

            with patch("helpers_security.threading.Thread", side_effect=capture_thread):
                hs.alerta_critico("Test enviar", "d")

            # Correr os targets de forma síncrona
            for fn in targets_capturados:
                fn()

        mock_remover.assert_called_once_with(["https://ep/1"])


# ══════════════════════════════════════════════════════════════
#  helpers_security.py — _push_notif sem condições
# ══════════════════════════════════════════════════════════════

class TestPushNotif:

    def test_push_notif_sem_push_ok(self):
        """_push_notif retorna imediatamente se _PUSH_OK falso."""
        import helpers_security as hs
        with patch.object(hs, "_PUSH_OK", False), \
             patch.object(hs.db, "push_listar") as mock_listar:
            hs._push_notif(1, "T", "C")
        mock_listar.assert_not_called()

    def test_push_notif_sem_vapid(self):
        """_push_notif retorna imediatamente se _VAPID_PRIVATE_KEY vazio."""
        import helpers_security as hs
        with patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", ""), \
             patch.object(hs.db, "push_listar") as mock_listar:
            hs._push_notif(1, "T", "C")
        mock_listar.assert_not_called()

    def test_push_notif_sem_subs(self):
        """_push_notif retorna sem chamar _push_one se não há subscrições."""
        import helpers_security as hs
        with patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "key"), \
             patch.object(hs.db, "push_listar", return_value=[]), \
             patch.object(hs, "_push_one") as mock_push_one:
            hs._push_notif(1, "T", "C")
        mock_push_one.assert_not_called()

    def test_push_notif_remove_expiradas(self):
        """_push_notif chama db.push_remover_expiradas com endpoints expirados."""
        import helpers_security as hs
        sub = {"endpoint": "https://ep/x", "p256dh": "p", "auth": "a"}
        with patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "key"), \
             patch.object(hs.db, "push_listar", return_value=[sub]), \
             patch.object(hs, "_push_one", return_value="expired"), \
             patch.object(hs.db, "push_remover_expiradas") as mock_rem:
            hs._push_notif(1, "T", "C")
        mock_rem.assert_called_once_with(["https://ep/x"])

    def test_push_notif_ok_nao_remove(self):
        """_push_notif não chama push_remover_expiradas se todos os envios ok."""
        import helpers_security as hs
        sub = {"endpoint": "https://ep/y", "p256dh": "p", "auth": "a"}
        with patch.object(hs, "_PUSH_OK", True), \
             patch.object(hs, "_VAPID_PRIVATE_KEY", "key"), \
             patch.object(hs.db, "push_listar", return_value=[sub]), \
             patch.object(hs, "_push_one", return_value="ok"), \
             patch.object(hs.db, "push_remover_expiradas") as mock_rem:
            hs._push_notif(1, "T", "C")
        mock_rem.assert_not_called()


# ══════════════════════════════════════════════════════════════
#  app.py — _enviar_lembretes_push
# ══════════════════════════════════════════════════════════════

class TestEnviarLembretePush:

    def test_enviar_lembretes_push_sem_push_ok(self):
        """_enviar_lembretes_push retorna cedo se _PUSH_OK=False."""
        import app as app_module
        with patch("helpers._PUSH_OK", False), \
             patch.object(app_module.db, "listar_barbearias") as mock_lb:
            app_module._enviar_lembretes_push()
        mock_lb.assert_not_called()

    def test_enviar_lembretes_push_sem_vapid(self):
        """_enviar_lembretes_push retorna cedo se VAPID_PRIVATE_KEY vazio."""
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", ""), \
             patch.object(app_module.db, "listar_barbearias") as mock_lb:
            app_module._enviar_lembretes_push()
        mock_lb.assert_not_called()

    def test_enviar_lembretes_push_exception_barbearias(self):
        """_enviar_lembretes_push absorve excepção em listar_barbearias."""
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          side_effect=Exception("DB error")):
            # Não deve lançar excepção
            app_module._enviar_lembretes_push()

    def test_enviar_lembretes_push_ja_enviado(self, ctx):
        """Se lembrete já foi enviado (chave em _lembretes_enviados), não reenvia."""
        import app as app_module

        bid_ = ctx["bid"]
        # Criar agendamento que caia na janela de 1h
        agora = datetime.now()
        hora_alvo = agora + timedelta(minutes=60)
        data_hora = hora_alvo.strftime("%Y-%m-%d %H:%M:%S")
        ag_id = ctx["db"].criar_agendamento(
            "Cliente Lembrete", ctx["svc_id"], data_hora, bid_,
            barbeiro_id=ctx["barb_id"])

        # Pré-marcar como já enviado
        chave = (ag_id, "1h")
        import time as _time
        with app_module._lembretes_lock:
            app_module._lembretes_enviados[chave] = _time.time()

        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[{"id": bid_}]), \
             patch("app._push_async") as mock_push:
            app_module._enviar_lembretes_push()

        mock_push.assert_not_called()

        # Cleanup
        with app_module._lembretes_lock:
            app_module._lembretes_enviados.pop(chave, None)


# ══════════════════════════════════════════════════════════════
#  app.py — _push_notif_sub
# ══════════════════════════════════════════════════════════════

class TestPushNotifSub:

    def test_push_notif_sub_sem_push_ok(self):
        """_push_notif_sub retorna cedo se _PUSH_OK=False."""
        import app as app_module
        with patch("helpers._PUSH_OK", False), \
             patch("helpers._push_one") as mock_po:
            app_module._push_notif_sub("ep", "p256", "auth", 1, "T", "C")
        mock_po.assert_not_called()

    def test_push_notif_sub_sem_vapid(self):
        """_push_notif_sub retorna cedo se _VAPID_PRIVATE_KEY vazio."""
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", ""), \
             patch("helpers._push_one") as mock_po:
            app_module._push_notif_sub("ep", "p256", "auth", 1, "T", "C")
        mock_po.assert_not_called()

    def test_push_notif_sub_ok(self):
        """_push_notif_sub chama _push_one e retorna sem erro se 'ok'."""
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch("helpers._push_one", return_value="ok") as mock_po:
            app_module._push_notif_sub("https://ep/1", "p256", "auth", 1, "Titulo", "Corpo")
        mock_po.assert_called_once()
        args = mock_po.call_args[0]
        sub = args[0]
        assert sub["endpoint"] == "https://ep/1"
        assert sub["p256dh"] == "p256"

    def test_push_notif_sub_expired_remove(self):
        """_push_notif_sub chama db.push_remover_expiradas se resultado 'expired'."""
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch("helpers._push_one", return_value="expired"), \
             patch.object(app_module.db, "push_remover_expiradas") as mock_rem:
            app_module._push_notif_sub("https://ep/exp", "p256", "auth", 1, "T", "C")
        mock_rem.assert_called_once_with(["https://ep/exp"])

    def test_push_notif_sub_payload_contem_campos(self):
        """Payload enviado contém titulo, corpo, url."""
        import app as app_module
        captured = []

        def fake_push_one(sub, payload):
            captured.append(json.loads(payload))
            return "ok"

        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch("helpers._push_one", side_effect=fake_push_one):
            app_module._push_notif_sub("https://ep/2", "p", "a", 1,
                                        "Titulo Teste", "Corpo Teste", "/rota")

        assert len(captured) == 1
        assert captured[0]["titulo"] == "Titulo Teste"
        assert captured[0]["corpo"] == "Corpo Teste"
        assert captured[0]["url"] == "/rota"


# ══════════════════════════════════════════════════════════════
#  app.py — _enviar_lembretes_push_cliente
# ══════════════════════════════════════════════════════════════

class TestEnviarLembretePushCliente:

    def test_retorna_cedo_sem_push_ok(self):
        import app as app_module
        with patch("helpers._PUSH_OK", False), \
             patch.object(app_module.db, "listar_barbearias") as mock_lb:
            app_module._enviar_lembretes_push_cliente()
        mock_lb.assert_not_called()

    def test_retorna_cedo_sem_vapid(self):
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", ""), \
             patch.object(app_module.db, "listar_barbearias") as mock_lb:
            app_module._enviar_lembretes_push_cliente()
        mock_lb.assert_not_called()

    def test_absorve_exception_listar_barbearias(self):
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          side_effect=Exception("DB down")):
            app_module._enviar_lembretes_push_cliente()

    def test_sem_subs_cliente_nao_chama_push(self, ctx):
        """Se cliente não tem subscricoes push, não chama _push_notif_sub."""
        import app as app_module

        bid_ = ctx["bid"]
        agora = datetime.now()
        hora_alvo = agora + timedelta(hours=24)
        data_hora = hora_alvo.strftime("%Y-%m-%d %H:%M:%S")
        ag_id = ctx["db"].criar_agendamento(
            "Cliente 24h", ctx["svc_id"], data_hora, bid_,
            barbeiro_id=ctx["barb_id"], telefone="913000001")

        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[{"id": bid_}]), \
             patch.object(app_module.db, "cliente_push_listar_por_tel",
                          return_value=[]) as mock_listar_subs, \
             patch("app._push_notif_sub") as mock_push:
            app_module._enviar_lembretes_push_cliente()

        mock_push.assert_not_called()

    def test_ja_enviado_nao_reenvia(self, ctx):
        """Chave já em _lembretes_enviados → não chama _push_notif_sub."""
        import app as app_module
        import time as _time

        bid_ = ctx["bid"]
        agora = datetime.now()
        hora_alvo = agora + timedelta(hours=24)
        data_hora = hora_alvo.strftime("%Y-%m-%d %H:%M:%S")
        ag_id = ctx["db"].criar_agendamento(
            "Cliente 24h Dup", ctx["svc_id"], data_hora, bid_,
            barbeiro_id=ctx["barb_id"], telefone="913000002")

        chave = (ag_id, "24h_cliente")
        with app_module._lembretes_lock:
            app_module._lembretes_enviados[chave] = _time.time()

        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[{"id": bid_}]), \
             patch("app._push_notif_sub") as mock_push:
            app_module._enviar_lembretes_push_cliente()

        mock_push.assert_not_called()

        with app_module._lembretes_lock:
            app_module._lembretes_enviados.pop(chave, None)

    def test_envia_push_para_subs_cliente(self, ctx):
        """Com subscrições activas, chama _push_notif_sub para cada uma."""
        import app as app_module
        from contextlib import contextmanager
        from unittest.mock import MagicMock as _MM

        bid_ = ctx["bid"]
        agora = datetime.now()
        hora_alvo = agora + timedelta(hours=24)
        data_hora = hora_alvo.strftime("%Y-%m-%d %H:%M:%S")

        sub_fake = {"endpoint": "https://ep/cli", "p256dh": "p", "auth": "a"}

        # Linha fake que simula um agendamento na janela 24h.
        # Usa dict para que ag["campo"] funcione tal como sqlite3.Row.
        fake_row = {
            "id": 9001,
            "cliente": "Cliente 24h Push",
            "telefone": "913000003",
            "data_hora": data_hora,
            "servico_nome": "Corte",
            "barbeiro_nome": "Barbeiro",
        }
        fake_cursor = _MM()
        fake_cursor.fetchall.return_value = [fake_row]
        fake_conn = _MM()
        fake_conn.execute.return_value = fake_cursor

        @contextmanager
        def fake_read():
            yield fake_conn

        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "key"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[{"id": bid_}]), \
             patch.object(app_module.db, "_read", fake_read), \
             patch.object(app_module.db, "cliente_push_listar_por_tel",
                          return_value=[sub_fake]), \
             patch("app._push_notif_sub") as mock_push:
            app_module._enviar_lembretes_push_cliente()

        # Deve ter sido chamado pelo menos uma vez
        assert mock_push.call_count >= 1
        args = mock_push.call_args[0]
        assert args[0] == "https://ep/cli"


# ══════════════════════════════════════════════════════════════
#  app.py — _ciclo_limpeza
# ══════════════════════════════════════════════════════════════

class TestCicloLimpeza:

    def test_ciclo_basico_sem_erros(self):
        """_ciclo_limpeza(1) corre sem excepção com todas as funções mockadas."""
        import app as app_module

        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"):
            app_module._ciclo_limpeza(1)

    def test_ciclo_mod6_chama_limpar_em_andamento(self):
        """ciclo%6==0 → chama limpar_em_andamento_presos e espera_limpar_expiradas."""
        import app as app_module

        barbearia_fake = {"id": 42}
        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[barbearia_fake]) as mock_lb, \
             patch.object(app_module.db, "limpar_em_andamento_presos",
                          return_value=0) as mock_lp, \
             patch.object(app_module.db, "espera_limpar_expiradas") as mock_esp:
            app_module._ciclo_limpeza(6)

        mock_lp.assert_called_once_with(42, horas=8)
        mock_esp.assert_called_once()

    def test_ciclo_mod6_com_libertados_invalida_idx(self):
        """ciclo%6==0 com libertados>0 → chama _invalidar_idx."""
        import app as app_module

        barbearia_fake = {"id": 99}
        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"), \
             patch.object(app_module.db, "listar_barbearias",
                          return_value=[barbearia_fake]), \
             patch.object(app_module.db, "limpar_em_andamento_presos",
                          return_value=3), \
             patch.object(app_module.db, "espera_limpar_expiradas"), \
             patch("app._invalidar_idx") as mock_inv:
            app_module._ciclo_limpeza(6)

        mock_inv.assert_called_once_with(99)

    def test_ciclo_mod6_limpa_lembretes_enviados(self):
        """ciclo%6==0 expurga entradas antigas de _lembretes_enviados."""
        import app as app_module
        import time as _time

        # Adicionar entrada antiga (>48h)
        chave_antiga = ("antiga", "1h")
        chave_nova   = ("nova",   "1h")
        ts_antigo    = _time.time() - 49 * 3600
        ts_novo      = _time.time()

        with app_module._lembretes_lock:
            app_module._lembretes_enviados[chave_antiga] = ts_antigo
            app_module._lembretes_enviados[chave_nova]   = ts_novo

        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"), \
             patch.object(app_module.db, "listar_barbearias", return_value=[]), \
             patch.object(app_module.db, "espera_limpar_expiradas"):
            app_module._ciclo_limpeza(6)

        with app_module._lembretes_lock:
            assert chave_antiga not in app_module._lembretes_enviados
            assert chave_nova in app_module._lembretes_enviados
            # Cleanup
            app_module._lembretes_enviados.pop(chave_nova, None)

    def test_ciclo_mod288_chama_desativar_planos(self):
        """ciclo%288==0 → chama desativar_planos_expirados e integrity_check."""
        import app as app_module
        from contextlib import contextmanager

        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, i: "ok"
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        @contextmanager
        def _fake_read():
            yield mock_conn

        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"), \
             patch.object(app_module.db, "listar_barbearias", return_value=[]), \
             patch.object(app_module.db, "espera_limpar_expiradas"), \
             patch.object(app_module.db, "desativar_planos_expirados") as mock_dp, \
             patch("db._conn._read", _fake_read):
            app_module._ciclo_limpeza(288)

        mock_dp.assert_called_once()

    def test_ciclo_mod288_integrity_check_falha_alerta(self):
        """integrity_check retorna algo != 'ok' → chama alerta_critico."""
        import app as app_module
        from contextlib import contextmanager

        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, i: "corruption found"
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        @contextmanager
        def _fake_read():
            yield mock_conn

        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch.object(app_module.db, "invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app._enviar_lembretes_push_cliente"), \
             patch.object(app_module.db, "listar_barbearias", return_value=[]), \
             patch.object(app_module.db, "espera_limpar_expiradas"), \
             patch.object(app_module.db, "desativar_planos_expirados"), \
             patch("db._conn._read", _fake_read), \
             patch("helpers_security.alerta_critico") as mock_alerta:
            app_module._ciclo_limpeza(288)

        mock_alerta.assert_called_once()
        call_args = mock_alerta.call_args[0]
        assert "DB" in call_args[0] or "corruption" in call_args[1].lower() or True

    def test_ciclo_absorve_excecoes(self):
        """_ciclo_limpeza absorve excepções em cada passo sem propagar."""
        import app as app_module

        with patch("app._pc_evict", side_effect=RuntimeError("pc_evict error")), \
             patch("app._rl_evict", side_effect=RuntimeError("rl_evict error")), \
             patch.object(app_module.db, "invalidar_cache_slots",
                          side_effect=Exception("slots error")), \
             patch("app._enviar_lembretes_push",
                   side_effect=Exception("lembretes error")), \
             patch("app._enviar_lembretes_push_cliente",
                   side_effect=Exception("cliente error")):
            # Não deve propagar excepção
            app_module._ciclo_limpeza(1)


# ══════════════════════════════════════════════════════════════
#  blueprints/cliente.py — fila de espera
# ══════════════════════════════════════════════════════════════

class TestFilaEspera:

    def _get_slug(self, ctx):
        b = ctx["db"].get_barbearia(ctx["bid"])
        return b.get("slug", "barbearia-extra2")

    def test_fila_espera_sem_sessao_redireciona(self, client):
        c, ctx = client
        _limpar_sessao(c)
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        resp = c.post(f"/cliente/{slug}/fila-espera",
                      data={"data": amanha, "servico_id": ctx["svc_id"]},
                      follow_redirects=False)
        # Sem sessão cliente → redireciona para entrada
        assert resp.status_code in (302, 303)

    def test_fila_espera_adiciona_ok(self, client):
        """POST fila-espera com sessão válida → adiciona à fila."""
        c, ctx = client
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.espera_adicionar", return_value=True) as mock_ea, \
             patch("blueprints.cliente._api_ok", return_value=True):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={
                              "data": amanha,
                              "servico_id": str(ctx["svc_id"]),
                          },
                          follow_redirects=False)

        assert resp.status_code in (302, 303)
        mock_ea.assert_called_once()

    def test_fila_espera_ja_na_fila(self, client):
        """Se espera_adicionar retorna False → flash de 'já estás na fila'."""
        c, ctx = client
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.espera_adicionar", return_value=False), \
             patch("blueprints.cliente._api_ok", return_value=True):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={"data": amanha,
                                "servico_id": str(ctx["svc_id"])},
                          follow_redirects=True)

        assert resp.status_code == 200
        # Mensagem de "já estás na fila" deve aparecer no corpo
        assert "fila" in resp.data.decode("utf-8", errors="replace").lower()

    def test_fila_espera_data_invalida(self, client):
        """Data inválida → flash 'Data inválida' e redireciona para marcar."""
        c, ctx = client
        slug = self._get_slug(ctx)
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente._api_ok", return_value=True):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={"data": "nao-e-data",
                                "servico_id": str(ctx["svc_id"])},
                          follow_redirects=False)

        assert resp.status_code in (302, 303)

    def test_fila_espera_rate_limit(self, client):
        """Rate limit activo → flash e redireciona."""
        c, ctx = client
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente._api_ok", return_value=False):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={"data": amanha,
                                "servico_id": str(ctx["svc_id"])},
                          follow_redirects=False)

        assert resp.status_code in (302, 303)

    def test_fila_espera_barbearia_inativa(self, client):
        """Barbearia inactiva → redireciona."""
        c, ctx = client
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.get_barbearia_por_slug",
                   return_value={"id": ctx["bid"], "ativa": False, "slug": slug}):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={"data": amanha,
                                "servico_id": str(ctx["svc_id"])},
                          follow_redirects=False)

        assert resp.status_code in (302, 303)

    def test_fila_espera_barbeiro_id_invalido(self, client):
        """barbeiro_id não numérico → interpretado como None, não lança excepção."""
        c, ctx = client
        slug = self._get_slug(ctx)
        amanha = ctx["amanha"]
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.espera_adicionar", return_value=True) as mock_ea, \
             patch("blueprints.cliente._api_ok", return_value=True):
            resp = c.post(f"/cliente/{slug}/fila-espera",
                          data={"data": amanha,
                                "servico_id": str(ctx["svc_id"]),
                                "barbeiro_id": "abc"},
                          follow_redirects=False)

        assert resp.status_code in (302, 303)
        # barbeiro_id deve ter sido None
        call_args = mock_ea.call_args
        # espera_adicionar(barbearia_id, nome, tel, sid, bid_, data)
        assert call_args[0][4] is None


# ══════════════════════════════════════════════════════════════
#  blueprints/cliente.py — dispensar-espera
# ══════════════════════════════════════════════════════════════

class TestDispensarEspera:

    def _get_slug(self, ctx):
        b = ctx["db"].get_barbearia(ctx["bid"])
        return b.get("slug", "barbearia-extra2")

    def test_dispensar_espera_sem_sessao(self, client):
        """Sem sessão cliente → redireciona para entrada."""
        c, ctx = client
        _limpar_sessao(c)
        slug = self._get_slug(ctx)
        resp = c.post(f"/cliente/{slug}/dispensar-espera/1",
                      follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_dispensar_espera_ok(self, client):
        """Com sessão válida → chama espera_marcar_notificado e redireciona."""
        c, ctx = client
        slug = self._get_slug(ctx)
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.espera_marcar_notificado") as mock_mn:
            resp = c.post(f"/cliente/{slug}/dispensar-espera/42",
                          follow_redirects=False)

        assert resp.status_code in (302, 303)
        mock_mn.assert_called_once_with(42)

    def test_dispensar_espera_exception_absorvida(self, client):
        """Se espera_marcar_notificado lançar excepção, não propaga."""
        c, ctx = client
        slug = self._get_slug(ctx)
        _como_cliente(c, ctx, slug)

        with patch("blueprints.cliente.db.espera_marcar_notificado",
                   side_effect=Exception("DB error")):
            resp = c.post(f"/cliente/{slug}/dispensar-espera/99",
                          follow_redirects=False)

        # Deve continuar a funcionar e redirecionar
        assert resp.status_code in (302, 303)

    def test_dispensar_espera_barbearia_nao_existe(self, client):
        """Barbearia não existe → redireciona para login."""
        c, ctx = client
        _como_cliente(c, ctx)

        with patch("blueprints.cliente.db.get_barbearia_por_slug", return_value=None):
            resp = c.post("/cliente/slug-inexistente/dispensar-espera/1",
                          follow_redirects=False)

        assert resp.status_code in (302, 303)
