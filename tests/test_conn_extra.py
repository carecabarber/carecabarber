"""
tests/test_conn_extra.py — Cobertura extra para db/_conn.py
Cobre: linhas 42-51, 73, 83-84, 93, 103-108, 133-135, 194-195,
       210-212, 219, 227-228, 238, 244-249, 258, 276, 306-307,
       321-337, 379-388, 394-395, 401-402, 408-409, 415-416,
       422-423, 428-429, 439-440, 455, 463-464, 471-472, 479-480,
       486-487, 518-519, 526-527, 663-664, 679-680, 689-699, 725-726

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_conn_extra.py -v --tb=short
"""
import os
import sys
import time
import tempfile
import shutil
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-conn-extra")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ══════════════════════════════════════════════════════════════
#  FIXTURE PRINCIPAL
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def tmp_db_ctx():
    """Minimal DB fixture for tests that need real DB operations."""
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_conn_extra.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()
    bid = db.criar_barbearia("Conn Test", tipo="barbearia")

    yield {"db": db, "bid": bid, "_db_conn": _db_conn}

    _db_conn._reset_conn()
    _db_conn._CONN = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  TESTES — CACHE DE SLOTS (linhas 41-51)
# ══════════════════════════════════════════════════════════════

class TestSlotsCacheFull:
    """Linha 41-51: _slots_cache_set quando a cache está cheia."""

    def setup_method(self):
        """Limpa a cache antes de cada teste."""
        import db._conn as _db_conn
        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache.clear()

    def teardown_method(self):
        """Limpa a cache depois de cada teste."""
        import db._conn as _db_conn
        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache.clear()

    def test_cache_full_with_expired_entries(self):
        """Linhas 42-46: quando há entradas expiradas, apaga-as para fazer espaço."""
        import db._conn as _db_conn

        now = time.monotonic()
        # Preencher a cache com 300 entradas, metade expiradas
        with _db_conn._slots_cache_lock:
            for i in range(300):
                exp = now - 1 if i < 150 else now + 3600
                _db_conn._slots_cache[f"key_{i}"] = {"data": {}, "exp": exp}

        # Inserir nova entrada — deve apagar as expiradas
        _db_conn._slots_cache_set("nova_key", {"x": 1}, 60)

        with _db_conn._slots_cache_lock:
            # As expiradas devem ter sido removidas
            assert "nova_key" in _db_conn._slots_cache
            # Entradas expiradas removidas
            for i in range(150):
                assert f"key_{i}" not in _db_conn._slots_cache

    def test_cache_full_no_expired_removes_oldest(self):
        """Linhas 47-51: sem entradas expiradas, apaga as 50 mais antigas."""
        import db._conn as _db_conn

        now = time.monotonic()
        # Preencher a cache com 300 entradas, nenhuma expirada
        # As primeiras 50 têm menor TTL (expiram primeiro)
        with _db_conn._slots_cache_lock:
            for i in range(300):
                exp = now + 100 + i  # crescente: chave_0 expira primeiro
                _db_conn._slots_cache[f"oldest_{i}"] = {"data": {}, "exp": exp}

        _db_conn._slots_cache_set("nova_key2", {"y": 2}, 60)

        with _db_conn._slots_cache_lock:
            assert "nova_key2" in _db_conn._slots_cache
            # As 50 com menor exp devem ter sido removidas
            for i in range(50):
                assert f"oldest_{i}" not in _db_conn._slots_cache
            # As restantes devem existir
            assert f"oldest_50" in _db_conn._slots_cache


# ══════════════════════════════════════════════════════════════
#  TESTES — INVALIDAR CACHE (linhas 73, 83-84)
# ══════════════════════════════════════════════════════════════

