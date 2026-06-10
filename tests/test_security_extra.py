"""
tests/test_security_extra.py — Cobertura extra para helpers_security.py

Alvos (linhas em falta):
  56-76  : _push_notif() — subs vazio, sucesso, WebPushException 404/410, Exception genérica, invalidas
  99     : _JsonFormatter.format() com exc_info
  140-141: _log() fora de request context → ip = path = "?"
  173    : _ip_ok() — backoff activo (now < ate) → return False
  180    : _ip_ok() — backoff expirado → del _ip_backoff[ip]
  195    : _ip_retry_after() com backoff activo
  205    : _user_locked() com username bloqueado
  255-274: _salvar_logo() — extensão inválida, magic errado, sucesso
  291    : _validar_imagem() — JPEG válido
  307    : staff_required — role=root → redirect root_dashboard
  309-317: staff_required — role=cliente com barbearia válida → redirect cliente_home
  319-320: staff_required — role=chefe sem barbearia_id → redirect login
  354-355: pode_gerir_agendamento() — barbearia_id diferente → IDOR block

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_security_extra.py -v --tb=short
"""
import os
import sys
import time
import logging
import tempfile
import shutil
from io import BytesIO
from unittest.mock import patch, MagicMock

os.environ.setdefault("SECRET_KEY", "test-sec-extra-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import helpers_security as hs


# ══════════════════════════════════════════════════════════════
#  FIXTURE — App + DB temporária
# ══════════════════════════════════════════════════════════════

import pytest


@pytest.fixture(scope="module")
def app_ctx():
    """Flask app com DB temporária para testes que precisam de contexto."""
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_sec_extra.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid = db.criar_barbearia("Barbearia SecExtra", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("sec-extra-slug", bid))

    # Chefe
    db.criar_chefe("Chefe SecExtra", "chefe_secextra", "senha_sec", bid)
    chefe = db.get_barbeiro_por_username("chefe_secextra")
    chefe_id = chefe["id"]

    # Barbeiro
    db.criar_barbeiro("Barb SecExtra", bid)
    with db._read() as c:
        barb = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barb SecExtra", bid)).fetchone()
    barb_id = barb["id"]
    db.set_credenciais(barb_id, "barb_secextra", "pass_sec")

    import app as app_module
    flask_app = app_module.app
    flask_app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-sec-extra-key",
    })

    yield {
        "app": flask_app,
        "db": db,
        "bid": bid,
        "chefe_id": chefe_id,
        "barb_id": barb_id,
        "slug": "sec-extra-slug",
    }

    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    _db_conn._CONN   = None
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  _push_notif (linhas 56-76)
# ══════════════════════════════════════════════════════════════

