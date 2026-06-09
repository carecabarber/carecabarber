# db/barbeiros.py — Barbeiros, autenticação, WebAuthn, fotos, ausências

import sqlite3
import secrets
import threading
from werkzeug.security import generate_password_hash, check_password_hash
from db._conn import _read, _write, _write_exclusive, _agora, FMT, _BARB_COLS


def get_barbeiro_por_username(username):
    with _read() as conn:
        row = conn.execute(
            "SELECT " + _BARB_COLS + " FROM barbeiros WHERE username=? AND ativo=1", (username,)).fetchone()
    return dict(row) if row else None


def verificar_senha(utilizador, senha):
    """Verifica a senha e migra automaticamente hashes scrypt → pbkdf2:sha256.
    Scrypt no Werkzeug 3.x pode levar centenas de segundos em VMs partilhadas —
    pbkdf2:sha256 é previsível e rápido."""
    if not utilizador or not utilizador["password_hash"]:
        return False
    ok = check_password_hash(utilizador["password_hash"], senha)
    if ok:
        _ph = utilizador["password_hash"]
        # Migração transparente: re-hash com pbkdf2:sha256:10000 na primeira login
        # bem-sucedida para hashes lentos (scrypt) ou com iterações elevadas (50000+)
        _needs_rehash = (
            _ph.startswith("scrypt:")
            or _ph.startswith("pbkdf2:sha256:50000:")
            or _ph.startswith("pbkdf2:sha256:260000:")
            or _ph.startswith("pbkdf2:sha256:600000:")
        )
        if _needs_rehash:
            import threading
            def _rehash():
                try:
                    new_hash = generate_password_hash(senha, method="pbkdf2:sha256:10000")
                    with _write() as conn:
                        conn.execute(
                            "UPDATE barbeiros SET password_hash=? WHERE id=? AND password_hash=?",
                            (new_hash, utilizador["id"], _ph))
                except Exception:
                    pass  # re-hash falhou — próxima login tenta outra vez
            threading.Thread(target=_rehash, daemon=True, name=f"rehash-{utilizador['id']}").start()
    return ok


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
                (username, generate_password_hash(senha, method="pbkdf2:sha256:10000"), id))
        return True
    except sqlite3.IntegrityError:
        return False


def alterar_senha(id, nova_senha):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET password_hash=? WHERE id=?",
                     (generate_password_hash(nova_senha, method="pbkdf2:sha256:10000"), id))


# ── Foto de perfil ─────────────────────────────────────────

def guardar_foto_perfil(barbeiro_id, dados: bytes, mime: str):
    """Guarda (ou substitui) a foto de perfil de um barbeiro."""
    with _write() as conn:
        conn.execute(
            "UPDATE barbeiros SET foto_perfil=?, foto_perfil_mime=? WHERE id=?",
            (dados, mime, barbeiro_id))


def get_foto_perfil(barbeiro_id):
    """Devolve (bytes, mime) ou (None, None) se não houver foto."""
    with _read() as conn:
        row = conn.execute(
            "SELECT foto_perfil, foto_perfil_mime FROM barbeiros WHERE id=?",
            (barbeiro_id,)).fetchone()
    if row and row["foto_perfil"]:
        return bytes(row["foto_perfil"]), row["foto_perfil_mime"] or "image/jpeg"
    return None, None


def apagar_foto_perfil(barbeiro_id):
    """Remove a foto de perfil de um barbeiro."""
    with _write() as conn:
        conn.execute(
            "UPDATE barbeiros SET foto_perfil=NULL, foto_perfil_mime=NULL WHERE id=?",
            (barbeiro_id,))


# ── WebAuthn / Biometria ───────────────────────────────────

def registar_credencial(barbeiro_id, credential_id, public_key, nome_dispositivo="Dispositivo"):
    """Guarda uma nova credencial WebAuthn para um barbeiro."""
    with _write() as conn:
        conn.execute(
            "INSERT INTO webauthn_credentials "
            "(barbeiro_id, credential_id, public_key, nome_dispositivo, criado_em) "
            "VALUES (?,?,?,?,?)",
            (barbeiro_id, credential_id, public_key,
             nome_dispositivo, _agora().strftime(FMT)))