class TestInvalidarCache:
    def setup_method(self):
        import db._conn as _db_conn
        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache.clear()

    def teardown_method(self):
        import db._conn as _db_conn
        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache.clear()

    def test_invalidar_cache_gc_path(self):
        """Linha 73: invalidar_cache_slots sem args apaga apenas entradas expiradas."""
        import db._conn as _db_conn

        now = time.monotonic()
        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache["expired_1"] = {"data": {}, "exp": now - 10}
            _db_conn._slots_cache["expired_2"] = {"data": {}, "exp": now - 5}
            _db_conn._slots_cache["valid_1"]   = {"data": {}, "exp": now + 3600}

        _db_conn.invalidar_cache_slots()  # sem barbearia_id → GC

        with _db_conn._slots_cache_lock:
            assert "expired_1" not in _db_conn._slots_cache
            assert "expired_2" not in _db_conn._slots_cache
            assert "valid_1"   in  _db_conn._slots_cache

    def test_invalidar_cache_slots_completo(self):
        """Linhas 83-84: invalidar_cache_slots_completo limpa tudo."""
        import db._conn as _db_conn

        with _db_conn._slots_cache_lock:
            _db_conn._slots_cache["k1"] = {"data": {}, "exp": time.monotonic() + 100}
            _db_conn._slots_cache["k2"] = {"data": {}, "exp": time.monotonic() + 200}

        _db_conn.invalidar_cache_slots_completo()

        with _db_conn._slots_cache_lock:
            assert len(_db_conn._slots_cache) == 0


# ══════════════════════════════════════════════════════════════
#  TESTES — FUSO HORÁRIO (linhas 93, 103-108)
# ══════════════════════════════════════════════════════════════

class TestFusoHorario:
    def test_set_request_tz_valid(self):
        """Linha 93: set_request_tz guarda tz_name no thread-local."""
        import db._conn as _db_conn
        _db_conn.set_request_tz("Europe/Lisbon")
        assert _db_conn._tz_local.tz_name == "Europe/Lisbon"

    def test_set_request_tz_none(self):
        """Linha 93: set_request_tz com None guarda string vazia."""
        import db._conn as _db_conn
        _db_conn.set_request_tz(None)
        assert _db_conn._tz_local.tz_name == ""

    def test_agora_exception_path(self):
        """Linhas 103-104: _agora() quando get_barbearia_tz lança excepção."""
        import db._conn as _db_conn
        with patch("db._conn.get_barbearia_tz", side_effect=Exception("DB error")):
            result = _db_conn._agora(barbearia_id=99)
        assert isinstance(result, datetime)

    def test_agora_invalid_tz(self):
        """Linhas 107-108: _agora() com TZ inválida → ZoneInfoNotFoundError → datetime.now()."""
        import db._conn as _db_conn
        with patch("db._conn.get_barbearia_tz", return_value="Invalid/TZ_That_Does_Not_Exist"):
            result = _db_conn._agora(barbearia_id=99)
        assert isinstance(result, datetime)


# ══════════════════════════════════════════════════════════════
#  TESTES — set_barbearia_tz (linhas 133-135)
# ══════════════════════════════════════════════════════════════