class TestPushNotif:
    """Testa _push_notif com _PUSH_OK=True simulado."""

    def _make_sub(self, endpoint="https://push.example.com/sub1"):
        return {"endpoint": endpoint, "p256dh": "dh_key", "auth": "auth_key"}

    def test_push_subs_vazios_retorna_cedo(self):
        """Quando subs=[] retorna imediatamente (linha 57-58)."""
        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[]):
            # Não deve lançar excepção
            hs._push_notif(1, "Titulo", "Corpo")

    def test_push_ok_sem_excepcao(self):
        """webpush bem-sucedido — sem invalidas (loop completo, linha 61-69)."""
        sub = self._make_sub()
        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[sub]), \
             patch('helpers_security.webpush') as mock_wp, \
             patch('helpers_security.db.push_remover_expiradas') as mock_rem:
            mock_wp.return_value = None
            hs._push_notif(1, "Titulo", "Corpo")
            assert mock_wp.called
            mock_rem.assert_not_called()

    def test_push_webpush_exception_404(self):
        """WebPushException com status 404 → adiciona a invalidas (linha 70-72)."""
        sub = self._make_sub()
        exc = MagicMock()
        exc.response = MagicMock()
        exc.response.status_code = 404

        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[sub]), \
             patch('helpers_security.webpush', side_effect=hs.WebPushException("err", response=exc.response)), \
             patch('helpers_security.db.push_remover_expiradas') as mock_rem:
            # Precisamos de importar WebPushException real ou usar o mock
            hs._push_notif(1, "Titulo", "Corpo")
            mock_rem.assert_called_once()

    def test_push_webpush_exception_410(self):
        """WebPushException com status 410 → adiciona a invalidas (linha 70-72)."""
        sub = self._make_sub(endpoint="https://push.example.com/sub2")
        exc_response = MagicMock()
        exc_response.status_code = 410

        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[sub]), \
             patch('helpers_security.webpush', side_effect=hs.WebPushException("err", response=exc_response)), \
             patch('helpers_security.db.push_remover_expiradas') as mock_rem:
            hs._push_notif(1, "Titulo", "Corpo")
            mock_rem.assert_called_once()

    def test_push_generic_exception_ignorada(self):
        """Excepção genérica é ignorada silenciosamente (linha 73-74)."""
        sub = self._make_sub(endpoint="https://push.example.com/sub3")
        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[sub]), \
             patch('helpers_security.webpush', side_effect=RuntimeError("network error")), \
             patch('helpers_security.db.push_remover_expiradas') as mock_rem:
            hs._push_notif(1, "Titulo", "Corpo")
            mock_rem.assert_not_called()

    def test_push_invalidas_chama_remover(self):
        """invalidas não vazio → chama push_remover_expiradas (linha 75-76)."""
        sub1 = self._make_sub(endpoint="https://push.example.com/gone1")
        sub2 = self._make_sub(endpoint="https://push.example.com/gone2")
        exc_response = MagicMock()
        exc_response.status_code = 410

        with patch.object(hs, '_PUSH_OK', True), \
             patch.object(hs, '_VAPID_PRIVATE_KEY', 'fake-key'), \
             patch('helpers_security.db.push_listar', return_value=[sub1, sub2]), \
             patch('helpers_security.webpush', side_effect=hs.WebPushException("err", response=exc_response)), \
             patch('helpers_security.db.push_remover_expiradas') as mock_rem:
            hs._push_notif(1, "Titulo", "Corpo")
            mock_rem.assert_called_once()
            endpoints_passados = mock_rem.call_args[0][0]
            assert "https://push.example.com/gone1" in endpoints_passados
            assert "https://push.example.com/gone2" in endpoints_passados


# ══════════════════════════════════════════════════════════════
#  _JsonFormatter (linha 99)
# ══════════════════════════════════════════════════════════════

class TestJsonFormatter:
    def test_format_com_exc_info(self):
        """exc_info presente → payload["exc"] preenchido (linha 99)."""
        import json as _json
        formatter = hs._JsonFormatter()
        logger = logging.getLogger("test_json_fmt")
        try:
            raise ValueError("test error")
        except ValueError:
            import sys as _sys
            exc_info = _sys.exc_info()

        record = logging.LogRecord(
            name="test_json_fmt",
            level=logging.ERROR,
            pathname=__file__,
            lineno=0,
            msg="test with exc",
            args=(),
            exc_info=exc_info,
        )
        result = formatter.format(record)
        payload = _json.loads(result)
        assert "exc" in payload
        assert "ValueError" in payload["exc"]


# ══════════════════════════════════════════════════════════════
#  _log() fora de request context (linhas 140-141)
# ══════════════════════════════════════════════════════════════

class TestLogOutsideContext:
    def test_log_sem_request_context(self):
        """_log() fora de contexto Flask → ip = path = "?" sem crash (linha 140-141)."""
        # Chamada directa sem app context activo
        # Não deve lançar excepção
        hs._log("mensagem de teste fora de contexto")


# ══════════════════════════════════════════════════════════════
#  _ip_ok() (linhas 173, 180)
# ══════════════════════════════════════════════════════════════

class TestIpOk:
    def setup_method(self):
        # Limpar estado entre testes
        hs._ip_backoff.clear()
        hs._ip_attempts.clear()

    def test_backoff_activo_retorna_false(self):
        """IP com backoff futuro → retorna False (linha 173)."""
        test_ip = "10.0.0.1"
        hs._ip_backoff[test_ip] = (time.time() + 3600, 1)
        result = hs._ip_ok(test_ip)
        assert result is False
        assert test_ip in hs._ip_backoff  # não foi apagado

    def test_backoff_expirado_remove_entrada(self):
        """IP com backoff expirado → remove entrada (linha 180) e continua."""
        test_ip = "10.0.0.2"
        hs._ip_backoff[test_ip] = (time.time() - 1, 1)
        result = hs._ip_ok(test_ip)
        assert test_ip not in hs._ip_backoff
        assert result is True  # sem tentativas anteriores → ok

    def teardown_method(self):
        hs._ip_backoff.clear()
        hs._ip_attempts.clear()


