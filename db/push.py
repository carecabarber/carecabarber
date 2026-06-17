# db/push.py — Web Push subscriptions

from datetime import datetime, timezone
from db._conn import _read, _write, FMT, _agora, ST_CONCLUIDO

_S_CONC = f"'{ST_CONCLUIDO}'"  # 'concluido'


def push_guardar(barbeiro_id: int, barbearia_id: int, endpoint: str, p256dh: str, auth: str) -> None:
    """Guarda ou actualiza uma subscripção Web Push."""
    agora = datetime.now(timezone.utc).strftime(FMT)
    with _write() as conn:
        conn.execute("""
            INSERT INTO push_subscriptions (barbeiro_id, barbearia_id, endpoint, p256dh, auth, criado_em)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
                barbeiro_id=excluded.barbeiro_id,
                barbearia_id=excluded.barbearia_id,
                p256dh=excluded.p256dh,
                auth=excluded.auth,
                criado_em=excluded.criado_em
        """, (barbeiro_id, barbearia_id, endpoint, p256dh, auth, agora))


def push_remover(endpoint: str) -> None:
    with _write() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))


def push_listar(barbearia_id: int, barbeiro_id: int | None = None) -> list[dict]:
    """Lista subscripções activas. Se barbeiro_id fornecido, filtra pelo barbeiro."""
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(
                "SELECT * FROM push_subscriptions WHERE barbearia_id=? AND barbeiro_id=?",
                (barbearia_id, barbeiro_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM push_subscriptions WHERE barbearia_id=?",
                (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def push_remover_expiradas(endpoints_invalidos: list[str]) -> None:
    if not endpoints_invalidos:
        return
    with _write() as conn:
        for ep in endpoints_invalidos:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (ep,))


def cliente_push_guardar(telefone: str, barbearia_id: int, endpoint: str, p256dh: str, auth: str) -> None:
    agora = datetime.now(timezone.utc).strftime(FMT)
    with _write() as conn:
        conn.execute("""
            INSERT INTO cliente_push_subs (telefone, barbearia_id, endpoint, p256dh, auth, criado_em)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
                telefone=excluded.telefone,
                barbearia_id=excluded.barbearia_id,
                p256dh=excluded.p256dh,
                auth=excluded.auth,
                criado_em=excluded.criado_em
        """, (telefone, barbearia_id, endpoint, p256dh, auth, agora))


def cliente_push_remover(endpoint: str) -> None:
    with _write() as conn:
        conn.execute("DELETE FROM cliente_push_subs WHERE endpoint=?", (endpoint,))


def cliente_push_listar_por_tel(telefone: str, barbearia_id: int) -> list[dict]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM cliente_push_subs WHERE telefone=? AND barbearia_id=?",
            (telefone, barbearia_id)).fetchall()
    return [dict(r) for r in rows]


def resumo_hoje(barbearia_id: int, barbeiro_id: int | None = None) -> dict:
    hoje = _agora(barbearia_id).strftime("%Y-%m-%d")
    with _read() as conn:
        q      = ("SELECT * FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
                  f"AND status={_S_CONC}")
        params = [barbearia_id, f"{hoje}%"]
        if barbeiro_id:
            q += " AND barbeiro_id=?"; params.append(barbeiro_id)
        rows = conn.execute(q, params).fetchall()
    return {"clientes": len(rows), "valor": sum(r["valor"] or 0 for r in rows)}