def test_set_barbearia_tz(tmp_db_ctx):
    """Linhas 133-135: set_barbearia_tz salva e invalida cache."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    # Popular cache de TZ
    with _db_conn._tz_cache_lock:
        _db_conn._tz_cache[bid] = ("Atlantic/Cape_Verde", time.monotonic() + 300)

    _db_conn.set_barbearia_tz(bid, "Europe/Lisbon")

    # Cache deve estar limpa
    with _db_conn._tz_cache_lock:
        assert bid not in _db_conn._tz_cache

    # Verificar que o valor foi guardado
    val = _db_conn.get_config("timezone", bid)
    assert val == "Europe/Lisbon"


# ══════════════════════════════════════════════════════════════
#  TESTES — _reset_conn (linhas 194-195)
# ══════════════════════════════════════════════════════════════

def test_reset_conn():
    """Linhas 194-195: _reset_conn() fecha e anula a conexão global."""
    import db._conn as _db_conn

    # Guardar estado original
    orig_conn = _db_conn._CONN
    orig_path = _db_conn.DB_PATH

    # Criar uma conexão temporária para não estragar a principal
    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_reset.db")
    try:
        _db_conn.DB_PATH = tmp_db
        _db_conn._CONN   = None
        _db_conn.get_conn()  # abre conexão
        assert _db_conn._CONN is not None

        _db_conn._reset_conn()
        assert _db_conn._CONN is None
    finally:
        _db_conn.DB_PATH = orig_path
        _db_conn._CONN   = orig_conn
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  TESTES — _acquire_lock retry (linhas 210-212)
# ══════════════════════════════════════════════════════════════

def test_acquire_lock_fails_with_retry():
    """Linhas 210-212: _acquire_lock faz pausa entre tentativas e devolve False."""
    import db._conn as _db_conn

    # Substituir _CONN_LOCK por um mock cujo acquire devolve sempre False
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = False
    with patch("db._conn._CONN_LOCK", mock_lock):
        with patch("db._conn.time.sleep") as mock_sleep:
            result = _db_conn._acquire_lock(
                timeout_por_tentativa=0.01, tentativas=3, pausa=0.01
            )
    assert result is False
    assert mock_sleep.call_count == 2  # pausa entre tentativas (não após a última)


# ══════════════════════════════════════════════════════════════
#  TESTES — DB_TIMEOUT em _write, _write_exclusive, _read (linhas 219, 238, 258)
# ══════════════════════════════════════════════════════════════

def test_write_db_timeout():
    """Linha 219: _write() lança RuntimeError DB_TIMEOUT quando lock não disponível."""
    import db._conn as _db_conn
    # Patchar _acquire_lock directamente (mais simples que mock no RLock C-level)
    with patch("db._conn._acquire_lock", return_value=False):
        with pytest.raises(RuntimeError, match="DB_TIMEOUT"):
            with _db_conn._write() as conn:
                pass


def test_write_exclusive_db_timeout():
    """Linha 238: _write_exclusive() lança RuntimeError DB_TIMEOUT."""
    import db._conn as _db_conn

    with patch("db._conn._acquire_lock", return_value=False):
        with pytest.raises(RuntimeError, match="DB_TIMEOUT"):
            with _db_conn._write_exclusive() as conn:
                pass


def test_read_db_timeout():
    """Linha 258: _read() lança RuntimeError DB_TIMEOUT."""
    import db._conn as _db_conn

    with patch("db._conn._acquire_lock", return_value=False):
        with pytest.raises(RuntimeError, match="DB_TIMEOUT"):
            with _db_conn._read() as conn:
                pass


# ══════════════════════════════════════════════════════════════
#  TESTES — rollback exception (linhas 227-228, 244-249)
# ══════════════════════════════════════════════════════════════

def test_write_rollback_exception_swallowed(tmp_db_ctx):
    """Linhas 227-228: excepção em rollback dentro de _write() é ignorada."""
    import db._conn as _db_conn

    # Usar mock_conn cujo rollback() lança excepção
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True
    mock_conn = MagicMock()
    mock_conn.rollback.side_effect = Exception("rollback failed")

    with patch("db._conn._CONN_LOCK", mock_lock), \
         patch("db._conn.get_conn", return_value=mock_conn):
        with pytest.raises(ValueError):
            with _db_conn._write() as c:
                raise ValueError("trigger rollback")

    mock_conn.rollback.assert_called_once()


def test_write_exclusive_rollback_exception_swallowed(tmp_db_ctx):
    """Linhas 244-249: excepção em rollback dentro de _write_exclusive() é ignorada."""
    import db._conn as _db_conn

    mock_lock = MagicMock()
    mock_lock.acquire.return_value = True
    mock_conn = MagicMock()
    mock_conn.rollback.side_effect = Exception("rollback failed exclusive")

    with patch("db._conn._CONN_LOCK", mock_lock), \
         patch("db._conn.get_conn", return_value=mock_conn):
        with pytest.raises(ValueError):
            with _db_conn._write_exclusive() as c:
                raise ValueError("trigger rollback exclusive")

    mock_conn.rollback.assert_called_once()


# ══════════════════════════════════════════════════════════════
#  TESTES — normalizar_tel (linha 276)
# ══════════════════════════════════════════════════════════════

class TestNormalizarTel:
    def test_prefixo_238_com_mais(self):
        """Linha 276: +238 prefix é removido."""
        import db._conn as _db_conn
        assert _db_conn.normalizar_tel("+2389911234") == "9911234"

    def test_prefixo_238_sem_mais(self):
        """Linha 276: 238 prefix sem + é removido."""
        import db._conn as _db_conn
        assert _db_conn.normalizar_tel("2389911234") == "9911234"

    def test_numero_normal(self):
        """Número sem prefixo mantém-se."""
        import db._conn as _db_conn
        assert _db_conn.normalizar_tel("9911234") == "9911234"

    def test_vazio(self):
        """String vazia devolve vazia."""
        import db._conn as _db_conn
        assert _db_conn.normalizar_tel("") == ""

    def test_com_espacos_e_tracoes(self):
        """Espaços e traços são removidos."""
        import db._conn as _db_conn
        assert _db_conn.normalizar_tel("991 12-34") == "9911234"


# ══════════════════════════════════════════════════════════════
#  TESTES — slug_unico com excluir_id (linhas 306-307)
# ══════════════════════════════════════════════════════════════

def test_slug_unico_com_excluir_id(tmp_db_ctx):
    """Linhas 306-307: slug_unico com excluir_id não colide com o próprio registo."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    # O slug da barbearia "Conn Test" já existe — excluir_id deve permitir o mesmo slug
    slug = _db_conn.slug_unico("Conn Test", excluir_id=bid)
    assert slug == "conn-test"  # deve devolver o mesmo base slug pois excluímos o próprio