# ══════════════════════════════════════════════════════════════
#  _ip_retry_after() (linha 195)
# ══════════════════════════════════════════════════════════════

class TestIpRetryAfter:
    def setup_method(self):
        hs._ip_backoff.clear()

    def test_retry_after_com_backoff(self):
        """IP em backoff → retorna segundos restantes (linha 195)."""
        test_ip = "10.0.0.3"
        future = time.time() + 300
        hs._ip_backoff[test_ip] = (future, 1)
        result = hs._ip_retry_after(test_ip)
        assert result > 0
        assert result <= 300

    def test_retry_after_sem_backoff(self):
        """IP sem backoff → retorna 0."""
        result = hs._ip_retry_after("10.0.0.99")
        assert result == 0

    def teardown_method(self):
        hs._ip_backoff.clear()


# ══════════════════════════════════════════════════════════════
#  _user_locked() (linha 205)
# ══════════════════════════════════════════════════════════════

class TestUserLocked:
    def setup_method(self):
        hs._user_fails.clear()

    def test_username_bloqueado(self):
        """Username com >= _USER_MAX falhas recentes → True (linha 205)."""
        username = "hacker_test"
        now = time.time()
        # Inserir _USER_MAX falhas recentes
        for _ in range(hs._USER_MAX):
            hs._user_fails[username].append(now)
        result = hs._user_locked(username)
        assert result is True

    def test_username_desbloqueado(self):
        """Username sem falhas → False."""
        result = hs._user_locked("utilizador_limpo")
        assert result is False

    def test_falhas_expiradas_desbloqueiam(self):
        """Falhas fora da janela de lockout → desbloqueado."""
        username = "antigo_hacker"
        old_time = time.time() - hs._USER_LOCKOUT - 1
        for _ in range(hs._USER_MAX + 1):
            hs._user_fails[username].append(old_time)
        result = hs._user_locked(username)
        assert result is False

    def teardown_method(self):
        hs._user_fails.clear()


# ══════════════════════════════════════════════════════════════
#  _salvar_logo() (linhas 255-274)
# ══════════════════════════════════════════════════════════════

