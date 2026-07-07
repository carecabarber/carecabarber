# db/_conn.py — Conexão SQLite, constantes, cache, migrações, configurações
# Importado por todos os sub-módulos db/*; nunca importa deles (sem ciclos).

import sqlite3
import os
import secrets
import logging
import unicodedata
import re
import threading
import time
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash

# DB_PATH: por omissão fica ao lado do projecto (PythonAnywhere). No Railway
# define-se a variável de ambiente DB_PATH=/data/barbearia.db (Volume persistente).
DB_PATH = os.environ.get("DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "barbearia.db"
)

FMT = "%Y-%m-%d %H:%M:%S"

# Colunas da tabela barbeiros SEM os BLOBs de foto — usar em todos os SELECT gerais
# para evitar arrastar megabytes de imagem em cada pedido.
_BARB_COLS = "id, nome, barbearia_id, ativo, role, username, password_hash, mesa_token, pausa_almoco_inicio, pausa_almoco_fim, telefone"

# ── Cache de slots disponíveis ────────────────────────────────────────────────
# TTL de 60 s para datas futuras, 15 s para hoje (dados mudam mais depressa)
#
# Invalidação cross-worker: o PythonAnywhere corre múltiplos workers, cada um
# com a sua própria cópia de _slots_cache em memória. Quando um worker invalida
# (booking/cancelamento), os outros não sabem — serviriam slots obsoletos até ao
# TTL expirar (até 60 s), podendo mostrar como livre um slot já ocupado.
# Solução: ficheiro-sentinela /tmp/ccb_bust_{bid} (mesmo usado pelo _pcache do
# dashboard em helpers_booking). Cada entrada guarda o mtime no momento do set;
# se o ficheiro for tocado por outro worker, o mtime muda e a entrada é miss.
# /tmp é tmpfs (ramdisk) no PA, logo getmtime é uma lookup de inode em memória.
_slots_cache: dict = {}
_slots_cache_lock = threading.Lock()


def _slots_bust_path(bid) -> str:
    return f"/tmp/ccb_bust_{bid}"


def _slots_bust_mtime(bid) -> float:
    try:
        return os.path.getmtime(_slots_bust_path(bid))
    except OSError:
        return 0.0


def _slots_bust_touch(bid) -> None:
    """Sinaliza invalidação da cache de slots a todos os workers."""
    try:
        with open(_slots_bust_path(bid), 'w') as _f:
            _f.write(str(time.time()))
    except OSError:
        pass


def _slots_bid_from_key(key: str):
    """barbearia_id é sempre o primeiro segmento da chave ({bid}:{...})."""
    head = key.split(':', 1)[0]
    return head if head.isdigit() else None


def _slots_cache_get(key: str) -> list | None:
    with _slots_cache_lock:
        entry = _slots_cache.get(key)
        if not entry or time.monotonic() >= entry["exp"]:
            return None
        bid = _slots_bid_from_key(key)
        if bid is not None and _slots_bust_mtime(bid) > entry.get("bust", 0.0):
            return None   # outro worker invalidou entretanto
        return entry["data"]

_SLOTS_CACHE_MAX = 300   # máximo de entradas — evita OOM em PythonAnywhere

def _slots_cache_set(key: str, data: list, ttl: int | float) -> None:
    bid = _slots_bid_from_key(key)
    bust_at_set = _slots_bust_mtime(bid) if bid is not None else 0.0
    with _slots_cache_lock:
        # Se cache cheio, apagar primeiro as entradas já expiradas; depois as mais antigas
        if len(_slots_cache) >= _SLOTS_CACHE_MAX:
            now = time.monotonic()
            expiradas = [k for k, v in _slots_cache.items() if now >= v["exp"]]
            if expiradas:
                for k in expiradas:
                    del _slots_cache[k]
            else:
                # Apagar as 50 entradas que expiram mais cedo
                mais_antigas = sorted(_slots_cache, key=lambda k: _slots_cache[k]["exp"])[:50]
                for k in mais_antigas:
                    del _slots_cache[k]
        _slots_cache[key] = {"data": data, "exp": time.monotonic() + ttl, "bust": bust_at_set}

# ── Constantes de status ──────────────────────────────────────────────────────
# Usar estas constantes em vez de strings literais para evitar typos silenciosos.
ST_AGENDADO     = "agendado"
ST_EM_ANDAMENTO = "em_andamento"
ST_CONCLUIDO    = "concluido"
ST_CANCELADO    = "cancelado"
ST_NAO_COMP     = "nao_compareceu"
ST_WALKIN       = "walk-in"