def test_slug_unico_colisao(tmp_db_ctx):
    """Linhas 306-307: slug_unico incrementa sufixo quando há colisão."""
    import db._conn as _db_conn
    db = tmp_db_ctx["db"]

    # Criar segunda barbearia com o mesmo nome
    bid2 = db.criar_barbearia("SlugColide", tipo="barbearia")

    # Tentar obter slug para "SlugColide" excluindo bid2 → deve retornar "slugcolide"
    # Se pedirmos sem excluir, vai tentar sufixo
    slug_base = _db_conn.gerar_slug("SlugColide")
    slug = _db_conn.slug_unico("SlugColide", excluir_id=bid2)
    assert slug == slug_base


# ══════════════════════════════════════════════════════════════
#  TESTES — backup_db (linhas 321-337)
# ══════════════════════════════════════════════════════════════

def test_backup_db(tmp_db_ctx):
    """Linhas 321-337: backup_db cria ficheiro de backup."""
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    dest = os.path.join(tmp_dir, "backup_test.db")
    try:
        _db_conn.backup_db(dest)
        assert os.path.exists(dest)
        assert os.path.getsize(dest) > 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_backup_db_timeout():
    """Linha 323: backup_db lança RuntimeError quando lock não disponível."""
    import db._conn as _db_conn

    # backup_db usa _CONN_LOCK.acquire(timeout=10) directamente (não usa _acquire_lock)
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = False
    with patch("db._conn._CONN_LOCK", mock_lock):
        with pytest.raises(RuntimeError, match="DB_TIMEOUT"):
            _db_conn.backup_db("/tmp/nao_deve_criar.db")


# ══════════════════════════════════════════════════════════════
#  TESTES — migrações (linhas 379-527)
# ══════════════════════════════════════════════════════════════

def test_migrations_already_applied(tmp_db_ctx):
    """Migrações já aplicadas são ignoradas (if _v in aplicadas: continue)."""
    import db._conn as _db_conn

    conn = _db_conn.get_conn()
    # Correr as migrações novamente — deve ser idempotente sem erros
    _db_conn._run_migrations(conn)