class TestSalvarLogo:
    def test_extensao_invalida_retorna_none(self):
        """Extensão não permitida → None (linha 258-259)."""
        from werkzeug.datastructures import FileStorage
        f = FileStorage(stream=BytesIO(b'xxx'), filename='test.bmp',
                        content_type='image/bmp')
        result = hs._salvar_logo(f, 99)
        assert result is None

    def test_magic_bytes_invalidos_retorna_none(self):
        """Magic bytes inválidos → None (linha 262-264)."""
        from werkzeug.datastructures import FileStorage
        bad_bytes = b'BADMAGIC' + b'\x00' * 20
        f = FileStorage(stream=BytesIO(bad_bytes), filename='test.jpg',
                        content_type='image/jpeg')
        result = hs._salvar_logo(f, 99)
        assert result is None

    def test_sem_ficheiro_retorna_none(self):
        """file=None → None (linha 255-256)."""
        result = hs._salvar_logo(None, 99)
        assert result is None

    def test_filename_vazio_retorna_none(self):
        """filename vazio → None."""
        from werkzeug.datastructures import FileStorage
        f = FileStorage(stream=BytesIO(b'\xff\xd8\xff' + b'\x00' * 20),
                        filename='', content_type='image/jpeg')
        result = hs._salvar_logo(f, 99)
        assert result is None

    def test_jpeg_valido_retorna_filename(self):
        """JPEG com magic bytes corretos e ext válida → filename (linhas 265-274)."""
        from werkzeug.datastructures import FileStorage
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * 20
        f = FileStorage(stream=BytesIO(jpeg_bytes), filename='test.jpg',
                        content_type='image/jpeg')
        with patch('helpers_security.os.makedirs'), \
             patch('helpers_security.os.listdir', return_value=[]), \
             patch.object(f, 'save') as mock_save:
            result = hs._salvar_logo(f, 99)
            assert result == 'logo_99.jpg'
            assert mock_save.called

    def test_png_valido_remove_logo_antigo(self):
        """PNG válido → remove ficheiro anterior e guarda novo (linha 266-274)."""
        from werkzeug.datastructures import FileStorage
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
        f = FileStorage(stream=BytesIO(png_bytes), filename='logo.png',
                        content_type='image/png')
        with patch('helpers_security.os.makedirs'), \
             patch('helpers_security.os.listdir', return_value=['logo_77.jpg']), \
             patch('helpers_security.os.remove') as mock_remove, \
             patch.object(f, 'save'):
            result = hs._salvar_logo(f, 77)
            assert result == 'logo_77.png'
            mock_remove.assert_called_once()

    def test_remove_oserror_ignorado(self):
        """OSError ao remover logo antigo é ignorado (linha 268-271)."""
        from werkzeug.datastructures import FileStorage
        jpeg_bytes = b'\xff\xd8\xff' + b'\x00' * 20
        f = FileStorage(stream=BytesIO(jpeg_bytes), filename='foto.jpg',
                        content_type='image/jpeg')
        with patch('helpers_security.os.makedirs'), \
             patch('helpers_security.os.listdir', return_value=['logo_88.jpg']), \
             patch('helpers_security.os.remove', side_effect=OSError("perm denied")), \
             patch.object(f, 'save') as mock_save:
            result = hs._salvar_logo(f, 88)
            assert result == 'logo_88.jpg'
            assert mock_save.called


# ══════════════════════════════════════════════════════════════
#  _validar_imagem() (linha 291)
# ══════════════════════════════════════════════════════════════

class TestValidarImagem:
    def test_jpeg_valido(self):
        """JPEG com magic bytes corretos e MIME correto → True (linha 291)."""
        jpeg = b'\xff\xd8\xff' + b'\x00' * 5
        assert hs._validar_imagem(jpeg, "image/jpeg") is True

    def test_jpeg_mime_errado(self):
        """JPEG com MIME errado → False."""
        jpeg = b'\xff\xd8\xff' + b'\x00' * 5
        assert hs._validar_imagem(jpeg, "image/png") is False

    def test_png_valido(self):
        """PNG com magic bytes corretos e MIME correto → True."""
        png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 5
        assert hs._validar_imagem(png, "image/png") is True

    def test_webp_valido(self):
        """WEBP com magic bytes corretos e MIME correto → True."""
        webp = b'RIFF' + b'\x00' * 4 + b'WEBP' + b'\x00' * 5
        assert hs._validar_imagem(webp, "image/webp") is True

    def test_webp_riff_sem_webp(self):
        """RIFF mas não WEBP → False (continua loop)."""
        riff_not_webp = b'RIFF' + b'\x00' * 4 + b'WAVE' + b'\x00' * 5
        assert hs._validar_imagem(riff_not_webp, "image/webp") is False

    def test_bytes_invalidos(self):
        """Bytes sem magic conhecido → False."""
        assert hs._validar_imagem(b'RANDOM_DATA', "image/jpeg") is False


# ══════════════════════════════════════════════════════════════
#  staff_required (linhas 307, 309-317, 319-320)
# ══════════════════════════════════════════════════════════════

