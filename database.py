import sqlite3
import os
import unicodedata
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "barbearia.db")
FMT = "%Y-%m-%d %H:%M:%S"

_SERVICOS_INICIAIS = [
    ("Corte Simples",        30),
    ("Corte Completo",       40),
    ("Corte + Barba",        45),
    ("Barba",                20),
    ("Corte Infantil",       25),
    ("Pé de Cabelo",         15),
    ("Sobrancelhas Mulher",  15),
    ("Sobrancelhas Homem",   10),
    ("Corte + Pé de Cabelo", 40),
    ("Navalhado",            30),
    ("Hidratação",           20),
    ("Penteado",             20),
]

_HORARIO_PADRAO = [
    (0, "08:00", "19:00", 0),
    (1, "08:00", "19:00", 0),
    (2, "08:00", "19:00", 0),
    (3, "08:00", "19:00", 0),
    (4, "08:00", "19:00", 0),
    (5, "08:00", "18:00", 0),
    (6, "00:00", "00:00", 1),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _write():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _read():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


# ── Slug helpers ───────────────────────────────────────────

def gerar_slug(nome):
    """Converte nome da barbearia para slug URL-amigável."""
    s = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s.strip('-') or 'barbearia'


def slug_unico(nome, excluir_id=None):
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


# ── Inicialização ──────────────────────────────────────────

def init_db():
    conn = get_conn()
    try:
        c = conn.cursor()

        c.execute("""CREATE TABLE IF NOT EXISTS barbearias (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            ativa INTEGER DEFAULT 1)""")

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

        conn.commit()

        # Migrations: adicionar colunas novas sem recriar tabela
        for _col, _def in [("slug", "TEXT"), ("logo", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE barbearias ADD COLUMN {_col} {_def}")
                conn.commit()
            except Exception:
                pass  # coluna já existe

        # Gerar slugs para barbearias que ainda não têm
        _sem_slug = conn.execute(
            "SELECT id, nome FROM barbearias WHERE slug IS NULL OR slug=''").fetchall()
        for _b in _sem_slug:
            _slug = slug_unico(_b["nome"], excluir_id=_b["id"])
            conn.execute("UPDATE barbearias SET slug=? WHERE id=?", (_slug, _b["id"]))
        if _sem_slug:
            conn.commit()

        # Seed root (só se não existir)
        if not c.execute("SELECT id FROM barbeiros WHERE role='root' LIMIT 1").fetchone():
            c.execute(
                "INSERT INTO barbeiros (nome, role, username, password_hash) VALUES (?,?,?,?)",
                ("Root", "root", "root", generate_password_hash("root1234")))
            conn.commit()

    finally:
        conn.close()


# ── Barbearias ─────────────────────────────────────────────

def listar_barbearias(apenas_ativas=False):
    with _read() as conn:
        q = "SELECT * FROM barbearias"
        if apenas_ativas:
            q += " WHERE ativa=1"
        rows = conn.execute(q + " ORDER BY nome").fetchall()
    return [dict(r) for r in rows]


def get_barbearia(id):
    with _read() as conn:
        row = conn.execute("SELECT * FROM barbearias WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def get_barbearia_por_slug(slug):
    with _read() as conn:
        row = conn.execute("SELECT * FROM barbearias WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def criar_barbearia(nome):
    with _write() as conn:
        cur = conn.execute("INSERT INTO barbearias (nome) VALUES (?)", (nome,))
        bid = cur.lastrowid
        for dia, ab, fecho, fechado in _HORARIO_PADRAO:
            conn.execute(
                "INSERT INTO horario_funcionamento (barbearia_id,dia_semana,hora_abertura,hora_fecho,fechado) VALUES (?,?,?,?,?)",
                (bid, dia, ab, fecho, fechado))
        for chave, valor in [("buffer_minutos", "10"), ("max_por_dia", "20")]:
            conn.execute(
                "INSERT INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
                (bid, chave, valor))
        for nome_s, dur in _SERVICOS_INICIAIS:
            conn.execute(
                "INSERT INTO servicos (barbearia_id,nome,duracao_min) VALUES (?,?,?)",
                (bid, nome_s, dur))
        slug = slug_unico(nome)
        conn.execute("UPDATE barbearias SET slug=? WHERE id=?", (slug, bid))
    return bid


def toggle_barbearia(id):
    with _write() as conn:
        conn.execute("UPDATE barbearias SET ativa = 1 - ativa WHERE id=?", (id,))


def editar_barbearia(id, nome):
    novo_slug = slug_unico(nome, excluir_id=id)
    with _write() as conn:
        conn.execute("UPDATE barbearias SET nome=?, slug=? WHERE id=?", (nome, novo_slug, id))


def set_logo(barbearia_id, filename):
    with _write() as conn:
        conn.execute("UPDATE barbearias SET logo=? WHERE id=?", (filename, barbearia_id))


# ── Configurações ──────────────────────────────────────────

def get_config(chave, barbearia_id, default=None):
    with _read() as conn:
        row = conn.execute(
            "SELECT valor FROM configuracoes WHERE barbearia_id=? AND chave=?",
            (barbearia_id, chave)).fetchone()
    return row["valor"] if row else default


def set_config(chave, valor, barbearia_id):
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
            (barbearia_id, chave, str(valor)))


def get_todas_configs(barbearia_id):
    with _read() as conn:
        rows = conn.execute(
            "SELECT chave, valor FROM configuracoes WHERE barbearia_id=?",
            (barbearia_id,)).fetchall()
    return {r["chave"]: r["valor"] for r in rows}


# ── Horário de funcionamento ───────────────────────────────

def get_horario(barbearia_id):
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM horario_funcionamento WHERE barbearia_id=? ORDER BY dia_semana",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def set_horario_dia(dia_semana, hora_abertura, hora_fecho, fechado, barbearia_id):
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO horario_funcionamento "
            "(barbearia_id,dia_semana,hora_abertura,hora_fecho,fechado) VALUES (?,?,?,?,?)",
            (barbearia_id, dia_semana, hora_abertura, hora_fecho, 1 if fechado else 0))


def get_horario_dia(dia_semana, barbearia_id):
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM horario_funcionamento WHERE barbearia_id=? AND dia_semana=?",
            (barbearia_id, dia_semana)).fetchone()
    return dict(row) if row else {"hora_abertura": "08:00", "hora_fecho": "19:00", "fechado": 0}


# ── Dias fechados ──────────────────────────────────────────

def listar_dias_fechados(barbearia_id):
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM dias_fechados WHERE barbearia_id=? ORDER BY data",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def adicionar_dia_fechado(data, motivo, barbearia_id):
    try:
        with _write() as conn:
            conn.execute(
                "INSERT INTO dias_fechados (barbearia_id,data,motivo) VALUES (?,?,?)",
                (barbearia_id, data, motivo))
    except Exception:
        pass


def remover_dia_fechado(id):
    with _write() as conn:
        conn.execute("DELETE FROM dias_fechados WHERE id=?", (id,))


def dia_esta_fechado(data_str, barbearia_id):
    with _read() as conn:
        row = conn.execute(
            "SELECT id FROM dias_fechados WHERE barbearia_id=? AND data=?",
            (barbearia_id, data_str)).fetchone()
    return row is not None


# ── Ausências de barbeiros ─────────────────────────────────

def listar_ausencias(barbearia_id, barbeiro_id=None):
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(
                "SELECT a.*, b.nome as barbeiro_nome FROM ausencias a "
                "JOIN barbeiros b ON a.barbeiro_id=b.id "
                "WHERE b.barbearia_id=? AND a.barbeiro_id=? ORDER BY a.data_inicio DESC",
                (barbearia_id, barbeiro_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT a.*, b.nome as barbeiro_nome FROM ausencias a "
                "JOIN barbeiros b ON a.barbeiro_id=b.id "
                "WHERE b.barbearia_id=? ORDER BY a.data_inicio DESC",
                (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def criar_ausencia(barbeiro_id, data_inicio, data_fim, tipo, motivo="", hora_inicio=None, hora_fim=None):
    with _write() as conn:
        conn.execute(
            "INSERT INTO ausencias (barbeiro_id,data_inicio,data_fim,tipo,motivo,hora_inicio,hora_fim) VALUES (?,?,?,?,?,?,?)",
            (barbeiro_id, data_inicio, data_fim, tipo, motivo, hora_inicio, hora_fim))


def apagar_ausencia(id):
    with _write() as conn:
        conn.execute("DELETE FROM ausencias WHERE id=?", (id,))


def ausencia_ativa(barbeiro_id, data_str, hora_str=None):
    with _read() as conn:
        rows = conn.execute(
            "SELECT a.*, b.nome as barbeiro_nome FROM ausencias a "
            "JOIN barbeiros b ON a.barbeiro_id=b.id "
            "WHERE a.barbeiro_id=? AND a.data_inicio<=? AND a.data_fim>=?",
            (barbeiro_id, data_str, data_str)).fetchall()

    def _hm(t):
        try:
            parts = t.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 0

    for a in rows:
        if not a["hora_inicio"] and not a["hora_fim"]:
            return dict(a)
        if hora_str and a["hora_inicio"] and a["hora_fim"]:
            h   = _hm(hora_str)
            ini = _hm(a["hora_inicio"])
            fim = _hm(a["hora_fim"])
            if ini < fim:
                if ini <= h < fim:
                    return dict(a)
            else:
                if h >= ini or h < fim:
                    return dict(a)
    return None


def barbeiro_ausente(barbeiro_id, data_str, hora_str=None):
    return ausencia_ativa(barbeiro_id, data_str, hora_str) is not None


# ── Autenticação ───────────────────────────────────────────

def get_barbeiro_por_username(username):
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM barbeiros WHERE username=? AND ativo=1", (username,)).fetchone()
    return dict(row) if row else None


def verificar_senha(utilizador, senha):
    if not utilizador or not utilizador["password_hash"]:
        return False
    return check_password_hash(utilizador["password_hash"], senha)


def username_existe(username):
    """Verifica se um username já está em uso (qualquer role)."""
    with _read() as conn:
        row = conn.execute(
            "SELECT id FROM barbeiros WHERE username=?", (username,)).fetchone()
    return row is not None


def set_credenciais(id, username, senha):
    try:
        with _write() as conn:
            conn.execute(
                "UPDATE barbeiros SET username=?, password_hash=? WHERE id=?",
                (username, generate_password_hash(senha), id))
        return True
    except sqlite3.IntegrityError:
        return False


def alterar_senha(id, nova_senha):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET password_hash=? WHERE id=?",
                     (generate_password_hash(nova_senha), id))


# ── Barbeiros ──────────────────────────────────────────────

def listar_barbeiros(barbearia_id, apenas_ativos=True, incluir_chefe=False):
    with _read() as conn:
        if incluir_chefe:
            q = "SELECT * FROM barbeiros WHERE barbearia_id=? AND role IN ('chefe','barbeiro')"
        else:
            q = "SELECT * FROM barbeiros WHERE barbearia_id=? AND role='barbeiro'"
        if apenas_ativos:
            q += " AND ativo=1"
        rows = conn.execute(q + " ORDER BY nome", (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def criar_barbeiro(nome, barbearia_id):
    with _write() as conn:
        conn.execute(
            "INSERT INTO barbeiros (nome, role, barbearia_id) VALUES (?, 'barbeiro', ?)",
            (nome, barbearia_id))


def criar_chefe(nome, username, senha, barbearia_id):
    try:
        with _write() as conn:
            conn.execute(
                "INSERT INTO barbeiros (nome, role, barbearia_id, username, password_hash) VALUES (?,?,?,?,?)",
                (nome, "chefe", barbearia_id, username, generate_password_hash(senha)))
        return True
    except sqlite3.IntegrityError:
        return False


def toggle_barbeiro(id):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET ativo = 1 - ativo WHERE id=?", (id,))


def get_barbeiro(id):
    if not id:
        return None
    with _read() as conn:
        row = conn.execute("SELECT * FROM barbeiros WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def editar_barbeiro(id, nome):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET nome=? WHERE id=?", (nome, id))


def repor_senha_barbeiro(id, nova_senha):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET password_hash=? WHERE id=?",
                     (generate_password_hash(nova_senha), id))


# ── Serviços ───────────────────────────────────────────────

def listar_servicos(barbearia_id, apenas_ativos=True):
    with _read() as conn:
        q = "SELECT * FROM servicos WHERE barbearia_id=?"
        if apenas_ativos:
            q += " AND ativo=1"
        rows = conn.execute(q + " ORDER BY nome", (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def servico_por_id(servico_id):
    if not servico_id:
        return None
    with _read() as conn:
        row = conn.execute("SELECT * FROM servicos WHERE id=?", (servico_id,)).fetchone()
    return dict(row) if row else None


def criar_servico(nome, duracao_min, barbearia_id, preco=0):
    with _write() as conn:
        conn.execute(
            "INSERT INTO servicos (barbearia_id,nome,duracao_min,preco) VALUES (?,?,?,?)",
            (barbearia_id, nome, duracao_min, preco or 0))


def atualizar_servico(id, nome, duracao_min, preco=0):
    with _write() as conn:
        conn.execute(
            "UPDATE servicos SET nome=?, duracao_min=?, preco=? WHERE id=?",
            (nome, duracao_min, preco or 0, id))


def apagar_servico(id):
    with _write() as conn:
        em_uso = conn.execute(
            "SELECT id FROM agendamentos WHERE servico_id=? LIMIT 1", (id,)).fetchone()
        if em_uso:
            conn.execute("UPDATE servicos SET ativo=0 WHERE id=?", (id,))
        else:
            conn.execute("DELETE FROM servicos WHERE id=?", (id,))


# ── Agendamentos ───────────────────────────────────────────

def criar_agendamento(cliente_nome, servico_id, data_hora, barbearia_id,
                      barbeiro_id=None, tipo="agendado", valor=0, telefone=None):
    with _write() as conn:
        cur = conn.execute(
            "INSERT INTO agendamentos "
            "(barbearia_id,cliente,telefone,servico_id,data_hora,barbeiro_id,tipo,valor) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (barbearia_id, cliente_nome, telefone, servico_id, data_hora,
             barbeiro_id or None, tipo, valor or 0))
        return cur.lastrowid


def marcar_nao_compareceu(id):
    with _write() as conn:
        conn.execute(
            "UPDATE agendamentos SET status='nao_compareceu' WHERE id=? AND status='agendado'", (id,))


def listar_hoje(barbearia_id, barbeiro_id=None):
    hoje = datetime.now().strftime("%Y-%m-%d")
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(
                "SELECT * FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
                "AND barbeiro_id=? ORDER BY data_hora",
                (barbearia_id, f"{hoje}%", barbeiro_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
                "ORDER BY data_hora",
                (barbearia_id, f"{hoje}%")).fetchall()
    return rows


def listar_todos(barbearia_id, barbeiro_id=None, data=None):
    base   = "SELECT * FROM agendamentos WHERE barbearia_id=?"
    params = [barbearia_id]
    if barbeiro_id:
        base += " AND barbeiro_id=?"; params.append(barbeiro_id)
    if data:
        base += " AND date(data_hora)=?"; params.append(data)
    base += " ORDER BY data_hora DESC"
    with _read() as conn:
        rows = conn.execute(base, params).fetchall()
    return rows


def listar_datas_historico(barbearia_id, barbeiro_id=None):
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute("""
                SELECT date(data_hora) AS data,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status='concluido' THEN 1 ELSE 0 END) AS concluidos,
                       SUM(CASE WHEN status='concluido' THEN COALESCE(valor,0) ELSE 0 END) AS valor
                FROM agendamentos WHERE barbearia_id=? AND barbeiro_id=?
                GROUP BY date(data_hora) ORDER BY date(data_hora) DESC
            """, (barbearia_id, barbeiro_id)).fetchall()
        else:
            rows = conn.execute("""
                SELECT date(data_hora) AS data,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status='concluido' THEN 1 ELSE 0 END) AS concluidos,
                       SUM(CASE WHEN status='concluido' THEN COALESCE(valor,0) ELSE 0 END) AS valor
                FROM agendamentos WHERE barbearia_id=?
                GROUP BY date(data_hora) ORDER BY date(data_hora) DESC
            """, (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def listar_por_telefone(telefone, barbearia_id):
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            "ORDER BY data_hora DESC",
            (barbearia_id, telefone)).fetchall()
    return rows


def get_agendamento(id):
    with _read() as conn:
        row = conn.execute("SELECT * FROM agendamentos WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def iniciar_trabalho(id):
    with _write() as conn:
        conn.execute(
            "UPDATE agendamentos SET inicio=?, status='em_andamento' "
            "WHERE id=? AND status IN ('agendado','walk-in')",
            (datetime.now().strftime(FMT), id))


def terminar_trabalho(id, valor=0):
    with _write() as conn:
        conn.execute(
            "UPDATE agendamentos SET fim=?, status='concluido', valor=? "
            "WHERE id=? AND status='em_andamento'",
            (datetime.now().strftime(FMT), valor or 0, id))


def estado_hoje(barbearia_id, barbeiro_id=None):
    hoje = datetime.now().strftime("%Y-%m-%d")
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(
                "SELECT id, status FROM agendamentos WHERE barbearia_id=? "
                "AND data_hora LIKE ? AND barbeiro_id=? ORDER BY id",
                (barbearia_id, f"{hoje}%", barbeiro_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, status FROM agendamentos WHERE barbearia_id=? "
                "AND data_hora LIKE ? ORDER BY id",
                (barbearia_id, f"{hoje}%")).fetchall()
    return "|".join(f"{r['id']}:{r['status']}" for r in rows)


def estado_cliente(telefone, barbearia_id):
    if not telefone:
        return ""
    with _read() as conn:
        rows = conn.execute(
            "SELECT id, status FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            "ORDER BY id DESC LIMIT 20",
            (barbearia_id, telefone)).fetchall()
    return "|".join(f"{r['id']}:{r['status']}" for r in rows)


def cancelar_agendamento(id):
    with _write() as conn:
        conn.execute("UPDATE agendamentos SET status='cancelado' WHERE id=?", (id,))


def reagendar_agendamento(id, nova_data_hora, novo_barbeiro_id=None):
    with _write() as conn:
        if novo_barbeiro_id:
            conn.execute(
                "UPDATE agendamentos SET data_hora=?, barbeiro_id=?, status='agendado' WHERE id=?",
                (nova_data_hora, novo_barbeiro_id, id))
        else:
            conn.execute(
                "UPDATE agendamentos SET data_hora=?, status='agendado' WHERE id=?",
                (nova_data_hora, id))


# ── Disponibilidade ────────────────────────────────────────

def verificar_disponibilidade(barbeiro_id, data_hora_str, duracao_min, barbearia_id, excluir_id=None):
    if not barbeiro_id or not data_hora_str:
        return True, None
    fmt_in = FMT if len(data_hora_str) == 19 else "%Y-%m-%d %H:%M"
    try:
        inicio_novo = datetime.strptime(data_hora_str, fmt_in)
    except (ValueError, TypeError):
        return True, None
    buffer   = int(get_config("buffer_minutos", barbearia_id, 10))
    fim_novo = inicio_novo + timedelta(minutes=duracao_min + buffer)

    q = ("SELECT a.*, s.duracao_min AS dur FROM agendamentos a "
         "LEFT JOIN servicos s ON a.servico_id = s.id "
         "WHERE a.barbeiro_id=? AND a.status NOT IN ('cancelado','concluido','nao_compareceu')")
    params = [barbeiro_id]
    if excluir_id:
        q += " AND a.id != ?"; params.append(excluir_id)
    with _read() as conn:
        rows = conn.execute(q, params).fetchall()

    for row in rows:
        dur = row["dur"] if row["dur"] else 30
        ref = row["inicio"] if row["status"] == "em_andamento" and row["inicio"] else row["data_hora"]
        try:
            inicio_ex = datetime.strptime(ref, FMT)
        except (ValueError, TypeError):
            try:
                inicio_ex = datetime.strptime(ref, "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                continue
        fim_ex = inicio_ex + timedelta(minutes=dur + buffer)
        if inicio_novo < fim_ex and fim_novo > inicio_ex:
            return False, dict(row)
    return True, None


def horarios_disponiveis(barbeiro_id, data_str, duracao_min, barbearia_id):
    try:
        weekday = datetime.strptime(data_str, "%Y-%m-%d").weekday()
    except (ValueError, TypeError):
        return []
    horario = get_horario_dia(weekday, barbearia_id)
    if horario["fechado"] or dia_esta_fechado(data_str, barbearia_id):
        return []

    buffer  = int(get_config("buffer_minutos", barbearia_id, 10))
    max_dia = int(get_config("max_por_dia",    barbearia_id, 20))

    with _read() as conn:
        q = ("SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
             "AND status NOT IN ('cancelado','concluido','nao_compareceu')")
        params = [barbearia_id, f"{data_str}%"]
        if barbeiro_id:
            q += " AND barbeiro_id=?"; params.append(barbeiro_id)
        total_dia = conn.execute(q, params).fetchone()[0]

        appt_rows = []
        if barbeiro_id:
            appt_rows = conn.execute(
                "SELECT a.*, s.duracao_min AS dur FROM agendamentos a "
                "LEFT JOIN servicos s ON a.servico_id = s.id "
                "WHERE a.barbearia_id=? AND a.barbeiro_id=? AND a.data_hora LIKE ? "
                "AND a.status NOT IN ('cancelado','concluido','nao_compareceu')",
                (barbearia_id, barbeiro_id, f"{data_str}%")).fetchall()

    if total_dia >= max_dia:
        return []

    abertura = datetime.strptime(f"{data_str} {horario['hora_abertura']}:00", FMT)
    fecho    = datetime.strptime(f"{data_str} {horario['hora_fecho']}:00",    FMT)
    agora    = datetime.now()

    # Gerar todos os slots de 10 em 10 minutos
    candidatos = {}
    slot = abertura
    while slot + timedelta(minutes=duracao_min) <= fecho:
        candidatos[slot.strftime("%H:%M")] = "normal"
        slot += timedelta(minutes=10)

    # Adicionar encaixe logo após cada agendamento se não cair num slot de 10 min
    for r in appt_rows:
        dur = r["dur"] if r["dur"] else 30
        ref = r["inicio"] if r["status"] == "em_andamento" and r["inicio"] else r["data_hora"]
        try:
            inicio_r = datetime.strptime(ref, FMT)
        except (ValueError, TypeError):
            try:
                inicio_r = datetime.strptime(ref, "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                continue
        apos     = inicio_r + timedelta(minutes=dur + buffer)
        apos_str = apos.strftime("%H:%M")
        if apos >= abertura and apos + timedelta(minutes=duracao_min) <= fecho:
            if apos_str not in candidatos:
                candidatos[apos_str] = "encaixe"

    resultado = []
    for hora_str in sorted(candidatos):
        tipo = candidatos[hora_str]
        try:
            slot_dt = datetime.strptime(f"{data_str} {hora_str}:00", FMT)
        except ValueError:
            continue
        if data_str == agora.strftime("%Y-%m-%d") and slot_dt < agora - timedelta(minutes=5):
            continue
        if barbeiro_id and barbeiro_ausente(barbeiro_id, data_str, hora_str):
            continue
        livre, _ = verificar_disponibilidade(barbeiro_id, slot_dt.strftime(FMT), duracao_min, barbearia_id)
        resultado.append({"hora": hora_str, "tipo": tipo if livre else "ocupado"})

    return resultado


# ── Marcações do cliente ───────────────────────────────────

def agendamentos_cliente_barbeiro_dia(telefone, barbeiro_id, data_str, barbearia_id):
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            "AND barbeiro_id=? AND data_hora LIKE ? AND status IN ('agendado','em_andamento')",
            (barbearia_id, telefone, barbeiro_id, f"{data_str}%")).fetchall()
    return [dict(r) for r in rows]


# ── Lembretes ──────────────────────────────────────────────

def proximos_agendamentos(barbearia_id, minutos=20, barbeiro_id=None):
    agora  = datetime.now()
    limite = agora + timedelta(minutes=minutos)
    q      = ("SELECT * FROM agendamentos WHERE barbearia_id=? AND status='agendado' "
               "AND data_hora BETWEEN ? AND ?")
    params = [barbearia_id, agora.strftime(FMT), limite.strftime(FMT)]
    if barbeiro_id:
        q += " AND barbeiro_id=?"; params.append(barbeiro_id)
    with _read() as conn:
        rows = conn.execute(q, params).fetchall()
    return rows


# ── Estatísticas ───────────────────────────────────────────

def estatisticas(barbearia_id, barbeiro_id=None):
    from collections import Counter

    hoje   = datetime.now()
    d_hoje = hoje.strftime("%Y-%m-%d")
    d_sem  = (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")
    d_mes  = hoje.strftime("%Y-%m-01")

    def resumo(rows):
        return {"clientes": len(rows), "valor": sum(r["valor"] or 0 for r in rows)}

    with _read() as conn:
        def query(desde):
            q = ("SELECT * FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
                 "AND data_hora >= ?")
            p = [barbearia_id, f"{desde} 00:00:00"]
            if barbeiro_id:
                q += " AND barbeiro_id=?"; p.append(barbeiro_id)
            return conn.execute(q, p).fetchall()

        hoje_rows  = query(d_hoje)
        sem_rows   = query(d_sem)
        mes_rows   = query(d_mes)
        todos_rows = query("2000-01-01")

        contagem_servicos = Counter(r["servico_id"] for r in todos_rows)
        top_servicos = []
        for sid, count in contagem_servicos.most_common(5):
            s = servico_por_id(sid)
            if s:
                dur_real_vals = [v for v in (
                    duracao_real_minutos(r["inicio"], r["fim"])
                    for r in todos_rows if r["servico_id"] == sid and r["inicio"] and r["fim"]
                ) if v is not None]
                media = round(sum(dur_real_vals) / len(dur_real_vals), 1) if dur_real_vals else None
                top_servicos.append({
                    "nome": s["nome"], "duracao_estimada": s["duracao_min"],
                    "count": count, "media_real": media,
                })

        barbeiros_stats = []
        if not barbeiro_id:
            for b in listar_barbeiros(barbearia_id, apenas_ativos=False, incluir_chefe=True):
                b_mes = conn.execute(
                    "SELECT * FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
                    "AND barbeiro_id=? AND data_hora >= ?",
                    (barbearia_id, b["id"], f"{d_mes} 00:00:00")).fetchall()
                b_sem = conn.execute(
                    "SELECT * FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
                    "AND barbeiro_id=? AND data_hora >= ?",
                    (barbearia_id, b["id"], f"{d_sem} 00:00:00")).fetchall()
                barbeiros_stats.append({
                    "id": b["id"], "nome": b["nome"],
                    "clientes":     len(b_mes), "valor":     sum(r["valor"] or 0 for r in b_mes),
                    "clientes_sem": len(b_sem), "valor_sem": sum(r["valor"] or 0 for r in b_sem),
                })
            barbeiros_stats.sort(key=lambda x: x["clientes"], reverse=True)

        horas    = [r["data_hora"][11:13] for r in todos_rows]
        hora_top = Counter(horas).most_common(1)[0] if horas else None

    return {
        "hoje": resumo(hoje_rows), "semana": resumo(sem_rows), "mes": resumo(mes_rows),
        "top_servicos": top_servicos, "barbeiros_stats": barbeiros_stats, "hora_top": hora_top,
    }


def estatisticas_detalhadas_barbeiro(barbeiro_id, barbearia_id):
    from collections import Counter

    hoje   = datetime.now()
    d_hoje = hoje.strftime("%Y-%m-%d")
    d_sem  = (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")
    d_mes  = hoje.strftime("%Y-%m-01")

    def resumo(rows):
        return {"clientes": len(rows), "valor": sum(r["valor"] or 0 for r in rows)}

    def safe_weekday(dh):
        try:
            return datetime.strptime(dh[:10], "%Y-%m-%d").weekday()
        except (ValueError, TypeError):
            return None

    with _read() as conn:
        def query(desde):
            return conn.execute(
                "SELECT * FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
                "AND barbeiro_id=? AND data_hora >= ?",
                (barbearia_id, barbeiro_id, f"{desde} 00:00:00")).fetchall()

        hoje_rows  = query(d_hoje)
        sem_rows   = query(d_sem)
        mes_rows   = query(d_mes)
        todos_rows = query("2000-01-01")

        contagem = Counter(r["servico_id"] for r in todos_rows)
        top_servicos, gargalhos = [], []
        for sid, count in contagem.most_common(10):
            s = servico_por_id(sid)
            if not s:
                continue
            dur_vals = [v for v in (
                duracao_real_minutos(r["inicio"], r["fim"])
                for r in todos_rows if r["servico_id"] == sid and r["inicio"] and r["fim"]
            ) if v is not None]
            media = round(sum(dur_vals) / len(dur_vals), 1) if dur_vals else None
            delta = round(media - s["duracao_min"], 1) if media is not None else None
            entry = {"nome": s["nome"], "duracao_estimada": s["duracao_min"],
                     "count": count, "media_real": media, "delta": delta}
            top_servicos.append(entry)
            if delta and delta > 0:
                gargalhos.append(entry)
        gargalhos.sort(key=lambda x: x["delta"], reverse=True)

        horas_count = Counter(r["data_hora"][11:13] for r in todos_rows)
        horas_dist  = [{"hora": h, "count": c} for h, c in sorted(horas_count.items())]

        dias_count = Counter(
            wd for wd in (safe_weekday(r["data_hora"]) for r in todos_rows) if wd is not None)
        dias_dist = [{"dia_semana": d, "count": dias_count.get(d, 0)} for d in range(7)]

        atrasos = []
        for r in todos_rows:
            if r["inicio"]:
                try:
                    sch = datetime.strptime(r["data_hora"][:16], "%Y-%m-%d %H:%M")
                    act = datetime.strptime(r["inicio"][:16],    "%Y-%m-%d %H:%M")
                    atrasos.append((act - sch).total_seconds() / 60)
                except Exception:
                    pass
        media_atraso = round(sum(atrasos) / len(atrasos), 1) if atrasos else 0

        nc = conn.execute(
            "SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=? "
            "AND status='nao_compareceu' AND barbeiro_id=?",
            (barbearia_id, barbeiro_id)).fetchone()[0]

        recentes = conn.execute(
            "SELECT * FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
            "AND barbeiro_id=? ORDER BY data_hora DESC LIMIT 15",
            (barbearia_id, barbeiro_id)).fetchall()
        recentes_list = []
        for r in recentes:
            s = servico_por_id(r["servico_id"])
            recentes_list.append({
                **dict(r),
                "servico_nome":     s["nome"] if s else "—",
                "duracao_estimada": s["duracao_min"] if s else 0,
                "duracao_real":     duracao_real_minutos(r["inicio"], r["fim"]),
            })

        b = get_barbeiro(barbeiro_id)

    return {
        "barbeiro": dict(b) if b else {},
        "hoje": resumo(hoje_rows), "semana": resumo(sem_rows), "mes": resumo(mes_rows),
        "total_geral": len(todos_rows), "top_servicos": top_servicos, "gargalhos": gargalhos,
        "horas_dist": horas_dist, "dias_dist": dias_dist, "media_atraso": media_atraso,
        "nao_compareceu": nc, "recentes": recentes_list,
    }


# ── Helpers ────────────────────────────────────────────────

def duracao_real_minutos(inicio_str, fim_str):
    if not inicio_str or not fim_str:
        return None
    try:
        return int((datetime.strptime(fim_str, FMT) - datetime.strptime(inicio_str, FMT)).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def resumo_hoje(barbearia_id, barbeiro_id=None):
    hoje = datetime.now().strftime("%Y-%m-%d")
    with _read() as conn:
        q      = ("SELECT * FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
                  "AND status='concluido'")
        params = [barbearia_id, f"{hoje}%"]
        if barbeiro_id:
            q += " AND barbeiro_id=?"; params.append(barbeiro_id)
        rows = conn.execute(q, params).fetchall()
    return {"clientes": len(rows), "valor": sum(r["valor"] or 0 for r in rows)}