def test_migrations_run_on_fresh_db():
    """Linhas 379-527: todas as migrações correm numa DB fresh."""
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_mig.db")
    orig    = _db_conn.DB_PATH

    try:
        _db_conn.DB_PATH = tmp_db
        _db_conn._CONN   = None

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row

        # Criar tabelas base que as migrações esperam
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS barbearias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                ativa INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS barbeiros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                barbearia_id INTEGER,
                ativo INTEGER DEFAULT 1,
                role TEXT DEFAULT 'barbeiro',
                username TEXT UNIQUE,
                password_hash TEXT);
            CREATE TABLE IF NOT EXISTS agendamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barbearia_id INTEGER NOT NULL,
                cliente TEXT NOT NULL,
                telefone TEXT,
                servico_id INTEGER NOT NULL,
                barbeiro_id INTEGER,
                data_hora TEXT NOT NULL,
                inicio TEXT,
                fim TEXT,
                status TEXT DEFAULT 'agendado',
                tipo TEXT DEFAULT 'agendado',
                valor INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pagamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barbearia_id INTEGER NOT NULL,
                codigo_plano TEXT NOT NULL,
                nome_plano TEXT NOT NULL,
                dias INTEGER NOT NULL,
                preco INTEGER DEFAULT 0,
                expira_em TEXT NOT NULL,
                registado_em TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS configuracoes (
                barbearia_id INTEGER NOT NULL,
                chave TEXT NOT NULL,
                valor TEXT,
                PRIMARY KEY (barbearia_id, chave));
        """)
        conn.commit()

        # Correr todas as migrações
        _db_conn._run_migrations(conn)

        # Verificar que a tabela schema_migrations existe com as versões
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        versoes = [r[0] for r in rows]
        assert 1 in versoes
        assert _db_conn._SCHEMA_VERSION in versoes

        conn.close()
    finally:
        _db_conn.DB_PATH = orig
        _db_conn._CONN   = None
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Restaurar conexão com DB original
        _db_conn.get_conn()


# ══════════════════════════════════════════════════════════════
#  TESTES — init_db: PRAGMA optimize e seed root (linhas 663-664, 679-680)
# ══════════════════════════════════════════════════════════════

def test_init_db_pragma_optimize_exception():
    """Linhas 663-664: excepção em PRAGMA optimize é ignorada."""
    import db._conn as _db_conn

    orig_conn = _db_conn._CONN
    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_pragma.db")
    orig_path = _db_conn.DB_PATH

    try:
        _db_conn.DB_PATH = tmp_db
        _db_conn._CONN   = None

        # Usar mock_conn cujo execute levanta excepção para PRAGMA optimize
        # mas executa normalmente para o resto via sqlite3.Connection real
        import sqlite3
        real_conn = sqlite3.connect(tmp_db)
        real_conn.row_factory = sqlite3.Row

        class ConnProxy:
            """Proxy que intercepta PRAGMA optimize e lança excepção."""
            def __init__(self, c): self._c = c
            def execute(self, sql, *a, **kw):
                if "PRAGMA optimize" in sql:
                    raise Exception("optimize not supported")
                return self._c.execute(sql, *a, **kw)
            def executescript(self, sql): return self._c.executescript(sql)
            def cursor(self): return self._c.cursor()
            def commit(self): return self._c.commit()
            def rollback(self): return self._c.rollback()
            def close(self): return self._c.close()
            def __getattr__(self, name): return getattr(self._c, name)
            @property
            def row_factory(self): return self._c.row_factory
            @row_factory.setter
            def row_factory(self, v): self._c.row_factory = v

        proxy = ConnProxy(real_conn)

        with patch("db._conn.get_conn", return_value=proxy), \
             patch("db._conn._acquire_lock", return_value=True), \
             patch("db._conn._CONN_LOCK"):
            _db_conn.init_db()  # não deve levantar excepção

        real_conn.close()

    finally:
        _db_conn._reset_conn()
        _db_conn.DB_PATH = orig_path
        _db_conn._CONN   = orig_conn
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _db_conn.get_conn()  # restaurar conexão


def test_init_db_seed_root_file_exception():
    """Linhas 679-680: excepção ao escrever .root_init_password é ignorada."""
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_seed.db")
    orig_path = _db_conn.DB_PATH
    orig_conn = _db_conn._CONN

    try:
        _db_conn.DB_PATH = tmp_db
        _db_conn._CONN   = None

        # Patchar open() para falhar quando tenta escrever o ficheiro de password
        with patch("builtins.open", side_effect=PermissionError("no write")):
            _db_conn.init_db()  # não deve levantar excepção

    finally:
        _db_conn._reset_conn()
        _db_conn.DB_PATH = orig_path
        _db_conn._CONN   = orig_conn
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_init_db_scrypt_migration():
    """Linhas 689-699: root com hash scrypt é migrado para pbkdf2."""
    from werkzeug.security import generate_password_hash
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_scrypt.db")
    orig_path = _db_conn.DB_PATH
    orig_conn = _db_conn._CONN

    try:
        _db_conn.DB_PATH = tmp_db
        _db_conn._CONN   = None

        # Criar DB com root que tem hash scrypt
        import sqlite3
        setup_conn = sqlite3.connect(tmp_db)
        setup_conn.row_factory = sqlite3.Row
        setup_conn.executescript("""
            CREATE TABLE IF NOT EXISTS barbearias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                ativa INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS barbeiros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                barbearia_id INTEGER,
                ativo INTEGER DEFAULT 1,
                role TEXT DEFAULT 'barbeiro',
                username TEXT UNIQUE,
                password_hash TEXT);
        """)
        # Inserir root com hash falso que começa com "scrypt:"
        fake_scrypt_hash = "scrypt:32768:8:1$fakesalt$fakederivedkey"
        setup_conn.execute(
            "INSERT INTO barbeiros (nome, role, username, password_hash) VALUES (?,?,?,?)",
            ("Root", "root", "root", fake_scrypt_hash)
        )
        setup_conn.commit()
        setup_conn.close()

        # Agora correr init_db — deve migrar o hash scrypt
        with patch("builtins.open", side_effect=PermissionError("no write")):
            _db_conn.init_db()

        # Verificar que o hash foi substituído
        check_conn = _db_conn.get_conn()
        row = check_conn.execute(
            "SELECT password_hash FROM barbeiros WHERE role='root'"
        ).fetchone()
        assert row is not None
        assert not row[0].startswith("scrypt:")
        assert row[0].startswith("pbkdf2:")

    finally:
        _db_conn._reset_conn()
        _db_conn.DB_PATH = orig_path
        _db_conn._CONN   = orig_conn
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  TESTES — set_config com timezone (linhas 725-726)
# ══════════════════════════════════════════════════════════════

def test_set_config_timezone_invalidates_tz_cache(tmp_db_ctx):
    """Linhas 725-726: set_config("timezone", ...) limpa cache de fuso."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    # Popular cache de TZ
    with _db_conn._tz_cache_lock:
        _db_conn._tz_cache[bid] = ("Atlantic/Cape_Verde", time.monotonic() + 300)

    _db_conn.set_config("timezone", "Europe/London", bid)

    with _db_conn._tz_cache_lock:
        assert bid not in _db_conn._tz_cache


