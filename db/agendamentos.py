# db/agendamentos.py — Agendamentos, disponibilidade, estado, bloqueios, tokens

import secrets
import hashlib
from datetime import datetime, timedelta
from db._conn import (
    _read, _write, _write_exclusive, _agora, FMT,
    ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO, ST_CANCELADO, ST_NAO_COMP, ST_WALKIN,
    normalizar_tel, invalidar_cache_slots,
)


def criar_agendamento(cliente_nome: str, servico_id: int, data_hora: str, barbearia_id: int,
                      barbeiro_id: int | None = None, tipo: str = ST_AGENDADO, valor: int = 0,
                      telefone: str | None = None, notas: str | None = None) -> int:
    token    = secrets.token_urlsafe(32)   # 256 bits — tokens públicos de longa duração
    token_av = secrets.token_urlsafe(32)
    token_cf = secrets.token_urlsafe(20)   # token de confirmação de presença
    criado   = _agora().strftime(FMT)
    tel      = normalizar_tel(telefone) or None   # normalizar antes de guardar
    # Guardar nome do barbeiro no momento da marcação — persiste se barbeiro for apagado
    barb_snap = None
    if barbeiro_id:
        try:
            with _read() as c:
                _brow = c.execute("SELECT nome FROM barbeiros WHERE id=?", (barbeiro_id,)).fetchone()
            barb_snap = _brow["nome"] if _brow else None
        except Exception:
            pass
    # BEGIN IMMEDIATE garante atomicidade do check+insert mesmo com múltiplos workers
    with _write_exclusive() as conn:
        cur = conn.execute(
            "INSERT INTO agendamentos "
            "(barbearia_id,cliente,telefone,servico_id,data_hora,barbeiro_id,tipo,valor,"
            "token_reagendar,criado_em,notas,token_avaliar,barbeiro_nome_snap,token_confirmar) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (barbearia_id, cliente_nome, tel, servico_id, data_hora,
             barbeiro_id or None, tipo, valor or 0, token, criado, notas or None, token_av,
             barb_snap, token_cf))
        lid = cur.lastrowid
    invalidar_cache_slots(barbearia_id)
    return lid


def confirmar_agendamento(token: str) -> dict | None:
    """Marca agendamento como confirmado via token. Devolve o registo (antes da update)."""
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM agendamentos WHERE token_confirmar=?", (token,)).fetchone()
        if not row:
            return None
        if not row["confirmado"]:
            conn.execute(
                "UPDATE agendamentos SET confirmado=1 WHERE token_confirmar=?", (token,))
    return dict(row)


def contar_marcacoes_cliente_dia(telefone: str, data: str, barbearia_id: int) -> int:
    """Conta marcações ACTIVAS de um cliente num determinado dia.

    Só conta 'agendado', 'em_andamento' e 'walk-in' — marcações já concluídas ou
    com não-comparência não bloqueiam o cliente de remarcar o mesmo dia.
    """
    if not telefone:
        return 0
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM agendamentos "
            "WHERE barbearia_id=? AND telefone=? AND DATE(data_hora)=? "
            "AND status IN ('agendado', 'em_andamento', 'walk-in')",
            (barbearia_id, telefone, data)).fetchone()
    return (row["n"] if row else 0) or 0


def marcar_nao_compareceu(id: int) -> bool:
    with _write() as conn:
        conn.execute(
            f"UPDATE agendamentos SET status={_S_NC} WHERE id=? AND status={_S_AG}", (id,))
        return bool(conn.execute("SELECT changes()").fetchone()[0])


def listar_hoje(barbearia_id: int, barbeiro_id: int | None = None) -> list[dict]:
    hoje = _agora(barbearia_id).strftime("%Y-%m-%d")
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
    return [dict(r) for r in rows]


def listar_proximas_barbeiro(barbearia_id: int, barbeiro_id: int) -> list[dict]:
    """Agendamentos do barbeiro de hoje em diante, excluindo concluídos/cancelados/nc.
    Ordenados por data_hora ASC — para a aba 'Marcações' do barbeiro."""
    hoje = _agora(barbearia_id).strftime("%Y-%m-%d") + " 00:00:00"
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM agendamentos "
            "WHERE barbearia_id=? AND barbeiro_id=? "
            "AND data_hora >= ? "
            f"AND status NOT IN {_ST_EXCLUIDOS} "
            "ORDER BY data_hora ASC",
            (barbearia_id, barbeiro_id, hoje)).fetchall()
    return [dict(r) for r in rows]


_STATUS_VALIDOS = {ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO,
                   ST_CANCELADO, ST_NAO_COMP, ST_WALKIN}

