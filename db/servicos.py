# db/servicos.py — Serviços

from db._conn import _read, _write, invalidar_cache_slots


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


def get_servicos_por_ids(ids):
    """Batch-fetch: devolve dict {id: servico} para uma lista de IDs (1 query)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    with _read() as conn:
        rows = conn.execute(f"SELECT * FROM servicos WHERE id IN ({ph})", ids).fetchall()
    return {r["id"]: dict(r) for r in rows}


def criar_servico(nome, duracao_min, barbearia_id, preco=0):
    with _write() as conn:
        conn.execute(
            "INSERT INTO servicos (barbearia_id,nome,duracao_min,preco) VALUES (?,?,?,?)",
            (barbearia_id, nome, duracao_min, preco or 0))


def atualizar_servico(id, nome, duracao_min, preco=0, barbearia_id=None):
    with _write() as conn:
        if barbearia_id:
            conn.execute(
                "UPDATE servicos SET nome=?, duracao_min=?, preco=? WHERE id=? AND barbearia_id=?",
                (nome, duracao_min, preco or 0, id, barbearia_id))
        else:
            conn.execute(
                "UPDATE servicos SET nome=?, duracao_min=?, preco=? WHERE id=?",
                (nome, duracao_min, preco or 0, id))


def apagar_servico(id, barbearia_id=None):
    with _write() as conn:
        em_uso = conn.execute(
            "SELECT id FROM agendamentos WHERE servico_id=? LIMIT 1", (id,)).fetchone()
        if barbearia_id:
            if em_uso:
                conn.execute("UPDATE servicos SET ativo=0 WHERE id=? AND barbearia_id=?",
                             (id, barbearia_id))
            else:
                conn.execute("DELETE FROM servicos WHERE id=? AND barbearia_id=?",
                             (id, barbearia_id))
        else:
            if em_uso:
                conn.execute("UPDATE servicos SET ativo=0 WHERE id=?", (id,))
            else:
                conn.execute("DELETE FROM servicos WHERE id=?", (id,))
    if barbearia_id:
        invalidar_cache_slots(barbearia_id)