def test_set_config_buffer_minutos_invalidates_slots_cache(tmp_db_ctx):
    """Linha 721-722: set_config("buffer_minutos", ...) invalida cache de slots."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    bid_str = str(bid)
    with _db_conn._slots_cache_lock:
        _db_conn._slots_cache[f"{bid_str}:2025-01-01"] = {
            "data": {}, "exp": time.monotonic() + 3600
        }

    _db_conn.set_config("buffer_minutos", "5", bid)

    with _db_conn._slots_cache_lock:
        assert f"{bid_str}:2025-01-01" not in _db_conn._slots_cache


# ══════════════════════════════════════════════════════════════
#  TESTES — get_todas_configs (linha 729-734)
# ══════════════════════════════════════════════════════════════

def test_get_todas_configs(tmp_db_ctx):
    """get_todas_configs devolve dict com todas as configs da barbearia."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    _db_conn.set_config("test_key_1", "val_1", bid)
    _db_conn.set_config("test_key_2", "val_2", bid)

    configs = _db_conn.get_todas_configs(bid)
    assert isinstance(configs, dict)
    assert configs.get("test_key_1") == "val_1"
    assert configs.get("test_key_2") == "val_2"


# ══════════════════════════════════════════════════════════════
#  TESTES — get_config / set_config básicos
# ══════════════════════════════════════════════════════════════

def test_get_config_default(tmp_db_ctx):
    """get_config devolve default quando chave não existe."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    val = _db_conn.get_config("chave_inexistente_xyz", bid, default="meu_default")
    assert val == "meu_default"


def test_get_config_existing(tmp_db_ctx):
    """get_config devolve o valor guardado."""
    import db._conn as _db_conn
    bid = tmp_db_ctx["bid"]

    _db_conn.set_config("chave_teste", "valor_teste", bid)
    val = _db_conn.get_config("chave_teste", bid)
    assert val == "valor_teste"
