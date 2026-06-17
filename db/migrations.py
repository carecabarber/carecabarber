# db/migrations.py — Motor de migrações SQLite declarativo
#
# Uso:
#   from db.migrations import migrate
#   migrate(conn)   # aplica todas as migrações pendentes, idempotente
#
# Para adicionar uma migração:
#   1. Adicionar um dict a MIGRATIONS (version deve ser sequencial).
#   2. Incrementar _LATEST_VERSION.
#   3. Documentar em migrations/history.md.
#
# Notas de integração:
#   - Este módulo é independente do sistema legado (_run_migrations / schema_migrations).
#     Ambos coexistem sem conflito — usam tabelas de controlo distintas.
#   - migrate(conn) é chamado em init_db() logo após _run_migrations(conn).
#   - Cada migração corre numa transacção isolada; falha → rollback + exception.
#   - Re-correr migrate(conn) é seguro: migrações já aplicadas são ignoradas.

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("db.migrations")

# ── Tabela de controlo ────────────────────────────────────────────────────────
_CREATE_VERSION_TABLE = """
    CREATE TABLE IF NOT EXISTS _schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT    NOT NULL,
        description TEXT   NOT NULL DEFAULT ''
    )
"""

# ── Catálogo de migrações ─────────────────────────────────────────────────────
# Cada entrada:
#   version     — inteiro sequencial (começa em 1)
#   description — texto legível (aparece no log e em _schema_version)
#   sql         — string SQL ou lista de strings SQL a executar em sequência
#                 Pode ser vazio ("") ou lista vazia [] para migrações de documentação.
#
# REGRA: nunca alterar nem remover entradas já existentes.
# Para reverter um efeito, adicionar uma nova migração.

MIGRATIONS: list[dict] = [
    {
        "version": 1,
        "description": "Exemplo: índice de performance em agendamentos por criado_em",
        # Este índice só é criado se ainda não existir — totalmente idempotente.
        # Serve como template para futuras migrações declarativas.
        "sql": [
            "CREATE INDEX IF NOT EXISTS idx_ag_criado_em "
            "ON agendamentos(barbearia_id, criado_em) "
            "WHERE criado_em IS NOT NULL",
        ],
    },
    {
        "version": 2,
        "description": "Adicionar lembrete_wa_em a agendamentos — rastreia quando lembrete WA foi enviado",
        "sql": [
            "ALTER TABLE agendamentos ADD COLUMN lembrete_wa_em TEXT DEFAULT NULL",
        ],
    },
    {
        "version": 3,
        "description": "Adicionar fid_resets — reset manual de ciclo de fidelidade por chefe",
        "sql": [
            "CREATE TABLE IF NOT EXISTS fidelidade_resets ("
            "  id           INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  barbearia_id INTEGER NOT NULL,"
            "  telefone     TEXT    NOT NULL,"
            "  resetado_em  TEXT    NOT NULL,"
            "  obs          TEXT    DEFAULT NULL"
            ")",
            "CREATE INDEX IF NOT EXISTS idx_fid_resets_tel "
            "ON fidelidade_resets(barbearia_id, telefone)",
        ],
    },
]

_LATEST_VERSION: int = max(m["version"] for m in MIGRATIONS) if MIGRATIONS else 0


# ── Motor ─────────────────────────────────────────────────────────────────────

def _ensure_version_table(conn: sqlite3.Connection) -> None:
    """Cria _schema_version se não existir. Não usa transacção própria —
    chamado dentro de um contexto já controlado pelo chamador."""
    conn.execute(_CREATE_VERSION_TABLE)


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Devolve o conjunto de versões já aplicadas."""
    return {row[0] for row in conn.execute("SELECT version FROM _schema_version")}


def _record_migration(conn: sqlite3.Connection, version: int, description: str) -> None:
    """Regista que a migração foi aplicada. Idempotente (INSERT OR IGNORE)."""
    applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR IGNORE INTO _schema_version (version, applied_at, description) "
        "VALUES (?, ?, ?)",
        (version, applied_at, description),
    )


def migrate(conn: sqlite3.Connection) -> None:
    """Aplica todas as migrações pendentes em ordem crescente de version.

    - Idempotente: re-correr não tem efeito se tudo já foi aplicado.
    - Cada migração corre numa transacção isolada; falha → rollback + exception
      (migrações anteriores da mesma chamada ficam aplicadas).
    - sql pode ser str, list[str] ou vazio ("" / []) para migrações de documentação.
    """
    # Garantir tabela de controlo
    _ensure_version_table(conn)
    conn.commit()

    aplicadas = _applied_versions(conn)

    pendentes = sorted(
        (m for m in MIGRATIONS if m["version"] not in aplicadas),
        key=lambda m: m["version"],
    )

    if not pendentes:
        logger.debug("db.migrations: sem migrações pendentes (latest=v%d)", _LATEST_VERSION)
        return

    for m in pendentes:
        version     = m["version"]
        description = m["description"]
        sql_raw     = m.get("sql", [])

        # Normalizar sql para lista de strings
        if isinstance(sql_raw, str):
            statements = [sql_raw] if sql_raw.strip() else []
        else:
            statements = [s for s in sql_raw if s and s.strip()]

        logger.info("db.migrations: aplicando v%d — %s", version, description)

        try:
            conn.execute("BEGIN")
            for stmt in statements:
                conn.execute(stmt)
            _record_migration(conn, version, description)
            conn.commit()
            logger.info("db.migrations: v%d aplicada com sucesso", version)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.error(
                "db.migrations: FALHA em v%d (%s) — %s",
                version, description, exc,
            )
            raise RuntimeError(
                f"Migração v{version} ({description!r}) falhou: {exc}"
            ) from exc
