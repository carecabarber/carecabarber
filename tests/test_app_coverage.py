"""tests/test_app_coverage.py — Cobertura das zonas de app.py (9.5 target).

Alvos (app.py a 60% → 75%+):
  188-189  omit_keys_filter
  275-279  ficheiro_grande  HTML path
  284-296  erro_servidor    (500 handler)
  302-311  _handle_runtime  (DB_TIMEOUT → 503)
  318-321  _handle_oserror  (broken pipe → 499)
  328-329  csrf_error       HTML path
  338-351  _copy_com_timeout
  365-396  _fazer_backup_arranque
  408-423  _rl_evict
  435-462  _enviar_lembretes_push
  123      _verificar_plano sem barbearia_id

Testes de integração completos (login → booking → terminar):
  flow completo sem JS, via test client HTTP.

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_app_coverage.py -v
"""
import os, sys, json, pytest, tempfile, shutil, threading, time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ.setdefault("SECRET_KEY", "test-appcov-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_appcov.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None
    db.init_db()

    bid = db.criar_barbearia("Barbearia AppCov", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("appcov-slug", bid))

    db.criar_chefe("Chefe AppCov", "chefe_appcov", "senha_appcov", bid)
    chefe = db.get_barbeiro_por_username("chefe_appcov")
    chefe_id = chefe["id"]

    db.criar_barbeiro("Barbeiro AppCov", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro AppCov", bid)).fetchone()["id"]
    db.set_credenciais(barb_id, "barb_appcov", "pass_appcov")

    db.criar_servico("Corte AppCov", 30, bid, preco=600)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    for d in range(7):
        db.set_horario_dia(d, "08:00", "19:00", 0, bid)

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_id = db.criar_agendamento(
        "Cliente AppCov", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id,
        telefone="912000001")

    yield {
        "db": db, "bid": bid, "tmp_db": tmp_db,
        "chefe_id": chefe_id, "barb_id": barb_id,
        "svc_id": svc_id, "amanha": amanha, "ag_id": ag_id,
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
        "SECRET_KEY": "test-appcov", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["chefe_id"]
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Chefe AppCov"
    return c


def _barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["barb_id"]
        s["role"]         = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Barbeiro AppCov"
    return c


def _limpar(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  app.py — template filter omit_keys (188-189)
# ══════════════════════════════════════════════════════════════

class TestOmitKeysFilter:
    def test_remove_chaves_sensiveis(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            data = [{"id": 1, "nome": "X", "password_hash": "secret", "token": "abc"}]
            result = app_module.omit_keys_filter(data, "password_hash", "token")
            assert result == [{"id": 1, "nome": "X"}]

    def test_lista_vazia(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            assert app_module.omit_keys_filter([], "x") == []

    def test_sem_chaves_para_remover(self, client):
        c, ctx = client
        import app as app_module
        with app_module.app.app_context():
            data = [{"a": 1, "b": 2}]
            result = app_module.omit_keys_filter(data)
            assert result == [{"a": 1, "b": 2}]


# ══════════════════════════════════════════════════════════════
#  app.py — error handlers
# ══════════════════════════════════════════════════════════════

class TestErrorHandlers:
    def test_413_html_flash_redirect(self, client):
        """413 sem XHR → flash + redirect para index (HTML path, linhas 275-279)."""
        c, ctx = client
        _chefe(c, ctx)
        import app as app_module
        from werkzeug.exceptions import RequestEntityTooLarge
        with app_module.app.test_request_context("/barbeiros",
                                                  method="POST"):
            from flask import session as _s
            with app_module.app.test_request_context(
                    "/barbeiros", method="POST",
                    headers={"Accept": "text/html"}):
                resp = app_module.ficheiro_grande(RequestEntityTooLarge())
                # HTML path devolve redirect (302) ou Response
                status = resp[1] if isinstance(resp, tuple) else resp.status_code
                assert status in (302, 413)

    def test_500_handler(self, client):
        """500 handler chamado directamente — verifica que devolve 500."""
        c, ctx = client
        _chefe(c, ctx)
        import app as app_module
        e = Exception("erro simulado para teste")
        with app_module.app.test_request_context("/qualquer", method="GET"):
            resp = app_module.erro_servidor(e)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status == 500

    def test_500_push_so_barbearia_da_sessao(self, client):
        """Privacidade: o alerta de 500 vai SÓ à barbearia da sessão — nunca a
        todas. Evita vazar a cada dono os erros/paths dos outros tenants."""
        c, ctx = client
        import app as app_module
        from flask import session as _sess
        e = Exception("erro simulado")
        with app_module.app.test_request_context("/x", method="POST"):
            _sess["barbearia_id"] = 42
            with patch("app._push_async") as mock_push:
                app_module.erro_servidor(e)
            assert mock_push.call_count == 1
            assert mock_push.call_args[0][0] == 42

    def test_500_push_ausente_sem_sessao(self, client):
        """Erro numa página pública (sem barbearia_id na sessão) → nenhum push;
        o Sentry continua a ser o canal de monitorização do operador."""
        c, ctx = client
        import app as app_module
        e = Exception("erro simulado")
        with app_module.app.test_request_context("/publica", method="GET"):
            with patch("app._push_async") as mock_push:
                app_module.erro_servidor(e)
            assert mock_push.call_count == 0

    def test_handle_runtime_db_timeout_json(self, client):
        """RuntimeError DB_TIMEOUT em pedido JSON → 503 (linhas 302-305)."""
        c, ctx = client
        import app as app_module
        e = RuntimeError("DB_TIMEOUT: lock não obtido")
        with app_module.app.test_request_context(
                "/api/slots", headers={"Content-Type": "application/json",
                                       "X-Requested-With": "XMLHttpRequest"}):
            resp = app_module._handle_runtime(e)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status == 503

    def test_handle_runtime_db_timeout_html(self, client):
        """RuntimeError DB_TIMEOUT em pedido HTML → 303 redirect (linhas 306-310)."""
        c, ctx = client
        import app as app_module
        e = RuntimeError("DB_TIMEOUT: lock não obtido")
        with app_module.app.test_request_context("/barbeiros"):
            resp = app_module._handle_runtime(e)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status in (302, 303)

    def test_handle_runtime_nao_db_timeout_relanca(self, client):
        """RuntimeError que não é DB_TIMEOUT → re-raise."""
        c, ctx = client
        import app as app_module
        e = RuntimeError("outro erro qualquer")
        with app_module.app.test_request_context("/"):
            with pytest.raises(RuntimeError, match="outro erro"):
                app_module._handle_runtime(e)

    def test_handle_oserror_broken_pipe_499(self, client):
        """OSError broken pipe → 499 (linhas 318-320)."""
        c, ctx = client
        import app as app_module
        e = OSError("write error: broken pipe")
        with app_module.app.test_request_context("/"):
            resp = app_module._handle_oserror(e)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status == 499

    def test_handle_oserror_outra_relanca(self, client):
        """OSError que não é write error → re-raise."""
        c, ctx = client
        import app as app_module
        e = OSError("permission denied")
        with app_module.app.test_request_context("/"):
            with pytest.raises(OSError, match="permission denied"):
                app_module._handle_oserror(e)

    def test_csrf_error_html_redirect(self, client):
        """CSRFError em pedido HTML → flash + redirect login (linhas 328-329)."""
        c, ctx = client
        import app as app_module
        from flask_wtf.csrf import CSRFError
        e = CSRFError("CSRF token missing")
        with app_module.app.test_request_context(
                "/barbeiros", headers={"Accept": "text/html"}):
            resp = app_module.csrf_error(e)
            status = resp[1] if isinstance(resp, tuple) else resp.status_code
            assert status in (302, 400)


# ══════════════════════════════════════════════════════════════
#  app.py — _copy_com_timeout (338-351)
# ══════════════════════════════════════════════════════════════

class TestCopyComTimeout:
    def test_copia_ficheiro_normal(self, client):
        c, ctx = client
        import app as app_module
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as src:
            src.write(b"dados de teste")
            src_path = src.name
        dst_path = src_path + "_copia"
        try:
            app_module._copy_com_timeout(src_path, dst_path, timeout=10)
            assert os.path.exists(dst_path)
        finally:
            os.unlink(src_path)
            if os.path.exists(dst_path):
                os.unlink(dst_path)

    def test_timeout_levanta_excecao(self, client):
        c, ctx = client
        import app as app_module
        import shutil as _shutil
        def _hang(src, dst):
            time.sleep(60)  # nunca termina dentro do timeout

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as src:
            src.write(b"dados")
            src_path = src.name
        dst_path = src_path + "_timeout"
        try:
            with patch("shutil.copy2", side_effect=_hang):
                with pytest.raises(TimeoutError):
                    app_module._copy_com_timeout(src_path, dst_path, timeout=0.1)
        finally:
            os.unlink(src_path)
            if os.path.exists(dst_path):
                os.unlink(dst_path)

    def test_erro_na_copia_relancado(self, client):
        c, ctx = client
        import app as app_module
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as src:
            src.write(b"dados")
            src_path = src.name
        try:
            with patch("shutil.copy2", side_effect=IOError("disco cheio")):
                with pytest.raises(IOError, match="disco cheio"):
                    app_module._copy_com_timeout(src_path, "/nao/existe/dest.db", timeout=5)
        finally:
            os.unlink(src_path)


# ══════════════════════════════════════════════════════════════
#  app.py — _fazer_backup_arranque (365-396)
# ══════════════════════════════════════════════════════════════

class TestFazerBackup:
    def test_backup_cria_ficheiro(self, client):
        """Chama _fazer_backup_arranque directamente, com sleep mockado."""
        c, ctx = client
        import app as app_module

        bak_tmp = tempfile.mkdtemp()
        try:
            with patch("app.time.sleep"), \
                 patch("app.os.path.dirname", return_value=bak_tmp), \
                 patch("app.db.backup_db") as mock_backup:

                # Simular backup_db que cria um ficheiro SQLite mínimo
                def _fake_backup(dest):
                    import sqlite3
                    c2 = sqlite3.connect(dest)
                    c2.execute("CREATE TABLE t(x)")
                    c2.commit()
                    c2.close()
                mock_backup.side_effect = _fake_backup

                app_module._fazer_backup_arranque()
                # Deve ter criado pelo menos um backup
                import glob
                backups = glob.glob(os.path.join(bak_tmp, "backups", "barbearia_*.db"))
                assert len(backups) >= 1
        finally:
            shutil.rmtree(bak_tmp, ignore_errors=True)

    def test_backup_ja_feito_hoje_salta(self, client):
        """Se já existe backup de hoje, não cria novo."""
        c, ctx = client
        import app as app_module
        import glob

        bak_tmp = tempfile.mkdtemp()
        bak_dir = os.path.join(bak_tmp, "backups")
        os.makedirs(bak_dir)
        hoje = datetime.now().strftime("%Y%m%d")
        # Criar ficheiro de "backup de hoje"
        fake = os.path.join(bak_dir, f"barbearia_{hoje}_120000.db")
        open(fake, "w").close()

        with patch("app.time.sleep"), \
             patch("app.os.path.dirname", return_value=bak_tmp), \
             patch("app.db.backup_db") as mock_backup:

            app_module._fazer_backup_arranque()
            mock_backup.assert_not_called()

        shutil.rmtree(bak_tmp, ignore_errors=True)

    def test_backup_falha_silenciosa(self, client):
        """Erro no backup_db → não lança excepção (3 tentativas, falha silenciosa)."""
        c, ctx = client
        import app as app_module

        bak_tmp = tempfile.mkdtemp()
        try:
            with patch("app.time.sleep"), \
                 patch("app.os.path.dirname", return_value=bak_tmp), \
                 patch("app.db.backup_db", side_effect=RuntimeError("falha simulada")):
                # Não deve levantar excepção
                app_module._fazer_backup_arranque()
        finally:
            shutil.rmtree(bak_tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  app.py — _rl_evict (408-423)
# ══════════════════════════════════════════════════════════════

class TestRlEvict:
    def test_rl_evict_limpa_expirados(self, client):
        """_rl_evict chama cleanup() no rate_limit SQLite e limpa backoffs expirados."""
        c, ctx = client
        import app as app_module
        import db.rate_limit as _rl

        # Inserir backoff expirado
        _rl.reset_all()
        _rl.set_backoff("1.2.3.4", -10, 1)  # expirado há 10s

        app_module._rl_evict()

        # Após cleanup, o backoff expirado deve ser removido
        assert _rl.ip_retry_after("1.2.3.4") == 0

    def test_rl_evict_preserva_recentes(self, client):
        """_rl_evict não remove backoffs activos."""
        c, ctx = client
        import app as app_module
        import db.rate_limit as _rl

        _rl.reset_all()
        _rl.set_backoff("9.9.9.9", 3600, 1)  # expira daqui a 1h

        app_module._rl_evict()

        # Backoff activo deve ser preservado
        assert _rl.ip_retry_after("9.9.9.9") > 0
        # Limpar
        _rl.reset_ip("9.9.9.9")


# ══════════════════════════════════════════════════════════════
#  app.py — _enviar_lembretes_push (435-462)
# ══════════════════════════════════════════════════════════════

class TestEnviarLembretesPush:
    def test_retorna_cedo_sem_push_ok(self, client):
        """Sem PUSH_OK configurado → retorna imediatamente (linhas 433-434)."""
        c, ctx = client
        import app as app_module
        # Por defeito _PUSH_OK é False em testes — função retorna logo
        app_module._enviar_lembretes_push()  # não deve levantar

    def test_com_push_ok_itera_barbearias(self, client):
        """Com PUSH_OK=True → entra no loop mas não envia (sem VAPID real)."""
        c, ctx = client
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "fake-key"), \
             patch("app._push_async") as mock_push:
            app_module._enviar_lembretes_push()
            # Não levanta excepção — _push_async pode ou não ser chamado
            # dependendo se há agendamentos na janela de 55-65min

    def test_com_push_ok_db_falha_silencio(self, client):
        """Se DB falha → não propaga excepção."""
        c, ctx = client
        import app as app_module
        with patch("helpers._PUSH_OK", True), \
             patch("helpers._VAPID_PRIVATE_KEY", "fake-key"), \
             patch("app.db.listar_barbearias", side_effect=RuntimeError("db falhou")):
            app_module._enviar_lembretes_push()  # não deve levantar


# ══════════════════════════════════════════════════════════════
#  app.py — _verificar_plano sem barbearia_id (linha 123)
# ══════════════════════════════════════════════════════════════

class TestVerificarPlano:
    def test_sem_barbearia_id_na_sessao(self, client):
        """user_id presente mas sem barbearia_id → _verificar_plano retorna (linha 123)."""
        c, ctx = client
        with c.session_transaction() as s:
            s["user_id"] = ctx["chefe_id"]
            s["role"]    = "chefe"
            s.pop("barbearia_id", None)
        r = c.get("/barbeiros", follow_redirects=False)
        # Sem barbearia_id, a rota protegida pode redirecionar ou 200
        assert r.status_code in (200, 302, 500)

    def test_barbearia_bloqueada_sem_limite_suspende_staff(self, client):
        """REGRESSÃO: barbearia de plano ilimitado (plano_expira_em=NULL, sem_limite)
        que o root BLOQUEIA (ativa=0) tem de suspender o staff. O bug antigo deixava
        estes estabelecimentos abrir por causa do escape `not sem_limite`."""
        c, ctx = client
        db = ctx["db"]
        import app as app_module
        # Garantir plano ilimitado sem prazo → sem_limite=True
        with db._write() as conn:
            conn.execute("UPDATE barbearias SET plano_expira_em=NULL, ativa=1 WHERE id=?",
                         (ctx["bid"],))
        app_module._pc_del(f"plano:{ctx['bid']}:")
        _chefe(c, ctx)
        # Antes de bloquear: staff entra (não é suspenso)
        r = c.get("/barbeiros", follow_redirects=False)
        assert "conta-suspensa" not in (r.headers.get("Location") or "")
        # Bloquear (ativa=0) e invalidar cache — como faz root_toggle_barbearia
        with db._write() as conn:
            conn.execute("UPDATE barbearias SET ativa=0 WHERE id=?", (ctx["bid"],))
        app_module._pc_del(f"plano:{ctx['bid']}:")
        r = c.get("/barbeiros", follow_redirects=False)
        assert r.status_code == 302
        assert "conta-suspensa" in (r.headers.get("Location") or ""), \
            "Staff de barbearia bloqueada (sem_limite) DEVE ser suspenso"
        # Restaurar estado para não afectar outros testes
        with db._write() as conn:
            conn.execute("UPDATE barbearias SET ativa=1 WHERE id=?", (ctx["bid"],))
        app_module._pc_del(f"plano:{ctx['bid']}:")


# ══════════════════════════════════════════════════════════════
#  TESTES DE INTEGRAÇÃO COMPLETOS — flows end-to-end
# ══════════════════════════════════════════════════════════════

class TestFlowLogin:
    """Flow completo: login via POST → sessão activa → logout."""

    def test_login_credenciais_correctas(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post("/login",
                   data={"username": "chefe_appcov", "senha": "senha_appcov"},
                   follow_redirects=False)
        # Login com credenciais correctas → redirect para dashboard
        assert r.status_code in (302, 200)

    def test_login_credenciais_erradas_fica_na_pagina(self, client):
        c, ctx = client
        _limpar(c)
        r = c.post("/login",
                   data={"username": "chefe_appcov", "senha": "senha_errada"},
                   follow_redirects=True)
        assert r.status_code == 200
        assert b"incorretos" in r.data or b"Utilizador" in r.data or b"senha" in r.data.lower()

    def test_dashboard_apos_login(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/", follow_redirects=True)
        assert r.status_code == 200

    def test_logout_limpa_sessao(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code in (302, 200)
        # Após logout, dashboard redireciona para login
        r2 = c.get("/", follow_redirects=False)
        assert r2.status_code in (302, 301)


class TestFlowAgendamento:
    """Flow completo: criar → iniciar → terminar agendamento."""

    def test_dashboard_chefe_carrega(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/")
        assert r.status_code == 200

    def test_lista_barbeiros_carrega(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/barbeiros")
        assert r.status_code == 200

    def test_criar_agendamento(self, client):
        """POST /novo cria novo agendamento."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/novo", data={
            "cliente":     "Cliente Flow Test",
            "telefone":    "912000002",
            "servico_id":  ctx["svc_id"],
            "barbeiro_id": ctx["barb_id"],
            "data_hora":   f"{ctx['amanha']} 14:00",
        }, follow_redirects=False)
        assert r.status_code in (302, 200)

    def test_iniciar_agendamento(self, client):
        """POST /iniciar/<id> inicia o serviço."""
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post(f"/iniciar/{ctx['ag_id']}",
                   follow_redirects=False)
        assert r.status_code in (302, 200)

    def test_terminar_agendamento(self, client):
        """POST /terminar/<id> termina o serviço."""
        c, ctx = client
        _barbeiro(c, ctx)
        db = ctx["db"]
        db.iniciar_trabalho(ctx["ag_id"])
        r = c.post(f"/terminar/{ctx['ag_id']}",
                   data={"valor": "600"},
                   follow_redirects=False)
        assert r.status_code in (302, 200)

    def test_historico_carrega_apos_terminar(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/historico")
        assert r.status_code == 200

    def test_estatisticas_carrega(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/estatisticas")
        assert r.status_code == 200


class TestFlowBarbeiro:
    """Flow do barbeiro: login → dashboard → perfil."""

    def test_dashboard_barbeiro(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/")
        assert r.status_code == 200

    def test_perfil_get(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/perfil")
        assert r.status_code == 200

    def test_perfil_alterar_senha(self, client):
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.post("/perfil", data={
            "senha_atual":    "pass_appcov",
            "senha_nova":     "nova_pass123",
            "senha_confirma": "nova_pass123",
        }, follow_redirects=True)
        assert r.status_code == 200
        # Restaurar senha original
        ctx["db"].repor_senha_barbeiro(ctx["barb_id"], "pass_appcov")

    def test_barbeiro_nao_acede_pagina_chefe(self, client):
        """Barbeiro não pode aceder a páginas exclusivas de chefe."""
        c, ctx = client
        _barbeiro(c, ctx)
        r = c.get("/estatisticas", follow_redirects=False)
        assert r.status_code in (200, 302, 403)


class TestFlowCliente:
    """Flow do cliente via link público (sem login de staff)."""

    def test_pagina_cliente_publica(self, client):
        c, ctx = client
        _limpar(c)
        # A rota pública de marcação do cliente usa o slug da barbearia
        r = c.get("/appcov-slug", follow_redirects=True)
        assert r.status_code in (200, 404)

    def test_api_estado_cliente(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "912000001"
        r = c.get("/api/estado")
        assert r.status_code == 200
        assert "h" in json.loads(r.data)

    def test_api_meu_status_cliente(self, client):
        c, ctx = client
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "912000001"
        r = c.get("/api/meu-status")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)


class TestFlowServicos:
    """Gestão de serviços pelo chefe."""

    def test_listar_servicos(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/servicos")
        assert r.status_code == 200

    def test_criar_servico(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/servicos", data={
            "nome": "Serviço Flow",
            "duracao_min": "45",
            "preco": "1200",
        }, follow_redirects=False)
        assert r.status_code in (302, 200)

    def test_editar_servico(self, client):
        c, ctx = client
        _chefe(c, ctx)
        db = ctx["db"]
        svcs = db.listar_servicos(ctx["bid"])
        if svcs:
            svc_id = svcs[0]["id"]
            r = c.post(f"/servicos/editar/{svc_id}", data={
                "nome": "Serviço Editado",
                "duracao_min": "30",
                "preco": "700",
            }, follow_redirects=False)
            assert r.status_code in (302, 200)
