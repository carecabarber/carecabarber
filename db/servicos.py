# db/servicos.py — Serviços

from db._conn import _read, _write, invalidar_cache_slots


def listar_servicos(barbearia_id: int, apenas_ativos: bool = True) -> list[dict]:
    with _read() as conn:
        q = "SELECT * FROM servicos WHERE barbearia_id=?"
        if apenas_ativos:
            q += " AND ativo=1"
        rows = conn.execute(q + " ORDER BY ordem, nome", (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def servico_por_id(servico_id: int | None) -> dict | None:
    if not servico_id:
        return None
    with _read() as conn:
        row = conn.execute("SELECT * FROM servicos WHERE id=?", (servico_id,)).fetchone()
    return dict(row) if row else None


def get_servicos_por_ids(ids: list[int]) -> dict[int, dict]:
    """Batch-fetch: devolve dict {id: servico} para uma lista de IDs (1 query)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    with _read() as conn:
        rows = conn.execute(f"SELECT * FROM servicos WHERE id IN ({ph})", ids).fetchall()
    return {r["id"]: dict(r) for r in rows}


def criar_servico(nome: str, duracao_min: int, barbearia_id: int, preco: int = 0) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT INTO servicos (barbearia_id,nome,duracao_min,preco) VALUES (?,?,?,?)",
            (barbearia_id, nome, duracao_min, preco or 0))


def atualizar_servico(id: int, nome: str, duracao_min: int, preco: int = 0, barbearia_id: int | None = None) -> None:
    with _write() as conn:
        if barbearia_id:
            conn.execute(
                "UPDATE servicos SET nome=?, duracao_min=?, preco=? WHERE id=? AND barbearia_id=?",
                (nome, duracao_min, preco or 0, id, barbearia_id))
        else:
            conn.execute(
                "UPDATE servicos SET nome=?, duracao_min=?, preco=? WHERE id=?",
                (nome, duracao_min, preco or 0, id))


def mover_servico(id: int, direcao: str, barbearia_id: int) -> None:
    """Troca a ordem do serviço com o adjacente (up=para cima, down=para baixo)."""
    with _write() as conn:
        rows = conn.execute(
            "SELECT id, ordem FROM servicos WHERE barbearia_id=? ORDER BY ordem, nome",
            (barbearia_id,)).fetchall()
        ids = [r["id"] for r in rows]
        if id not in ids:
            return
        idx = ids.index(id)
        swap = idx - 1 if direcao == "up" else idx + 1
        if swap < 0 or swap >= len(ids):
            return
        id_a, ord_a = rows[idx]["id"], rows[idx]["ordem"]
        id_b, ord_b = rows[swap]["id"], rows[swap]["ordem"]
        # Se ordem igual, usar posição como tiebreaker
        if ord_a == ord_b:
            ord_a, ord_b = idx * 10, swap * 10
        conn.execute("UPDATE servicos SET ordem=? WHERE id=?", (ord_b, id_a))
        conn.execute("UPDATE servicos SET ordem=? WHERE id=?", (ord_a, id_b))
    invalidar_cache_slots(barbearia_id)


def toggle_servico_ativo(id: int, barbearia_id: int, ativo: int) -> None:
    with _write() as conn:
        conn.execute("UPDATE servicos SET ativo=? WHERE id=? AND barbearia_id=?",
                     (ativo, id, barbearia_id))
    invalidar_cache_slots(barbearia_id)


def apagar_servico(id: int, barbearia_id: int | None = None) -> None:
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