class TestStaffRequired:
    def test_role_root_redirect_root_dashboard(self, app_ctx):
        """role=root → redirect para root_dashboard (linha 307)."""
        flask_app = app_ctx["app"]
        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 1
                s["role"] = "root"
            # Qualquer rota protegida por staff_required (/ é o index)
            r = c.get("/")
            assert r.status_code == 302
            assert "root" in r.location.lower() or "root_dashboard" in r.location.lower() or "/root" in r.location

    def test_role_cliente_redirect_cliente_home(self, app_ctx):
        """role=cliente com barbearia válida → redirect para cliente_home (linhas 309-317)."""
        flask_app = app_ctx["app"]
        bid = app_ctx["bid"]
        slug = app_ctx["slug"]
        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 999
                s["role"] = "cliente"
                s["barbearia_id"] = bid
            r = c.get("/")
            assert r.status_code == 302
            assert slug in r.location

    def test_role_chefe_sem_barbearia_id_redirect_login(self, app_ctx):
        """role=chefe sem barbearia_id → redirect login (linhas 319-320)."""
        flask_app = app_ctx["app"]
        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 999
                s["role"] = "chefe"
                # sem barbearia_id
            r = c.get("/")
            assert r.status_code == 302
            assert "login" in r.location.lower()

    def test_role_barbeiro_sem_barbearia_id_redirect_login(self, app_ctx):
        """role=barbeiro sem barbearia_id → redirect login (linhas 319-320)."""
        flask_app = app_ctx["app"]
        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 999
                s["role"] = "barbeiro"
                # sem barbearia_id
            r = c.get("/")
            assert r.status_code == 302
            assert "login" in r.location.lower()

    def test_role_cliente_barbearia_sem_slug_redirect_login(self, app_ctx):
        """role=cliente mas barbearia sem slug → redirect login (linhas 314-316)."""
        flask_app = app_ctx["app"]
        db = app_ctx["db"]

        # Criar barbearia sem slug
        bid2 = db.criar_barbearia("Barbearia SemSlug", tipo="barbearia")
        db.registar_pagamento(bid2, "exp")
        with db._write() as c2:
            c2.execute("UPDATE barbearias SET slug=NULL WHERE id=?", (bid2,))

        with flask_app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 888
                s["role"] = "cliente"
                s["barbearia_id"] = bid2
            r = c.get("/")
            assert r.status_code == 302
            assert "login" in r.location.lower()


# ══════════════════════════════════════════════════════════════
#  pode_gerir_agendamento() (linhas 354-355)
# ══════════════════════════════════════════════════════════════

class TestPodeGerirAgendamento:
    def test_barbearia_diferente_idor_block(self, app_ctx):
        """ag.barbearia_id != bid() → IDOR block, retorna False (linhas 354-355)."""
        flask_app = app_ctx["app"]
        bid = app_ctx["bid"]
        barb_id = app_ctx["barb_id"]

        ag = {"id": 1, "barbearia_id": bid + 9999, "barbeiro_id": barb_id}

        with flask_app.test_request_context("/"):
            from flask import session as _session
            _session["barbearia_id"] = bid
            _session["role"] = "barbeiro"
            _session["user_id"] = barb_id
            result = hs.pode_gerir_agendamento(ag)
            assert result is False

    def test_ag_none_retorna_false(self, app_ctx):
        """ag=None → retorna False."""
        flask_app = app_ctx["app"]
        bid = app_ctx["bid"]

        with flask_app.test_request_context("/"):
            from flask import session as _session
            _session["barbearia_id"] = bid
            _session["role"] = "barbeiro"
            _session["user_id"] = 1
            result = hs.pode_gerir_agendamento(None)
            assert result is False

    def test_chefe_pode_gerir_qualquer_ag(self, app_ctx):
        """role=chefe com barbearia correcta → True."""
        flask_app = app_ctx["app"]
        bid = app_ctx["bid"]
        chefe_id = app_ctx["chefe_id"]

        ag = {"id": 1, "barbearia_id": bid, "barbeiro_id": 999}

        with flask_app.test_request_context("/"):
            from flask import session as _session
            _session["barbearia_id"] = bid
            _session["role"] = "chefe"
            _session["user_id"] = chefe_id
            result = hs.pode_gerir_agendamento(ag)
            assert result is True

    def test_barbeiro_pode_gerir_proprio_ag(self, app_ctx):
        """role=barbeiro com barbeiro_id correcto → True."""
        flask_app = app_ctx["app"]
        bid = app_ctx["bid"]
        barb_id = app_ctx["barb_id"]

        ag = {"id": 2, "barbearia_id": bid, "barbeiro_id": barb_id}

        with flask_app.test_request_context("/"):
            from flask import session as _session
            _session["barbearia_id"] = bid
            _session["role"] = "barbeiro"
            _session["user_id"] = barb_id
            result = hs.pode_gerir_agendamento(ag)
            assert result is True