# Fragmentos SQL derivados das constantes — evitam strings mágicas nas queries.
# Se o valor de uma constante mudar, todas as queries acompanham automaticamente.
_S_AG   = f"'{ST_AGENDADO}'"      # 'agendado'
_S_EM   = f"'{ST_EM_ANDAMENTO}'"  # 'em_andamento'
_S_CONC = f"'{ST_CONCLUIDO}'"     # 'concluido'
_S_CANC = f"'{ST_CANCELADO}'"     # 'cancelado'
_S_NC   = f"'{ST_NAO_COMP}'"      # 'nao_compareceu'
_S_WK   = f"'{ST_WALKIN}'"        # 'walk-in'

_ST_EXCLUIDOS = f"({_S_CANC},{_S_CONC},{_S_NC})"    # cancelado, concluido, nao_compareceu
_ST_ATIVOS    = f"({_S_AG},{_S_WK})"                 # agendado, walk-in
_ST_ATIVOS_EM = f"({_S_AG},{_S_WK},{_S_EM})"        # agendado, walk-in, em_andamento
_ST_AG_EM     = f"({_S_AG},{_S_EM})"                 # agendado, em_andamento
_ST_EM_WK     = f"({_S_EM},{_S_WK})"                 # em_andamento, walk-in


def listar_todos(barbearia_id: int, barbeiro_id: int | None = None, data: str | None = None,
                 data_ini: str | None = None, data_fim: str | None = None,
                 limit: int | None = None, offset: int = 0, status: str | None = None) -> list[dict]:
    base   = "SELECT * FROM agendamentos WHERE barbearia_id=?"
    params = [barbearia_id]
    if barbeiro_id:
        base += " AND barbeiro_id=?"; params.append(barbeiro_id)
    if data:
        # Comparação de prefixo de string em vez de date() SQLite (que usa UTC).
        # Evita desvio de fuso: um agendamento das 23:30 CVT (00:30 UTC) ficava no dia seguinte.
        data_fim_excl = (datetime.strptime(data, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        base += " AND data_hora >= ? AND data_hora < ?"; params += [data + " 00:00:00", data_fim_excl + " 00:00:00"]
    elif data_ini and data_fim:
        # Garantir que data_ini <= data_fim (troca silenciosa se invertidos)
        if data_ini > data_fim:
            data_ini, data_fim = data_fim, data_ini
        data_fim_excl = (datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        base += " AND data_hora >= ? AND data_hora < ?"; params += [data_ini + " 00:00:00", data_fim_excl + " 00:00:00"]
    if status and status in _STATUS_VALIDOS:
        base += " AND status=?"; params.append(status)
    base += " ORDER BY data_hora DESC"
    if limit:
        base += " LIMIT ? OFFSET ?"; params += [limit, offset]
    elif data:
        # Filtro por data específica sem paginação — limite defensivo para evitar OOM
        base += " LIMIT 500"
    with _read() as conn:
        rows = conn.execute(base, params).fetchall()
    return [dict(r) for r in rows]


def contar_ativos_dia(barbearia_id: int, data_str: str) -> int:
    """Conta agendamentos activos (não cancelados/concluídos) num dia — para verificar capacidade."""
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
            f"AND status NOT IN {_ST_EXCLUIDOS}",
            (barbearia_id, f"{data_str}%")).fetchone()
    return row[0] if row else 0


def contar_todos(barbearia_id: int, barbeiro_id: int | None = None, data: str | None = None,
                 data_ini: str | None = None, data_fim: str | None = None,
                 status: str | None = None) -> int:
    """Conta o total de agendamentos (para paginação)."""
    base   = "SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=?"
    params = [barbearia_id]
    if barbeiro_id:
        base += " AND barbeiro_id=?"; params.append(barbeiro_id)
    if data:
        data_fim_excl = (datetime.strptime(data, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        base += " AND data_hora >= ? AND data_hora < ?"; params += [data + " 00:00:00", data_fim_excl + " 00:00:00"]
    elif data_ini and data_fim:
        # Garantir que data_ini <= data_fim
        if data_ini > data_fim:
            data_ini, data_fim = data_fim, data_ini
        data_fim_excl = (datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        base += " AND data_hora >= ? AND data_hora < ?"; params += [data_ini + " 00:00:00", data_fim_excl + " 00:00:00"]
    if status and status in _STATUS_VALIDOS:
        base += " AND status=?"; params.append(status)
    with _read() as conn:
        return conn.execute(base, params).fetchone()[0]


def listar_datas_historico(barbearia_id: int, barbeiro_id: int | None = None, dias: int = 180) -> list[dict]:
    """Devolve resumo por dia dos últimos `dias` dias (default 180).
    Limitar evita carregar anos de dados para o picker de datas."""
    # Usar _agora() com o fuso da barbearia em vez de date('now') do SQLite (que é UTC)
    cutoff = (_agora(barbearia_id=barbearia_id) - timedelta(days=dias)).strftime("%Y-%m-%d")
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(f"""
                SELECT date(data_hora) AS data,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status={_S_CONC} THEN 1 ELSE 0 END) AS concluidos,
                       SUM(CASE WHEN status={_S_CONC} THEN COALESCE(valor,0) ELSE 0 END) AS valor
                FROM agendamentos
                WHERE barbearia_id=? AND barbeiro_id=?
                  AND data_hora >= ?
                GROUP BY date(data_hora) ORDER BY date(data_hora) DESC
            """, (barbearia_id, barbeiro_id, cutoff)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT date(data_hora) AS data,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status={_S_CONC} THEN 1 ELSE 0 END) AS concluidos,
                       SUM(CASE WHEN status={_S_CONC} THEN COALESCE(valor,0) ELSE 0 END) AS valor
                FROM agendamentos
                WHERE barbearia_id=?
                  AND data_hora >= ?
                GROUP BY date(data_hora) ORDER BY date(data_hora) DESC
            """, (barbearia_id, cutoff)).fetchall()
    return [dict(r) for r in rows]


def listar_por_telefone(telefone: str, barbearia_id: int) -> list[dict]:
    tel = normalizar_tel(telefone)
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            "ORDER BY data_hora ASC",
            (barbearia_id, tel)).fetchall()
    return [dict(r) for r in rows]


def get_agendamento(id: int) -> dict | None:
    with _read() as conn:
        row = conn.execute("SELECT * FROM agendamentos WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def barbeiro_tem_em_andamento(barbeiro_id) -> bool:
    """Devolve True se o barbeiro já tem um serviço em_andamento agora.
    Inclui walk-ins acabados de criar (status='walk-in') para prevenir duplicados."""
    with _read() as conn:
        row = conn.execute(
            "SELECT id FROM agendamentos "
            f"WHERE barbeiro_id=? AND status IN {_ST_EM_WK} LIMIT 1",
            (barbeiro_id,)).fetchone()
    return row is not None


def get_servico_em_andamento(barbeiro_id) -> dict | None:
    """Devolve o agendamento em_andamento actual do barbeiro (ou None).
    Usado para mostrar qual serviço preso está a bloquear novas iniciações."""
    with _read() as conn:
        row = conn.execute(
            "SELECT a.*, s.nome AS servico_nome "
            "FROM agendamentos a "
            "LEFT JOIN servicos s ON s.id=a.servico_id "
            f"WHERE a.barbeiro_id=? AND a.status={_S_EM} LIMIT 1",
            (barbeiro_id,)).fetchone()
    return dict(row) if row else None


def barbeiro_proxima_marcacao_minutos(barbeiro_id, barbearia_id) -> int:
    """Devolve em quantos minutos é a próxima marcação agendada do barbeiro.
    Devolve 9999 se não houver nenhuma hoje."""
    agora = _agora(barbearia_id)
    hoje  = agora.strftime("%Y-%m-%d")
    agora_str = agora.strftime(FMT)
    with _read() as conn:
        row = conn.execute(
            "SELECT data_hora FROM agendamentos "
            "WHERE barbeiro_id=? AND barbearia_id=? "
            f"AND status={_S_AG} AND data_hora LIKE ? "
            "AND data_hora >= ? "
            "ORDER BY data_hora ASC LIMIT 1",
            (barbeiro_id, barbearia_id, hoje + "%", agora_str)).fetchone()
    if not row:
        return 9999
    try:
        from datetime import datetime as _dt
        dh = row["data_hora"]
        for fmt in (FMT, "%Y-%m-%d %H:%M"):
            try:
                proxima = _dt.strptime(dh, fmt)
                break
            except ValueError:
                continue
        else:
            return 9999
        diff = int((proxima - agora).total_seconds() / 60)
        return max(0, diff)
    except Exception:
        return 9999


def iniciar_trabalho(id: int) -> bool:
    # IMPORTANTE: o agendamento é lido DENTRO do lock exclusivo para evitar TOCTOU.
    # Se fosse lido antes, um reagendar concorrente podia mudar barbeiro_id entre o fetch e o lock.
    barbearia_id_cache = None
    with _write_exclusive() as conn:
        ag_row = conn.execute(
            "SELECT * FROM agendamentos WHERE id=?", (id,)).fetchone()
        if not ag_row:
            return False
        ag = dict(ag_row)
        # Verificação atómica: impede race condition entre dois iniciar simultâneos
        row = conn.execute(
            f"SELECT id FROM agendamentos "
            f"WHERE barbeiro_id=? AND status={_S_EM} LIMIT 1",
            (ag["barbeiro_id"],)).fetchone()
        if row:
            return False   # já tem serviço em curso
        conn.execute(
            f"UPDATE agendamentos SET inicio=?, status={_S_EM} "
            f"WHERE id=? AND status IN {_ST_ATIVOS}",
            (_agora().strftime(FMT), id))
        changed = conn.execute("SELECT changes()").fetchone()[0]
        barbearia_id_cache = ag.get("barbearia_id")
    if not changed:
        return False   # row não existia ou já estava noutro estado
    invalidar_cache_slots(barbearia_id_cache)
    return True


def terminar_trabalho(id: int, valor: int = 0) -> None:
    barbearia_id_cache = None
    with _write_exclusive() as conn:
        ag_row = conn.execute("SELECT barbearia_id FROM agendamentos WHERE id=?", (id,)).fetchone()
        if not ag_row:
            return
        barbearia_id_cache = ag_row["barbearia_id"]
        conn.execute(
            f"UPDATE agendamentos SET fim=?, status={_S_CONC}, valor=? "
            f"WHERE id=? AND barbearia_id=? AND status={_S_EM}",
            (_agora().strftime(FMT), valor or 0, id, barbearia_id_cache))
    if barbearia_id_cache:
        invalidar_cache_slots(barbearia_id_cache)


def _estado_hash(rows: list) -> str:
    """Converte lista de rows {id, status} num hash MD5 compacto.
    O cliente compara o hash para detectar mudanças — evita enviar strings grandes a cada poll."""
    conteudo = "|".join(f"{r['id']}:{r['status']}" for r in rows)
    return hashlib.md5(conteudo.encode()).hexdigest()


def estado_hoje(barbearia_id: int, barbeiro_id: int | None = None) -> str:
    hoje = _agora(barbearia_id).strftime("%Y-%m-%d")
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
    return _estado_hash(rows)


def estado_cliente(telefone: str | None, barbearia_id: int) -> str:
    if not telefone:
        return ""
    tel = normalizar_tel(telefone)   # garantir formato consistente com o que está guardado
    with _read() as conn:
        rows = conn.execute(
            "SELECT id, status FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            "ORDER BY id DESC LIMIT 20",
            (barbearia_id, tel)).fetchall()
    return _estado_hash(rows)


def cancelar_agendamento(id: int, incluir_em_andamento: bool = False) -> bool:
    """Cancela agendamento. Devolve True se efectivamente cancelado, False se já não estava activo."""
    ag = get_agendamento(id)
    with _write() as conn:
        if incluir_em_andamento:
            cur = conn.execute(
                f"UPDATE agendamentos SET status={_S_CANC} "
                f"WHERE id=? AND status IN {_ST_ATIVOS_EM}", (id,))
        else:
            cur = conn.execute(
                f"UPDATE agendamentos SET status={_S_CANC} "
                f"WHERE id=? AND status IN {_ST_ATIVOS}", (id,))
        cancelado = cur.rowcount > 0
    if ag:
        invalidar_cache_slots(ag.get("barbearia_id"))
    return cancelado


def deletar_walkin_orfao(id: int) -> None:
    """Remove um walk-in criado mas nunca iniciado (órfão de race condition)."""
    barbearia_id_cache = None
    with _write() as conn:
        row = conn.execute("SELECT barbearia_id FROM agendamentos WHERE id=?", (id,)).fetchone()
        if row:
            barbearia_id_cache = row["barbearia_id"]
        conn.execute(
            f"DELETE FROM agendamentos WHERE id=? AND status={_S_WK}", (id,))
    if barbearia_id_cache:
        invalidar_cache_slots(barbearia_id_cache)


def reagendar_agendamento(id: int, nova_data_hora: str, novo_barbeiro_id: int | None = None,
                          novo_servico_id: int | None = None) -> None:
    ag = get_agendamento(id)
    with _write() as conn:
        if novo_barbeiro_id and novo_servico_id:
            conn.execute(
                f"UPDATE agendamentos SET data_hora=?, barbeiro_id=?, servico_id=?, "
                f"status={_S_AG}, token_reagendar=NULL WHERE id=?",
                (nova_data_hora, novo_barbeiro_id, novo_servico_id, id))
        elif novo_barbeiro_id:
            conn.execute(
                f"UPDATE agendamentos SET data_hora=?, barbeiro_id=?, "
                f"status={_S_AG}, token_reagendar=NULL WHERE id=?",
                (nova_data_hora, novo_barbeiro_id, id))
        elif novo_servico_id:
            conn.execute(
                f"UPDATE agendamentos SET data_hora=?, servico_id=?, "
                f"status={_S_AG}, token_reagendar=NULL WHERE id=?",
                (nova_data_hora, novo_servico_id, id))
        else:
            conn.execute(
                f"UPDATE agendamentos SET data_hora=?, status={_S_AG}, token_reagendar=NULL WHERE id=?",
                (nova_data_hora, id))
    if ag:
        invalidar_cache_slots(ag.get("barbearia_id"))


# ── Disponibilidade ────────────────────────────────────────
# As funções de disponibilidade (verificar_disponibilidade, horarios_disponiveis)
# foram movidas para db/disponibilidade.py — cálculo read-only de slots/conflitos,
# separado da gestão de estado das marcações que vive aqui.


# ── Visitas do cliente ────────────────────────────────────

def contar_visitas(telefone: str, barbearia_id: int) -> int:
    """Devolve o número total de visitas concluídas de um cliente pelo telemóvel."""
    tel = normalizar_tel(telefone)
    if not tel:
        return 0
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM agendamentos "
            f"WHERE barbearia_id=? AND telefone=? AND status={_S_CONC}",
            (barbearia_id, tel)).fetchone()
    return row[0] if row else 0


def contar_visitas_batch(telefones: list[str], barbearia_id: int) -> dict[str, int]:
    """Devolve {telefone: count} para uma lista de telefones em 1 query (evita N+1)."""
    tels = [normalizar_tel(t) for t in telefones if t]
    tels = [t for t in tels if t]
    if not tels:
        return {}
    ph = ",".join("?" * len(tels))
    with _read() as conn:
        rows = conn.execute(
            f"SELECT telefone, COUNT(*) as c FROM agendamentos "
            f"WHERE barbearia_id=? AND telefone IN ({ph}) AND status={_S_CONC} "
            f"GROUP BY telefone",
            [barbearia_id] + tels).fetchall()
    return {r["telefone"]: r["c"] for r in rows}


# ── Bloqueios de horário ───────────────────────────────────

def criar_bloqueio_hora(barbeiro_id: int, data: str, hora_inicio: str, hora_fim: str, motivo: str = "") -> None:
    """Cria um bloqueio de horário para um barbeiro (ausência de tipo bloqueio).
    Levanta ValueError se hora_inicio >= hora_fim."""
    if hora_inicio >= hora_fim:
        raise ValueError(f"hora_inicio ({hora_inicio}) deve ser anterior a hora_fim ({hora_fim})")
    def _hm(s: str) -> int:
        """Converte 'HH:MM' em minutos desde meia-noite."""
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    ini_min = _hm(hora_inicio)
    fim_min = _hm(hora_fim)

    with _write_exclusive() as conn:
        # Verificar sobreposição: bloqueio existente que se sobrepõe ao novo
        overlap = conn.execute(
            "SELECT id FROM ausencias WHERE barbeiro_id=? AND data_inicio=? AND data_fim=? "
            "AND tipo='bloqueio' AND hora_inicio < ? AND hora_fim > ?",
            (barbeiro_id, data, data, hora_fim, hora_inicio)).fetchone()
        if overlap:
            raise ValueError(f"Já existe um bloqueio que se sobrepõe a {hora_inicio}–{hora_fim}.")
        # Verificar sobreposição com agendamentos existentes
        ags = conn.execute(
            f"SELECT a.id, strftime('%H:%M', a.data_hora) AS hora, "
            f"COALESCE(s.duracao_min, 30) AS dur "
            f"FROM agendamentos a "
            f"LEFT JOIN servicos s ON s.id=a.servico_id "
            f"WHERE a.barbeiro_id=? AND DATE(a.data_hora)=? "
            f"AND a.status NOT IN {_ST_EXCLUIDOS}",
            (barbeiro_id, data)).fetchall()
        for ag in ags:
            ag_ini = _hm(ag["hora"])
            ag_fim = ag_ini + int(ag["dur"])
            if ag_ini < fim_min and ag_fim > ini_min:
                raise ValueError(
                    f"Existe um agendamento às {ag['hora']} que se sobrepõe ao bloqueio {hora_inicio}–{hora_fim}.")
        conn.execute(
            "INSERT INTO ausencias (barbeiro_id,data_inicio,data_fim,tipo,motivo,hora_inicio,hora_fim) "
            "VALUES (?,?,?,?,?,?,?)",
            (barbeiro_id, data, data, "bloqueio", motivo or "", hora_inicio, hora_fim))
        barb = conn.execute("SELECT barbearia_id FROM barbeiros WHERE id=?", (barbeiro_id,)).fetchone()
        bid  = barb["barbearia_id"] if barb else None
    if bid:
        invalidar_cache_slots(bid)


def listar_bloqueios_dia(barbearia_id: int, data: str, barbeiro_id: int | None = None) -> list[dict]:
    """Devolve os bloqueios de horário do dia para a barbearia (ou barbeiro específico)."""
    with _read() as conn:
        if barbeiro_id:
            rows = conn.execute(
                "SELECT a.*, b.nome AS barbeiro_nome FROM ausencias a "
                "JOIN barbeiros b ON a.barbeiro_id=b.id "
                "WHERE b.barbearia_id=? AND a.barbeiro_id=? "
                "AND a.tipo='bloqueio' AND a.data_inicio<=? AND a.data_fim>=? "
                "ORDER BY a.hora_inicio",
                (barbearia_id, barbeiro_id, data, data)).fetchall()
        else:
            rows = conn.execute(
                "SELECT a.*, b.nome AS barbeiro_nome FROM ausencias a "
                "JOIN barbeiros b ON a.barbeiro_id=b.id "
                "WHERE b.barbearia_id=? "
                "AND a.tipo='bloqueio' AND a.data_inicio<=? AND a.data_fim>=? "
                "ORDER BY a.hora_inicio",
                (barbearia_id, data, data)).fetchall()
    return [dict(r) for r in rows]


# ── Marcações do cliente ───────────────────────────────────

def agendamentos_cliente_barbeiro_dia(telefone: str, barbeiro_id: int, data_str: str, barbearia_id: int) -> list[dict]:
    tel = normalizar_tel(telefone)   # garantir formato consistente com o que está guardado
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM agendamentos WHERE barbearia_id=? AND telefone=? "
            f"AND barbeiro_id=? AND data_hora LIKE ? AND status IN {_ST_AG_EM}",
            (barbearia_id, tel, barbeiro_id, f"{data_str}%")).fetchall()
    return [dict(r) for r in rows]


# ── Token de reagendamento pelo cliente ───────────────────

def gerar_token_reagendar(agendamento_id: int) -> str:
    """Gera (ou reutiliza) um token único para reagendamento pelo cliente.
    Usa BEGIN IMMEDIATE para evitar race condition read-check-write."""
    import secrets as _sec
    with _write_exclusive() as conn:
        row = conn.execute(
            "SELECT token_reagendar FROM agendamentos WHERE id=?",
            (agendamento_id,)).fetchone()
        if row and row["token_reagendar"]:
            return row["token_reagendar"]
        token = _sec.token_urlsafe(32)   # 256 bits
        conn.execute(
            "UPDATE agendamentos SET token_reagendar=? WHERE id=?",
            (token, agendamento_id))
    return token


def get_agendamento_por_token(token: str) -> dict | None:
    """Devolve agendamento pelo token de reagendamento (só se ainda agendado e token não expirado).
    O token expira 7 dias após a data original do agendamento."""
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM agendamentos WHERE token_reagendar=? "
            f"AND status IN {_ST_ATIVOS}", (token,)).fetchone()
    if not row:
        return None
    ag = dict(row)
    # TTL: token expira 7 dias após a data do agendamento
    try:
        data_ag = datetime.strptime(ag["data_hora"][:10], "%Y-%m-%d")
        if _agora(ag.get("barbearia_id")) > data_ag + timedelta(days=7):
            return None  # link expirado
    except (ValueError, TypeError):
        pass
    return ag


# ── Avaliações ────────────────────────────────────────────

def get_agendamento_por_token_avaliar(token: str | None) -> dict | None:
    """Devolve agendamento pelo token de avaliação público.
    O token expira 90 dias após a data do agendamento."""
    if not token:
        return None
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM agendamentos WHERE token_avaliar=?",
            (token,)).fetchone()
    if not row:
        return None
    ag = dict(row)
    # Usar Python para o cutoff com o fuso correcto da barbearia (date('now') no SQLite é UTC)
    try:
        cutoff = (_agora(ag.get("barbearia_id")) - timedelta(days=90))
        data_ag = datetime.strptime(ag["data_hora"][:10], "%Y-%m-%d")
        if data_ag < cutoff:
            return None  # link expirado
    except (ValueError, TypeError):
        pass
    return ag


def guardar_avaliacao(agendamento_id: int, barbearia_id: int, nota: int) -> None:
    """Guarda avaliação (1-5) de um agendamento concluído."""
    if nota not in (1, 2, 3, 4, 5):
        raise ValueError("Nota deve ser entre 1 e 5")
    with _write() as conn:
        conn.execute(
            "UPDATE agendamentos SET avaliacao=? "
            f"WHERE id=? AND barbearia_id=? AND status={_S_CONC}",
            (nota, agendamento_id, barbearia_id))


def media_avaliacoes(barbearia_id: int, barbeiro_id: int | None = None) -> dict:
    """Retorna média e contagem de avaliações da barbearia ou de um barbeiro."""
    q = ("SELECT ROUND(AVG(avaliacao),1) AS media, COUNT(avaliacao) AS total "
         "FROM agendamentos WHERE barbearia_id=? AND avaliacao IS NOT NULL")
    p = [barbearia_id]
    if barbeiro_id:
        q += " AND barbeiro_id=?"; p.append(barbeiro_id)
    with _read() as conn:
        row = conn.execute(q, p).fetchone()
    return {"media": row["media"] or 0, "total": row["total"] or 0}


# ── Limpeza de atendimentos presos ────────────────────────

def limpar_em_andamento_presos(barbearia_id: int, horas: int = 8) -> int:
    """Marca como 'concluido' atendimentos em_andamento há mais de `horas` horas
    (ficaram presos após crash do servidor ou fecho inesperado do browser).
    Devolve o número de linhas actualizadas (0 = nada foi feito)."""
    agora_local = _agora(barbearia_id=barbearia_id)
    limite = (agora_local - timedelta(hours=horas)).strftime(FMT)
    with _write() as conn:
        conn.execute(
            f"UPDATE agendamentos SET status={_S_CONC}, fim=? "
            f"WHERE barbearia_id=? AND status={_S_EM} "
            "AND (inicio < ? OR inicio IS NULL)",
            (agora_local.strftime(FMT), barbearia_id, limite))
        return conn.execute("SELECT changes()").fetchone()[0]


# ── Novos agendamentos (para notificações) ────────────────

def novos_agendamentos(barbearia_id: int, desde_id: int = 0, barbeiro_id: int | None = None) -> list[dict]:
    """Devolve agendamentos criados após `desde_id` (para deteção de novos bookings)."""
    q = ("SELECT * FROM agendamentos WHERE barbearia_id=? AND id>? "
         f"AND status IN {_ST_ATIVOS}")
    p = [barbearia_id, desde_id]
    if barbeiro_id:
        q += " AND barbeiro_id=?"; p.append(barbeiro_id)
    q += " ORDER BY id DESC LIMIT 50"
    with _read() as conn:
        rows = conn.execute(q, p).fetchall()
    return [dict(r) for r in rows]


# ── Lembretes ──────────────────────────────────────────────

def proximos_agendamentos(barbearia_id: int, minutos: int = 20, barbeiro_id: int | None = None) -> list[dict]:
    agora  = _agora(barbearia_id=barbearia_id)   # fuso correcto da barbearia
    limite = agora + timedelta(minutes=minutos)
    q      = (f"SELECT * FROM agendamentos WHERE barbearia_id=? AND status={_S_AG} "
               "AND data_hora BETWEEN ? AND ?")
    params = [barbearia_id, agora.strftime(FMT), limite.strftime(FMT)]
    if barbeiro_id:
        q += " AND barbeiro_id=?"; params.append(barbeiro_id)
    with _read() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


# ── Estatísticas ───────────────────────────────────────────


# ── Fila de espera ────────────────────────────────────────────────────────────

def espera_adicionar(barbearia_id: int, cliente_nome: str, telefone: str | None, servico_id: int | None,
                     barbeiro_id: int | None, data_preferida: str) -> bool:
    """Adiciona cliente à fila de espera. Retorna False se já existe entrada igual activa."""
    from db._conn import FMT, _agora, _write_exclusive
    from datetime import timedelta
    agora = _agora(barbearia_id).strftime(FMT)
    expira = (_agora(barbearia_id) + timedelta(days=7)).strftime(FMT)
    with _write_exclusive() as conn:
        existe = conn.execute(
            "SELECT id FROM lista_espera WHERE barbearia_id=? AND telefone=? "
            "AND data_preferida=? AND slot_livre=0 AND expira_em > ?",
            (barbearia_id, telefone, data_preferida, agora)).fetchone()
        if existe:
            return False
        conn.execute(
            "INSERT INTO lista_espera (barbearia_id, cliente_nome, telefone, servico_id, "
            "barbeiro_id, data_preferida, criado_em, expira_em) VALUES (?,?,?,?,?,?,?,?)",
            (barbearia_id, cliente_nome, telefone or "", servico_id, barbeiro_id,
             data_preferida, agora, expira))
    return True


def espera_verificar_cliente(barbearia_id: int, telefone: str) -> list[dict]:
    """Verifica se o cliente tem slot disponível na fila de espera."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM lista_espera WHERE barbearia_id=? AND telefone=? "
            "AND slot_livre=1 AND notificado=0 ORDER BY data_preferida",
            (barbearia_id, telefone)).fetchall()
    return [dict(r) for r in rows]


def espera_marcar_notificado(espera_id: int) -> None:
    with _write() as conn:
        conn.execute("UPDATE lista_espera SET notificado=1 WHERE id=?", (espera_id,))


def espera_notificar_proximo(barbearia_id: int, data_cancelada: str, barbeiro_id_cancelado: int | None) -> dict | None:
    """Quando um agendamento é cancelado, marca o próximo da fila como slot_livre=1.

    Devolve o dict completo da entrada (com telefone, data_preferida, etc.)
    para que o chamador possa enviar push/SMS ao cliente. Devolve None se não
    houver ninguém em espera para esse dia.
    """
    from db._conn import FMT, _agora
    agora_local = _agora(barbearia_id).strftime(FMT)
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM lista_espera WHERE barbearia_id=? AND data_preferida=? "
            "AND slot_livre=0 AND expira_em > ? "
            "AND (barbeiro_id IS NULL OR barbeiro_id=?) "
            "ORDER BY criado_em LIMIT 1",
            (barbearia_id, data_cancelada, agora_local, barbeiro_id_cancelado)).fetchone()
        if row:
            conn.execute("UPDATE lista_espera SET slot_livre=1 WHERE id=?", (row["id"],))
            return dict(row)
    return None


def espera_listar_activa(barbearia_id: int, limit: int = 50, offset: int = 0) -> dict:
    """Lista fila de espera activa (para o chefe ver no painel).

    Args:
        limit:  máximo de registos a devolver (default 50, máx 200)
        offset: posição inicial para paginação
    """
    from db._conn import FMT, _agora
    limit      = min(max(int(limit), 1), 200)
    offset     = max(int(offset), 0)
    agora_local = _agora(barbearia_id).strftime(FMT)
    with _read() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM lista_espera "
            "WHERE barbearia_id=? AND expira_em > ?",
            (barbearia_id, agora_local)).fetchone()[0]
        rows = conn.execute(
            "SELECT e.*, s.nome AS servico_nome, b.nome AS barbeiro_nome "
            "FROM lista_espera e "
            "LEFT JOIN servicos s ON s.id=e.servico_id "
            "LEFT JOIN barbeiros b ON b.id=e.barbeiro_id "
            "WHERE e.barbearia_id=? AND e.expira_em > ? "
            "ORDER BY e.data_preferida, e.criado_em "
            "LIMIT ? OFFSET ?",
            (barbearia_id, agora_local, limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def espera_remover(id: int, barbearia_id: int) -> bool:
    """Remove uma entrada da fila de espera (só a própria barbearia pode apagar)."""
    with _write() as conn:
        cur = conn.execute(
            "DELETE FROM lista_espera WHERE id=? AND barbearia_id=?", (id, barbearia_id))
    return (cur.rowcount or 0) > 0


def espera_limpar_expiradas() -> None:
    """Remove entradas expiradas (chamado pelo thread de limpeza)."""
    with _write() as conn:
        conn.execute("DELETE FROM lista_espera WHERE expira_em <= datetime('now')")


# ── Lembretes WA ──────────────────────────────────────────────────────────────

def marcar_lembrete_wa(id: int, barbearia_id: int) -> bool:
    """Regista o timestamp em que o lembrete WA foi enviado para este agendamento.
    Só actualiza se o agendamento pertencer à barbearia (multi-tenant safe)."""
    agora = _agora().strftime(FMT)
    with _write() as conn:
        cur = conn.execute(
            "UPDATE agendamentos SET lembrete_wa_em=? "
            "WHERE id=? AND barbearia_id=? AND status IN ('agendado','em_andamento')",
            (agora, id, barbearia_id))
    return (cur.rowcount or 0) > 0


# ── Fidelidade — reset manual ─────────────────────────────────────────────────

def fidelidade_reset(barbearia_id: int, telefone: str, obs: str | None = None) -> None:
    """Regista um reset manual do ciclo de fidelidade para um cliente."""
    agora = _agora().strftime(FMT)
    with _write() as conn:
        conn.execute(
            "INSERT INTO fidelidade_resets (barbearia_id, telefone, resetado_em, obs) "
            "VALUES (?, ?, ?, ?)",
            (barbearia_id, telefone, agora, obs or None))


def fidelidade_resets_count(barbearia_id: int, telefone: str) -> int:
    """Devolve o número de resets manuais já feitos para este cliente."""
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM fidelidade_resets WHERE barbearia_id=? AND telefone=?",
            (barbearia_id, telefone)).fetchone()
    return row[0] if row else 0