def invalidar_cache_slots(barbearia_id: int | str | None = None) -> None:
    """Invalida toda a cache de slots (ou apenas de uma barbearia).
    Sem argumentos: limpa entradas expiradas (GC periódico)."""
    with _slots_cache_lock:
        if barbearia_id is None:
            # GC: remover apenas entradas expiradas (chamado pelo thread de limpeza)
            now = time.monotonic()
            expiradas = [k for k, v in _slots_cache.items() if now >= v["exp"]]
            for k in expiradas:
                del _slots_cache[k]
        else:
            bid_str = str(barbearia_id)
            chaves = [k for k in _slots_cache if k.startswith(bid_str + ":")]
            for k in chaves:
                del _slots_cache[k]
    # Fora do lock (I/O): sinaliza a invalidação aos outros workers do PA.
    if barbearia_id is not None:
        _slots_bust_touch(barbearia_id)


def invalidar_cache_slots_completo() -> None:
    """Limpa completamente a cache de slots (usar com cuidado)."""
    with _slots_cache_lock:
        _slots_cache.clear()

# ── Fuso horário por pedido (thread-local) ────────────────────────────────────
# app.py chama set_request_tz() uma vez por pedido HTTP (via before_request).
# Assim cada thread usa o fuso do dispositivo que fez o pedido.
_tz_local = threading.local()

def set_request_tz(tz_name: str | None) -> None:
    """Guarda o nome do fuso horário para este pedido/thread."""
    _tz_local.tz_name = (tz_name or "").strip()

