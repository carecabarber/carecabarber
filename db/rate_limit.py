# db/rate_limit.py — Rate limiting persistente via SQLite dedicado.
# Ficheiro separado da DB principal para não adicionar contention ao _CONN_LOCK.
import sqlite3
import threading
import time
import os

_RL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rate_limit.db")

_lock = threading.Lock()
_cleanup_n = 0
_CLEANUP_EVERY = 200


def _init_conn() -> sqlite3.Connection:
    """Cria e inicializa a conexão SQLite. Chamado uma vez no arranque do módulo."""
    c = sqlite3.connect(_RL_PATH, check_same_thread=False, timeout=5)
    c.row_factory = sqlite3.Row
    # DELETE é o único modo journal seguro em NFS (WAL usa mmap + shm files
    # que podem corromper em montagens NFS — PythonAnywhere usa NFS).
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("PRAGMA synchronous=OFF")   # sem fsync — consistente com _conn.py
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("""CREATE TABLE IF NOT EXISTS rl_api (
        ip TEXT NOT NULL, ts REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rl_api ON rl_api(ip, ts)")
    c.execute("""CREATE TABLE IF NOT EXISTS rl_login_ts (
        ip TEXT NOT NULL, ts REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rl_login ON rl_login_ts(ip, ts)")
    c.execute("""CREATE TABLE IF NOT EXISTS rl_backoff (
        ip            TEXT    PRIMARY KEY,
        bloqueado_ate REAL    NOT NULL DEFAULT 0,
        nivel         INTEGER NOT NULL DEFAULT 0)""")
    # Falhas de login por username — persiste entre restarts do servidor
    c.execute("""CREATE TABLE IF NOT EXISTS rl_user_fails (
        username TEXT    NOT NULL,
        ts       REAL    NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rl_user ON rl_user_fails(username, ts)")
    c.commit()
    return c


# Conexão inicializada no arranque do módulo (evita re-aquisição do _lock dentro de _lock)
_conn: sqlite3.Connection = _init_conn()


def _get_conn() -> sqlite3.Connection:
    return _conn


def _maybe_cleanup() -> None:
    global _cleanup_n
    _cleanup_n += 1
    if _cleanup_n < _CLEANUP_EVERY:
        return
    _cleanup_n = 0
    now = time.time()
    conn = _get_conn()
    conn.execute("DELETE FROM rl_api WHERE ts < ?", (now - 120,))
    conn.execute("DELETE FROM rl_login_ts WHERE ts < ?", (now - 600,))
    conn.execute("DELETE FROM rl_backoff WHERE bloqueado_ate > 0 AND bloqueado_ate < ?", (now - 1,))
    conn.commit()


def api_ok(ip: str, max_req: int = 120, window: int = 60) -> bool:
    if not ip or ip == "?":
        ip = "__unknown__"
    now = time.time()
    with _lock:
        conn = _get_conn()
        _maybe_cleanup()
        count = conn.execute(
            "SELECT COUNT(*) FROM rl_api WHERE ip=? AND ts >= ?",
            (ip, now - window)).fetchone()[0]
        if count >= max_req:
            return False
        conn.execute("INSERT INTO rl_api (ip, ts) VALUES (?, ?)", (ip, now))
        conn.commit()
    return True


def ip_ok(ip: str, max_attempts: int = 10, window: int = 300) -> bool:
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    now = time.time()
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT bloqueado_ate, nivel FROM rl_backoff WHERE ip=?", (ip,)).fetchone()
        if row:
            ate   = float(row["bloqueado_ate"])
            nivel = int(row["nivel"])
            if now < ate:
                return False
            conn.execute("DELETE FROM rl_backoff WHERE ip=?", (ip,))
            conn.commit()
        else:
            nivel = 0
        count = conn.execute(
            "SELECT COUNT(*) FROM rl_login_ts WHERE ip=? AND ts >= ?",
            (ip, now - window)).fetchone()[0]
        if count >= max_attempts:
            nivel_novo = nivel + 1
            espera = min(30 * (2 ** nivel), 1800)
            conn.execute(
                "INSERT OR REPLACE INTO rl_backoff (ip, bloqueado_ate, nivel) VALUES (?, ?, ?)",
                (ip, now + espera, nivel_novo))
            conn.commit()
            return False
        conn.execute("INSERT INTO rl_login_ts (ip, ts) VALUES (?, ?)", (ip, now))
        conn.commit()
    return True


def ip_retry_after(ip: str) -> int:
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT bloqueado_ate FROM rl_backoff WHERE ip=?", (ip,)).fetchone()
        if row:
            return max(0, int(float(row["bloqueado_ate"]) - time.time()))
    return 0


def set_backoff(ip: str, duration_s: float, nivel: int = 1) -> None:
    """Define backoff para um IP (admin / testes)."""
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    now = time.time()
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO rl_backoff (ip, bloqueado_ate, nivel) VALUES (?, ?, ?)",
            (ip, now + duration_s, nivel))
        conn.commit()


def reset_ip(ip: str) -> None:
    """Limpa todo o histórico de um IP."""
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM rl_api WHERE ip=?", (ip,))
        conn.execute("DELETE FROM rl_login_ts WHERE ip=?", (ip,))
        conn.execute("DELETE FROM rl_backoff WHERE ip=?", (ip,))
        conn.commit()


def reset_all() -> None:
    """Limpa todo o estado (para testes)."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM rl_api")
        conn.execute("DELETE FROM rl_login_ts")
        conn.execute("DELETE FROM rl_backoff")
        conn.execute("DELETE FROM rl_user_fails")
        conn.commit()


# ── Falhas de login por username (persistente entre restarts) ─────────────────

def user_fail_record(username: str) -> None:
    """Regista uma tentativa falhada de login para o username."""
    with _lock:
        conn = _get_conn()
        conn.execute("INSERT INTO rl_user_fails (username, ts) VALUES (?, ?)",
                     (username, time.time()))
        conn.commit()


def user_fail_count(username: str, window: float) -> int:
    """Devolve o número de falhas recentes (dentro de `window` segundos)."""
    cutoff = time.time() - window
    with _lock:
        conn = _get_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM rl_user_fails WHERE username=? AND ts >= ?",
            (username, cutoff)).fetchone()[0]


def user_fail_clear(username: str) -> None:
    """Limpa todas as falhas registadas para o username (após login com sucesso)."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM rl_user_fails WHERE username=?", (username,))
        conn.commit()


def user_fail_load_all(window: float) -> dict[str, list[float]]:
    """Carrega todos os registos recentes — usado no arranque para repopular o cache em memória."""
    cutoff = time.time() - window
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT username, ts FROM rl_user_fails WHERE ts >= ? ORDER BY ts",
            (cutoff,)).fetchall()
    result: dict[str, list[float]] = {}
    for row in rows:
        result.setdefault(row["username"], []).append(row["ts"])
    return result


def cleanup() -> None:
    """Forçar limpeza de expirados (chamado pelo background thread do app)."""
    global _cleanup_n
    _cleanup_n = _CLEANUP_EVERY
    with _lock:
        _maybe_cleanup()