def get_credenciais_barbeiro(barbeiro_id):
    """Lista todas as credenciais registadas de um barbeiro."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE barbeiro_id=? ORDER BY criado_em DESC",
            (barbeiro_id,)).fetchall()
    return [dict(r) for r in rows]


def get_credencial_por_id(credential_id):
    """Devolve uma credencial pelo credential_id (base64url)."""
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE credential_id=?",
            (credential_id,)).fetchone()
    return dict(row) if row else None


def atualizar_sign_count(id, sign_count):
    """Actualiza o contador de assinaturas de uma credencial (anti-replay)."""
    with _write() as conn:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count=? WHERE id=?",
            (sign_count, id))


def apagar_credencial(id, barbeiro_id):
    """Remove uma credencial, verificando que pertence ao barbeiro."""
    with _write() as conn:
        conn.execute(
            "DELETE FROM webauthn_credentials WHERE id=? AND barbeiro_id=?",
            (id, barbeiro_id))


# ── Barbeiros ──────────────────────────────────────────────

def listar_barbeiros(barbearia_id, apenas_ativos=True, incluir_chefe=False):
    with _read() as conn:
        if incluir_chefe:
            q = "SELECT " + _BARB_COLS + " FROM barbeiros WHERE barbearia_id=? AND role IN ('chefe','barbeiro')"
        else:
            q = "SELECT " + _BARB_COLS + " FROM barbeiros WHERE barbearia_id=? AND role='barbeiro'"
        if apenas_ativos:
            q += " AND ativo=1"
        rows = conn.execute(q + " ORDER BY nome", (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def criar_barbeiro(nome, barbearia_id):
    tok = secrets.token_urlsafe(32)   # 256 bits
    with _write() as conn:
        conn.execute(
            "INSERT INTO barbeiros (nome, role, barbearia_id, mesa_token) VALUES (?, 'barbeiro', ?, ?)",
            (nome, barbearia_id, tok))


def criar_chefe(nome, username, senha, barbearia_id):
    tok = secrets.token_urlsafe(32)   # 256 bits
    try:
        with _write() as conn:
            conn.execute(
                "INSERT INTO barbeiros (nome, role, barbearia_id, username, password_hash, mesa_token) VALUES (?,?,?,?,?,?)",
                (nome, "chefe", barbearia_id, username, generate_password_hash(senha, method="pbkdf2:sha256:10000"), tok))
        return True
    except sqlite3.IntegrityError:
        return False


def toggle_barbeiro(id):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET ativo = 1 - ativo WHERE id=?", (id,))


def contar_agendamentos_futuros_barbeiro(barbeiro_id, a_partir_de):
    """Conta agendamentos futuros com status 'agendado' para um barbeiro.
    Usado para impedir desativar barbeiros com agenda pendente."""
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM agendamentos "
            "WHERE barbeiro_id=? AND status='agendado' AND date(data_hora) >= ?",
            (barbeiro_id, a_partir_de)).fetchone()
    return row[0] if row else 0


def contar_chefes_ativos(barbearia_id, excluir_id=None):
    """Conta chefes activos na barbearia. Usado para impedir apagar o último chefe."""
    with _read() as conn:
        if excluir_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM barbeiros WHERE barbearia_id=? AND role='chefe' AND ativo=1 AND id!=?",
                (barbearia_id, excluir_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM barbeiros WHERE barbearia_id=? AND role='chefe' AND ativo=1",
                (barbearia_id,)).fetchone()
    return row[0] if row else 0


def apagar_barbeiro(barbeiro_id, barbearia_id):
    """Apaga um barbeiro.
    - Sem histórico: hard delete.
    - Com histórico (agendamentos passados): soft delete — remove credenciais e desativa.
    Devolve 'hard' ou 'soft'.
    """
    with _write() as conn:
        n_total = conn.execute(
            "SELECT COUNT(*) FROM agendamentos WHERE barbeiro_id=?",
            (barbeiro_id,)).fetchone()[0]
        if n_total == 0:
            conn.execute("DELETE FROM barbeiros WHERE id=? AND barbearia_id=?",
                         (barbeiro_id, barbearia_id))
            return "hard"
        else:
            # Soft: desativar + limpar credenciais de acesso
            conn.execute(
                "UPDATE barbeiros SET ativo=0, username=NULL, password_hash=NULL, mesa_token=NULL "
                "WHERE id=? AND barbearia_id=?",
                (barbeiro_id, barbearia_id))
            return "soft"


def get_barbeiro(id):
    if not id:
        return None
    with _read() as conn:
        row = conn.execute("SELECT " + _BARB_COLS + " FROM barbeiros WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def get_barbeiro_por_mesa_token(token):
    """Devolve barbeiro activo associado ao mesa_token (URL do QR de mesa)."""
    if not token:
        return None
    with _read() as conn:
        row = conn.execute(
            "SELECT " + _BARB_COLS + " FROM barbeiros WHERE mesa_token=? AND ativo=1", (token,)).fetchone()
    return dict(row) if row else None


def get_agendamentos_mesa(barbeiro_id, barbearia_id, data):
    """Agendamentos de hoje para a mesa: agendado, walk-in e em_andamento."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT a.*, s.nome AS servico_nome, s.duracao_min, s.preco "
            "FROM agendamentos a "
            "LEFT JOIN servicos s ON s.id=a.servico_id "
            "WHERE a.barbeiro_id=? AND a.barbearia_id=? "
            "AND date(a.data_hora)=? "
            "AND a.status IN ('agendado','walk-in','em_andamento') "
            "ORDER BY a.data_hora",
            (barbeiro_id, barbearia_id, data)).fetchall()
    return [dict(r) for r in rows]


def get_barbeiros_por_ids(ids):
    """Batch-fetch: devolve dict {id: barbeiro} para uma lista de IDs (1 query)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    with _read() as conn:
        rows = conn.execute(
            f"SELECT {_BARB_COLS} FROM barbeiros WHERE id IN ({ph})", ids
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def editar_barbeiro(id, nome, barbearia_id=None):
    with _write() as conn:
        if barbearia_id:
            conn.execute("UPDATE barbeiros SET nome=? WHERE id=? AND barbearia_id=?",
                         (nome, id, barbearia_id))
        else:
            conn.execute("UPDATE barbeiros SET nome=? WHERE id=?", (nome, id))


def repor_senha_barbeiro(id, nova_senha):
    with _write() as conn:
        conn.execute("UPDATE barbeiros SET password_hash=? WHERE id=?",
                     (generate_password_hash(nova_senha, method="pbkdf2:sha256:10000"), id))


def set_pausa_almoco(barbeiro_id, barbearia_id, inicio, fim):
    """Define ou remove a pausa de almoço permanente de um profissional.
    inicio/fim: string "HH:MM" ou None para remover.
    """
    with _write() as conn:
        conn.execute(
            "UPDATE barbeiros SET pausa_almoco_inicio=?, pausa_almoco_fim=? "
            "WHERE id=? AND barbearia_id=?",
            (inicio or None, fim or None, barbeiro_id, barbearia_id))