def _agora(barbearia_id: int | None = None) -> datetime:
    """Hora actual no fuso da barbearia (default: Atlantic/Cape_Verde).
    Independente do fuso do servidor."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    tz_name = "Atlantic/Cape_Verde"
    if barbearia_id is not None:
        try:
            tz_name = get_barbearia_tz(barbearia_id) or "Atlantic/Cape_Verde"
        except Exception:
            pass
    try:
        return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, Exception):
        return datetime.now()


# Cache de fuso por barbearia com TTL de 5 minutos
# (sem TTL, workers diferentes servem o fuso antigo indefinidamente após mudança)
_tz_cache: dict = {}   # {barbearia_id: (tz_name, expires_monotonic)}
_TZ_CACHE_TTL = 300    # 5 minutos

_tz_cache_lock = threading.Lock()

# ── Cache de configurações por barbearia ─────────────────────────────────────
# get_config() é chamado em cada request por context processors (moeda, vocab, etc.).
# Cache com TTL de 60 s elimina round-trips NFS repetidos para dados que raramente mudam.
_config_cache: dict = {}   # {(barbearia_id, chave): (valor, expires_monotonic)}
_config_cache_lock = threading.Lock()
_CONFIG_CACHE_TTL = 60  # segundos

def get_barbearia_tz(barbearia_id: int) -> str:
    """Devolve o nome do fuso horário configurado para esta barbearia.
    Default: Atlantic/Cape_Verde.  Lê config 'timezone'."""
    with _tz_cache_lock:
        entry = _tz_cache.get(barbearia_id)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
    val = get_config("timezone", barbearia_id, "Atlantic/Cape_Verde") or "Atlantic/Cape_Verde"
    with _tz_cache_lock:
        _tz_cache[barbearia_id] = (val, time.monotonic() + _TZ_CACHE_TTL)
    return val


def set_barbearia_tz(barbearia_id: int, tz_name: str) -> None:
    """Guarda o fuso horário da barbearia e limpa o cache."""
    set_config("timezone", tz_name, barbearia_id)
    with _tz_cache_lock:
        _tz_cache.pop(barbearia_id, None)


_HORARIO_PADRAO = [
    (0, "08:00", "19:00", 0),
    (1, "08:00", "19:00", 0),
    (2, "08:00", "19:00", 0),
    (3, "08:00", "19:00", 0),
    (4, "08:00", "19:00", 0),
    (5, "08:00", "18:00", 0),
    (6, "00:00", "00:00", 1),
]


# ── Conexão persistente ───────────────────────────────────────────────────────
# No PythonAnywhere (NFS), cada sqlite3.connect() faz open() no NFS que pode
# bloquear 40-160 s → HARAKIRI uWSGI → 502. Solução: abrir UMA conexão no
# arranque do processo e reutilizá-la em todos os pedidos HTTP.
# check_same_thread=False: uWSGI single-worker — só um thread activo de cada vez.
_CONN: sqlite3.Connection | None = None
_CONN_LOCK = threading.RLock()   # serializa escritas (_write/_write_exclusive)
_INIT_LOCK = threading.Lock()    # serializa APENAS a inicialização da write conn

# Conexão dedicada a leituras — lock próprio, independente de _CONN_LOCK.
# Resultado: reads de request handlers e writes do _thread_limpeza não se
# bloqueiam mutuamente; SQLite resolve conflitos a nível de ficheiro via busy_timeout.
_READ_CONN: sqlite3.Connection | None = None
_READ_LOCK = threading.RLock()   # serializa leituras (_read)
_READ_INIT_LOCK = threading.Lock()

# ── Monitorização de contenção no lock ──────────────────────
_LOCK_WAIT_WARN_S = 1.0
_lock_wait_total  = 0.0
_lock_wait_log    = logging.getLogger("db.conn")


def _connect() -> sqlite3.Connection:
    # SEM locking_mode=EXCLUSIVE. O EXCLUSIVE obriga o worker a segurar um POSIX
    # file-lock durante toda a vida do processo: impede reloads limpos (o worker
    # novo bloqueia até o antigo morrer — 60s de "mercy" do uWSGI), torna o
    # arranque lento (31s+) e provoca downtime a cada recycle do PythonAnywhere.
    # Com UM único worker e UMA única conexão persistente não há contenção entre
    # conexões, logo o fcntl() por operação em NFS é barato (não há lock para
    # disputar). busy_timeout cobre a janela de transição em que o worker antigo
    # (ainda EXCLUSIVE) só liberta o ficheiro ao ser morto.
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")
    # NORMAL: fsync 1× por commit (antes de apagar o journal) — protege contra
    # crash do OS. Seguro em NFS com conexão persistente: sem locking_mode=EXCLUSIVE
    # e sem reconexões, a latência do fsync NFS é < 50ms → aceitável.
    # OFF foi necessário quando havia reconexões frequentes + EXCLUSIVE; já não se aplica.
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA temp_store=MEMORY")  # ficheiros temp em RAM, nunca no NFS
    c.execute("PRAGMA mmap_size=0")        # mmap desativado — inseguro em NFS
    c.execute("PRAGMA cache_size=-4096")   # 4 MB page cache em processo
    return c


def get_conn() -> sqlite3.Connection:
    """Devolve conexão de escrita — aberta uma vez por processo."""
    global _CONN
    if _CONN is None:
        with _INIT_LOCK:
            if _CONN is None:
                _CONN = _connect()
    return _CONN


def _connect_read() -> sqlite3.Connection:
    """Conexão de leitura em autocommit (isolation_level=None).
    Cada SELECT vê imediatamente os dados committed pela write conn.
    Sem isolation_level=None, a conn poderia manter snapshot antigo entre calls."""
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30,
                        isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA mmap_size=0")
    c.execute("PRAGMA cache_size=-4096")
    c.execute("PRAGMA query_only=ON")   # segurança: bloqueia escritas acidentais
    return c


def _get_read_conn() -> sqlite3.Connection:
    """Devolve conexão dedicada a leituras — independente da write conn.

    Detecta automaticamente mudanças em DB_PATH (comum em fixtures de teste)
    e reconecta quando o path mudou.
    """
    global _READ_CONN
    if _READ_CONN is None:
        with _READ_INIT_LOCK:
            if _READ_CONN is None:
                _READ_CONN = _connect_read()
        return _READ_CONN
    # Verificar se DB_PATH mudou (fixtures de teste podem alterar DB_PATH)
    try:
        current = _READ_CONN.execute(
            "PRAGMA database_list").fetchone()[2]
        if current and current != DB_PATH:
            with _READ_INIT_LOCK:
                try:
                    _READ_CONN.close()
                except Exception:
                    pass
                _READ_CONN = _connect_read()
    except Exception:
        # Conexão inválida — reconectar
        with _READ_INIT_LOCK:
            _READ_CONN = _connect_read()
    return _READ_CONN


def _reset_conn() -> None:
    """Fecha e anula ambas as conexões — usar nos teardowns de testes."""
    global _CONN, _READ_CONN
    for c in (_CONN, _READ_CONN):
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
    _CONN = None
    _READ_CONN = None


def _acquire_rlock(lock: threading.RLock, nome: str,
                   timeout_por_tentativa: int = 8, tentativas: int = 3,
                   pausa: float = 2.0) -> bool:
    """Adquire um RLock com retry e monitorização de espera."""
    global _lock_wait_total
    t0 = time.monotonic()
    for _t in range(tentativas):
        if lock.acquire(timeout=timeout_por_tentativa):
            espera = time.monotonic() - t0
            _lock_wait_total += espera
            if espera >= _LOCK_WAIT_WARN_S:
                _lock_wait_log.warning(
                    "DB %s lock: espera %.2fs (total acumulado %.1fs) — "
                    "considerar optimização de queries ou separação de tenants",
                    nome, espera, _lock_wait_total)
            return True
        if _t < tentativas - 1:
            time.sleep(pausa)
    _lock_wait_log.error("DB %s lock: timeout após %d tentativas × %ds",
                         nome, tentativas, timeout_por_tentativa)
    return False


def _acquire_lock(timeout_por_tentativa: int = 8, tentativas: int = 3, pausa: float = 2.0) -> bool:
    """Adquire o lock de escrita (_CONN_LOCK)."""
    return _acquire_rlock(_CONN_LOCK, "write", timeout_por_tentativa, tentativas, pausa)


def _acquire_read_lock(timeout_por_tentativa: int = 8, tentativas: int = 3, pausa: float = 2.0) -> bool:
    """Adquire o lock de leitura (_READ_LOCK)."""
    return _acquire_rlock(_READ_LOCK, "read", timeout_por_tentativa, tentativas, pausa)


def _is_nfs_io_error(e: Exception) -> bool:
    """Detecta erros de I/O do NFS (disk I/O error, unable to open database)."""
    msg = str(e).lower()
    return any(k in msg for k in ("disk i/o error", "unable to open database", "database disk image is malformed"))


@contextmanager
def _write():
    # Retry automático: NFS pode estar lento por <5s; 3 tentativas absorvem isso
    if not _acquire_lock():
        raise RuntimeError("DB_TIMEOUT")
    global _CONN
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        if _is_nfs_io_error(e):
            # NFS I/O error — forçar reconexão no próximo pedido
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _CONN_LOCK.release()


@contextmanager
def _write_exclusive():
    """Transação com BEGIN IMMEDIATE — impede race conditions em criação de agendamentos."""
    if not _acquire_lock():
        raise RuntimeError("DB_TIMEOUT")
    global _CONN
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception as e:
        if _is_nfs_io_error(e):
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _CONN_LOCK.release()


@contextmanager
def _read():
    # Usa _READ_CONN + _READ_LOCK — independente da write conn.
    # Reads e writes podem progredir sem se bloquear mutuamente;
    # conflitos SQLite são absorvidos por busy_timeout=60000.
    if not _acquire_read_lock():
        raise RuntimeError("DB_TIMEOUT")
    global _READ_CONN
    conn = _get_read_conn()
    try:
        yield conn
    except Exception as e:
        if _is_nfs_io_error(e):
            try:
                _READ_CONN.close()
            except Exception:
                pass
            _READ_CONN = None
        raise
    finally:
        _READ_LOCK.release()


# ── Normalização de telemóvel ──────────────────────────────

def normalizar_tel(tel: str | None) -> str:
    """Remove espaços, traços e parênteses; elimina prefixo +238/00238/238 de Cabo Verde.
    Devolve string com dígitos limpos (ex: '9911234') ou '' se vazio."""
    if not tel:
        return ""
    digits = re.sub(r'[\s\-\(\)\+]', '', tel)
    # Prefixo internacional 00238 (12 dígitos) ou +238/238 (10 dígitos) antes de 7 dígitos
    if len(digits) == 12 and digits.startswith("00238"):
        digits = digits[5:]
    elif len(digits) == 10 and digits.startswith("238"):
        digits = digits[3:]
    return digits


# ── Slug helpers ───────────────────────────────────────────

def gerar_slug(nome: str) -> str:
    """Converte nome da barbearia para slug URL-amigável."""
    s = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s.strip('-') or 'barbearia'


def slug_unico(nome: str, excluir_id: int | None = None) -> str:
    """Garante que o slug é único na tabela barbearias."""
    base = gerar_slug(nome)
    slug = base
    i = 2
    while True:
        with _read() as conn:
            q = "SELECT id FROM barbearias WHERE slug=?"
            params = [slug]
            if excluir_id:
                q += " AND id != ?"
                params.append(excluir_id)
            row = conn.execute(q, params).fetchone()
        if not row:
            return slug
        slug = f"{base}-{i}"
        i += 1


def backup_db(dest_path: str) -> None:
    """Backup seguro com locking_mode=EXCLUSIVE.

    Porquê é rápido e não bloqueia pedidos:
    - synchronous=OFF no destino: conn.backup() escreve para kernel buffer do NFS,
      sem fsync() — para uma DB <5MB completa em <150ms
    - _CONN_LOCK.timeout=10: se por algum motivo demorar mais, libertamos em vez de HARAKIRI
    - Durante esses <150ms, _read()/_write() aguardam com timeout=8s — não há problema

    Não abre nova connection (incompatível com locking_mode=EXCLUSIVE no source).
    """
    import sqlite3 as _sq
    if not _CONN_LOCK.acquire(timeout=10):
        raise RuntimeError("DB_TIMEOUT: lock não disponível para backup")
    try:
        conn = get_conn()
        conn.commit()  # garantir que não há transação pendente
        dst = _sq.connect(dest_path)
        try:
            # synchronous=OFF: sem fsync() em NFS → escrita em kernel buffer → rápido
            dst.execute("PRAGMA synchronous=OFF")
            dst.execute("PRAGMA journal_mode=OFF")  # destino é backup, não precisa de journal
            # pages=0 → copia tudo de uma vez, sem iterações com sleep entre lotes
            conn.backup(dst, pages=0)
        finally:
            dst.close()
    finally:
        _CONN_LOCK.release()


# ══════════════════════════════════════════════════════════════
#  MIGRAÇÕES DE SCHEMA NUMERADAS
#  Cada migração corre exactamente uma vez — registada em schema_migrations.
#  Para adicionar uma nova: incrementar _SCHEMA_VERSION e adicionar um bloco
#  "if _v == N:" no corpo de _run_migrations.
# ══════════════════════════════════════════════════════════════

_SCHEMA_VERSION = 25   # versão actual do schema

def _run_migrations(conn: sqlite3.Connection) -> None:
    """Aplica todas as migrações pendentes de forma idempotente."""

    # Tabela de controlo — criada se não existir
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            aplicado_em TEXT NOT NULL DEFAULT (datetime('now')))""")
    conn.commit()

    aplicadas = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    def _done(v: int):
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (v,))
        conn.commit()

    def _col_existe(tabela: str, coluna: str) -> bool:
        return any(row[1] == coluna
                   for row in conn.execute(f"PRAGMA table_info({tabela})").fetchall())

    for _v in range(1, _SCHEMA_VERSION + 1):
        if _v in aplicadas:
            continue

        if _v == 1:
            # barbearias: slug, logo, tipo
            for _c, _d in [("slug","TEXT"), ("logo","TEXT"), ("tipo","TEXT DEFAULT 'barbearia'")]:
                try:
                    conn.execute(f"ALTER TABLE barbearias ADD COLUMN {_c} {_d}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("UPDATE barbearias SET tipo='barbearia' WHERE tipo IS NULL")

        elif _v == 2:
            # barbearias: vocabulário personalizado (tipo='outro')
            try:
                conn.execute("ALTER TABLE barbearias ADD COLUMN vocab_custom TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 3:
            # agendamentos: avaliação 1-5 estrelas
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN avaliacao INTEGER DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 4:
            # agendamentos: token para reagendamento pelo cliente
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN token_reagendar TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 5:
            # agendamentos: data/hora de criação
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN criado_em TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 6:
            # agendamentos: notas internas do barbeiro
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN notas TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 7:
            # agendamentos: token de avaliação pública + índice único
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN token_avaliar TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_token_avaliar "
                    "ON agendamentos(token_avaliar) WHERE token_avaliar IS NOT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 8:
            # barbeiros: mesa_token (QR de mesa) + índice único
            if not _col_existe("barbeiros", "mesa_token"):
                conn.execute("ALTER TABLE barbeiros ADD COLUMN mesa_token TEXT")
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_barbeiros_mesa_token "
                    "ON barbeiros(mesa_token) WHERE mesa_token IS NOT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 9:
            # barbeiros: foto de perfil (BLOB + mime)
            if not _col_existe("barbeiros", "foto_perfil"):
                conn.execute("ALTER TABLE barbeiros ADD COLUMN foto_perfil BLOB")
            if not _col_existe("barbeiros", "foto_perfil_mime"):
                conn.execute("ALTER TABLE barbeiros ADD COLUMN foto_perfil_mime TEXT")

        elif _v == 10:
            # barbeiros: gerar mesa_token para quem não tem
            _sem = conn.execute(
                "SELECT id FROM barbeiros WHERE mesa_token IS NULL "
                "AND role IN ('barbeiro','chefe') AND barbearia_id IS NOT NULL").fetchall()
            for _b in _sem:
                conn.execute("UPDATE barbeiros SET mesa_token=? WHERE id=?",
                             (secrets.token_urlsafe(32), _b["id"]))

        elif _v == 11:
            # agendamentos: snapshot do nome do barbeiro (persiste após apagar)
            try:
                conn.execute(
                    "ALTER TABLE agendamentos ADD COLUMN barbeiro_nome_snap TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 12:
            # barbearias: data de expiração do plano
            try:
                conn.execute(
                    "ALTER TABLE barbearias ADD COLUMN plano_expira_em TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 13:
            # barbearias: gerar slugs para quem não tem
            _sem = conn.execute(
                "SELECT id, nome FROM barbearias WHERE slug IS NULL OR slug=''").fetchall()
            for _b in _sem:
                _slug = slug_unico(_b["nome"], excluir_id=_b["id"])
                conn.execute("UPDATE barbearias SET slug=? WHERE id=?", (_slug, _b["id"]))

        elif _v == 14:
            # pagamentos: moeda (histórico multi-moeda)
            try:
                conn.execute("ALTER TABLE pagamentos ADD COLUMN moeda TEXT DEFAULT 'ECV'")
            except sqlite3.OperationalError:
                pass

        elif _v == 15:
            # planos_precos_barbearia: preços por estabelecimento
            conn.execute("""
                CREATE TABLE IF NOT EXISTS planos_precos_barbearia (
                    barbearia_id INTEGER NOT NULL,
                    codigo       TEXT    NOT NULL,
                    preco        INTEGER NOT NULL DEFAULT 0,
                    moeda        TEXT    NOT NULL DEFAULT 'ECV',
                    PRIMARY KEY (barbearia_id, codigo),
                    FOREIGN KEY (barbearia_id) REFERENCES barbearias(id))""")

        elif _v == 16:
            # push_subscriptions: garantir tabela existe (pode ter sido criada noutra versão)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    barbeiro_id  INTEGER NOT NULL,
                    barbearia_id INTEGER NOT NULL,
                    endpoint     TEXT    NOT NULL UNIQUE,
                    p256dh       TEXT    NOT NULL,
                    auth         TEXT    NOT NULL,
                    criado_em    TEXT    NOT NULL)""")

        elif _v == 17:
            # barbeiros: username único (índice, caso em falta)
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_barbeiros_username "
                    "ON barbeiros(username) WHERE username IS NOT NULL")
            except sqlite3.OperationalError:
                pass

        elif _v == 18:
            # barbeiros: pausa de almoço permanente por profissional
            for col in ("pausa_almoco_inicio", "pausa_almoco_fim"):
                try:
                    conn.execute(f"ALTER TABLE barbeiros ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass   # coluna já existe

        elif _v == 19:
            # Tabela de push subscriptions de clientes (lembrete 24h antes)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cliente_push_subs (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    telefone   TEXT NOT NULL,
                    barbearia_id INTEGER NOT NULL,
                    endpoint   TEXT UNIQUE NOT NULL,
                    p256dh     TEXT NOT NULL,
                    auth       TEXT NOT NULL,
                    criado_em  TEXT NOT NULL
                )""")
            _done(19)

        elif _v == 20:
            # Fila de espera: clientes que querem uma vaga quando não há horários
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lista_espera (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    barbearia_id   INTEGER NOT NULL,
                    cliente_nome   TEXT NOT NULL,
                    telefone       TEXT NOT NULL,
                    servico_id     INTEGER,
                    barbeiro_id    INTEGER,
                    data_preferida TEXT NOT NULL,
                    slot_livre     INTEGER DEFAULT 0,
                    notificado     INTEGER DEFAULT 0,
                    criado_em      TEXT NOT NULL,
                    expira_em      TEXT NOT NULL
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_espera_barbearia ON lista_espera(barbearia_id, data_preferida, slot_livre)")
            _done(20)

        elif _v == 21:
            # clientes_bloqueados: impede clientes problemáticos de marcar
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clientes_bloqueados (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    barbearia_id INTEGER NOT NULL,
                    telefone     TEXT    NOT NULL,
                    motivo       TEXT    DEFAULT '',
                    bloqueado_em TEXT    NOT NULL,
                    UNIQUE (barbearia_id, telefone)
                )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cli_bloq ON clientes_bloqueados(barbearia_id)")
            _done(21)

        elif _v == 22:
            # rate_limit: tabelas para o rate limiter persistente (SQLite dedicado, não aqui)
            # Migração vazia — o ficheiro rate_limit.db é gerido por db/rate_limit.py
            _done(22)

        elif _v == 23:
            # barbeiros: telefone para contacto via WhatsApp entre cliente e profissional
            try:
                conn.execute("ALTER TABLE barbeiros ADD COLUMN telefone TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass   # coluna já existe
            _done(23)

        elif _v == 24:
            # servicos: coluna ordem para reordenação manual
            try:
                conn.execute("ALTER TABLE servicos ADD COLUMN ordem INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            # Inicializar ordem pela ordenação actual (alfabética por nome)
            try:
                _rows_srv = conn.execute(
                    "SELECT id FROM servicos ORDER BY nome, id").fetchall()
                for _i, _r in enumerate(_rows_srv):
                    conn.execute("UPDATE servicos SET ordem=? WHERE id=?", (_i * 10, _r["id"]))
            except sqlite3.OperationalError:
                pass
            _done(24)

        elif _v == 25:
            # agendamentos: confirmação de presença pelo cliente
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN confirmado INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE agendamentos ADD COLUMN token_confirmar TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass
            _done(25)

        conn.commit()
        _done(_v)


# ── Inicialização ──────────────────────────────────────────

def init_db() -> None:
    conn = get_conn()
    try:
        # Durante a transição de deploy, o worker antigo pode ainda segurar um lock
        # EXCLUSIVE; busy_timeout (60s, em _connect) absorve essa espera até ele morrer.
        conn.execute("SELECT 1")

        c = conn.cursor()

        c.execute("""CREATE TABLE IF NOT EXISTS barbearias (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            ativa INTEGER DEFAULT 1)""")

        c.execute("""CREATE TABLE IF NOT EXISTS pagamentos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL REFERENCES barbearias(id),
            codigo_plano TEXT NOT NULL,
            nome_plano   TEXT NOT NULL,
            dias         INTEGER NOT NULL,
            preco        INTEGER DEFAULT 0,
            expira_em    TEXT NOT NULL,
            registado_em TEXT NOT NULL)""")

        c.execute("""CREATE TABLE IF NOT EXISTS barbeiros (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            nome         TEXT NOT NULL,
            barbearia_id INTEGER REFERENCES barbearias(id),
            ativo        INTEGER DEFAULT 1,
            role         TEXT DEFAULT 'barbeiro',
            username     TEXT UNIQUE,
            password_hash TEXT)""")

        c.execute("""CREATE TABLE IF NOT EXISTS servicos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            nome         TEXT NOT NULL,
            duracao_min  INTEGER NOT NULL DEFAULT 30,
            preco        INTEGER DEFAULT 0,
            ativo        INTEGER DEFAULT 1)""")

        c.execute("""CREATE TABLE IF NOT EXISTS agendamentos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            cliente      TEXT NOT NULL,
            telefone     TEXT,
            servico_id   INTEGER NOT NULL,
            barbeiro_id  INTEGER,
            data_hora    TEXT NOT NULL,
            inicio       TEXT,
            fim          TEXT,
            status       TEXT DEFAULT 'agendado',
            tipo         TEXT DEFAULT 'agendado',
            valor        INTEGER DEFAULT 0)""")

        c.execute("""CREATE TABLE IF NOT EXISTS horario_funcionamento (
            barbearia_id  INTEGER NOT NULL,
            dia_semana    INTEGER NOT NULL,
            hora_abertura TEXT DEFAULT '08:00',
            hora_fecho    TEXT DEFAULT '19:00',
            fechado       INTEGER DEFAULT 0,
            PRIMARY KEY (barbearia_id, dia_semana))""")

        c.execute("""CREATE TABLE IF NOT EXISTS dias_fechados (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            data         TEXT NOT NULL,
            motivo       TEXT,
            UNIQUE(barbearia_id, data))""")

        c.execute("""CREATE TABLE IF NOT EXISTS configuracoes (
            barbearia_id INTEGER NOT NULL,
            chave        TEXT NOT NULL,
            valor        TEXT,
            PRIMARY KEY (barbearia_id, chave))""")

        c.execute("""CREATE TABLE IF NOT EXISTS ausencias (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            barbeiro_id INTEGER NOT NULL,
            data_inicio TEXT NOT NULL,
            data_fim    TEXT NOT NULL,
            tipo        TEXT DEFAULT 'falta',
            motivo      TEXT,
            hora_inicio TEXT,
            hora_fim    TEXT)""")

        c.execute("""CREATE TABLE IF NOT EXISTS webauthn_credentials (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            barbeiro_id      INTEGER NOT NULL REFERENCES barbeiros(id),
            credential_id    TEXT    NOT NULL UNIQUE,
            public_key       TEXT    NOT NULL,
            sign_count       INTEGER DEFAULT 0,
            nome_dispositivo TEXT    DEFAULT 'Dispositivo',
            criado_em        TEXT    NOT NULL)""")

        c.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            barbeiro_id INTEGER NOT NULL,
            barbearia_id INTEGER NOT NULL,
            endpoint    TEXT NOT NULL UNIQUE,
            p256dh      TEXT NOT NULL,
            auth        TEXT NOT NULL,
            criado_em   TEXT NOT NULL)""")

        conn.commit()

        # Índices para acelerar queries frequentes
        for _idx in [
            "CREATE INDEX IF NOT EXISTS idx_ag_barbearia_data    ON agendamentos(barbearia_id, data_hora)",
            "CREATE INDEX IF NOT EXISTS idx_ag_barbeiro_status   ON agendamentos(barbeiro_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_ag_telefone          ON agendamentos(barbearia_id, telefone)",
            "CREATE INDEX IF NOT EXISTS idx_ag_barbearia_status  ON agendamentos(barbearia_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_ag_barbearia_status_data ON agendamentos(barbearia_id, status, data_hora)",
            "CREATE INDEX IF NOT EXISTS idx_barb_barbearia_ativo ON barbeiros(barbearia_id, ativo)",
            "CREATE INDEX IF NOT EXISTS idx_serv_barbearia_ativo ON servicos(barbearia_id, ativo)",
            "CREATE INDEX IF NOT EXISTS idx_ausencias_barbeiro   ON ausencias(barbeiro_id, data_inicio, data_fim)",
            # Unicidade do token de reagendamento (NULL excluído pelo WHERE parcial)
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_token_reagendar ON agendamentos(token_reagendar) WHERE token_reagendar IS NOT NULL",
        ]:
            try:
                conn.execute(_idx)
            except sqlite3.OperationalError:
                pass
        conn.commit()

        # Optimizar o plano de queries do SQLite com base nas estatísticas actuais
        try:
            conn.execute("PRAGMA optimize")
        except Exception:
            pass

        # Migrações numeradas — cada uma corre exactamente uma vez
        _run_migrations(conn)

        # Migrações declarativas (db/migrations.py) — motor independente
        from db.migrations import migrate as _migrate_declarative
        _migrate_declarative(conn)

        # Seed root (só se não existir)
        _root_row = c.execute("SELECT id, password_hash FROM barbeiros WHERE role='root' LIMIT 1").fetchone()
        _pw_file  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".root_init_password")
        if not _root_row:
            # Primeira instalação — gerar password aleatória
            _root_pw = secrets.token_urlsafe(16)
            try:
                with open(_pw_file, "w") as _f:
                    _f.write(f"username: root\npassword: {_root_pw}\n")
                os.chmod(_pw_file, 0o600)
            except Exception:
                pass
            c.execute(
                "INSERT INTO barbeiros (nome, role, username, password_hash) VALUES (?,?,?,?)",
                ("Root", "root", "root", generate_password_hash(_root_pw, method="pbkdf2:sha256:10000")))
            conn.commit()
        # Migração de emergência: se root tem hash scrypt, substitui sem verificar a senha antiga.
        # Scrypt no Werkzeug 3.x leva ~300 s em VMs partilhadas → HARAKIRI uWSGI em cada login.
        # Corre uma vez no arranque — sem check_password_hash, sem bloqueio.
        elif _root_row and _root_row["password_hash"] and _root_row["password_hash"].startswith("scrypt:"):
            _nova_pw = secrets.token_urlsafe(16)
            _novo_hash = generate_password_hash(_nova_pw, method="pbkdf2:sha256:10000")
            conn.execute(
                "UPDATE barbeiros SET password_hash=? WHERE role='root'", (_novo_hash,))
            conn.commit()
            try:
                with open(_pw_file, "w") as _f:
                    _f.write(f"username: root\npassword: {_nova_pw}\n[scrypt migrado para pbkdf2 no arranque]\n")
                os.chmod(_pw_file, 0o600)
            except Exception:
                pass

    finally:
        pass  # Sem conn.close() — conexão persistente; fica aberta para todos os pedidos HTTP


# ── Configurações (aqui para evitar dependência circular) ──────────────────

def get_config(chave: str, barbearia_id: int, default: str | None = None) -> str | None:
    key = (barbearia_id, chave)
    with _config_cache_lock:
        entry = _config_cache.get(key)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
    with _read() as conn:
        row = conn.execute(
            "SELECT valor FROM configuracoes WHERE barbearia_id=? AND chave=?",
            (barbearia_id, chave)).fetchone()
    val = row["valor"] if row else default
    with _config_cache_lock:
        _config_cache[key] = (val, time.monotonic() + _CONFIG_CACHE_TTL)
    return val


def set_config(chave: str, valor: str | int, barbearia_id: int) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
            (barbearia_id, chave, str(valor)))
    # Invalidar config cache para a chave alterada
    with _config_cache_lock:
        _config_cache.pop((barbearia_id, chave), None)
    # Invalidar cache de slots quando configurações que afectam disponibilidade mudam
    if chave in ("buffer_minutos", "max_por_dia"):
        invalidar_cache_slots(barbearia_id)
    # Invalidar cache de fuso quando timezone muda
    if chave == "timezone":
        with _tz_cache_lock:
            _tz_cache.pop(barbearia_id, None)


def get_todas_configs(barbearia_id: int) -> dict[str, str | None]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT chave, valor FROM configuracoes WHERE barbearia_id=?",
            (barbearia_id,)).fetchall()
    return {r["chave"]: r["valor"] for r in rows}

