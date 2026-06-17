"""
tests/test_migrations_extra.py — Cobre linhas restantes em db/migrations.py.

Linhas alvo: 114 (sql como string), 127-136 (bloco except / rollback + RuntimeError)
"""
import os, sys, pytest, sqlite3, tempfile
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-migrations-extra")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def mem_conn():
    """Conexão SQLite em memória com row_factory configurada."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


class TestMigrationsSqlString:
    def test_sql_como_string_nao_vazia(self, mem_conn):
        """sql como string (não lista) → linha 114: [sql_raw] se não vazio."""
        from db.migrations import migrate, _ensure_version_table, _LATEST_VERSION
        fake = [
            {
                "version": 9901,
                "description": "teste string sql",
                "sql": "CREATE TABLE IF NOT EXISTS _test_str_sql (id INTEGER PRIMARY KEY)",
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9901):
            migrate(mem_conn)
        # Deve ter criado a tabela
        tables = [r[0] for r in mem_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        assert "_test_str_sql" in tables

    def test_sql_como_string_vazia(self, mem_conn):
        """sql como string vazia → linha 114: statements = [] (migração documental)."""
        from db.migrations import migrate
        fake = [
            {
                "version": 9902,
                "description": "migração documental",
                "sql": "",
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9902):
            migrate(mem_conn)  # não crasha, não faz nada

    def test_sql_lista_com_strings_vazias(self, mem_conn):
        """sql como lista com strings vazias/None → filtradas (linha 116)."""
        from db.migrations import migrate
        fake = [
            {
                "version": 9903,
                "description": "lista com vazios",
                "sql": ["", None, "   ", "SELECT 1"],
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9903):
            migrate(mem_conn)  # deve correr sem erro

    def test_migracoes_ja_aplicadas_saltadas(self, mem_conn):
        """Migração já aplicada não é re-executada."""
        from db.migrations import migrate, _ensure_version_table, _record_migration
        _ensure_version_table(mem_conn)
        mem_conn.commit()
        # Marcar como aplicada manualmente
        _record_migration(mem_conn, 9904, "pre-aplicada")
        mem_conn.commit()
        fake = [
            {
                "version": 9904,
                "description": "pre-aplicada",
                "sql": "INVALID SQL THAT WOULD FAIL",
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9904):
            migrate(mem_conn)  # não deve tentar executar (já aplicada)


class TestMigracoesExcecao:
    def test_sql_invalido_levanta_runtime_error(self, mem_conn):
        """SQL inválido → except captura, rollback, RuntimeError (linhas 127-136)."""
        from db.migrations import migrate
        fake = [
            {
                "version": 9905,
                "description": "sql invalido",
                "sql": "TOTALLY INVALID SQL !!!",
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9905):
            with pytest.raises(RuntimeError, match="Migração v9905"):
                migrate(mem_conn)

    def test_rollback_em_sql_multiplo_parcial(self, mem_conn):
        """SQL parcialmente inválido → rollback e RuntimeError."""
        from db.migrations import migrate
        fake = [
            {
                "version": 9906,
                "description": "multi parcial",
                "sql": [
                    "CREATE TABLE IF NOT EXISTS _parcial_test (id INTEGER PRIMARY KEY)",
                    "INVALID SQL HERE",
                ],
            }
        ]
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9906):
            with pytest.raises(RuntimeError, match="Migração v9906"):
                migrate(mem_conn)
        # A tabela pode ou não existir (depende do autocommit do SQLite)
        # mas o importante é que RuntimeError foi levantado

    def test_rollback_exception_interno_ignorado(self):
        """Quando rollback() também falha, a exception original prevalece (linha 130)."""
        from db.migrations import migrate

        # Usar uma conn fake com rollback que falha para cobrir o except interior
        class FakeConn:
            def __init__(self):
                self._executed = []
                self._committed = False
            def execute(self, sql, params=()):
                if "BEGIN" in sql:
                    return self
                if "TOTALLY" in sql or "INVALID" in sql:
                    raise sqlite3.OperationalError("syntax error")
                self._executed.append(sql)
                return self
            def commit(self):
                self._committed = True
            def rollback(self):
                raise Exception("rollback também falhou!")
            def fetchall(self):
                return []
            def __iter__(self):
                return iter([])

        fake = [
            {
                "version": 9907,
                "description": "rollback erro",
                "sql": "TOTALLY INVALID SQL",
            }
        ]
        conn = FakeConn()
        with patch("db.migrations.MIGRATIONS", fake), \
             patch("db.migrations._LATEST_VERSION", 9907), \
             patch("db.migrations._ensure_version_table"), \
             patch("db.migrations._applied_versions", return_value=set()):
            with pytest.raises(RuntimeError, match="Migração v9907"):
                migrate(conn)
