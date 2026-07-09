# db/disponibilidade.py — Cálculo de slots disponíveis e verificação de conflitos
#
# Extraído de db/agendamentos.py: separa o cálculo de disponibilidade
# (slots livres, conflitos, encaixes, ausências, pausas) da gestão de estado
# das marcações. Estas funções são read-only sobre a BD (não mudam estado).

from datetime import datetime, timedelta

from db._conn import (
    _read, _agora, FMT,
    ST_EM_ANDAMENTO, ST_CONCLUIDO, ST_CANCELADO, ST_NAO_COMP,
    _slots_cache_get, _slots_cache_set,
    get_config,
)
from db.barbearia import get_horario_dia, dia_esta_fechado

# Fragmento SQL: status que não bloqueiam um horário (já terminados/cancelados).
_ST_EXCLUIDOS = f"('{ST_CANCELADO}','{ST_CONCLUIDO}','{ST_NAO_COMP}')"


def verificar_disponibilidade(barbeiro_id: int | None, data_hora_str: str | None, duracao_min: int,
                              barbearia_id: int, excluir_id: int | None = None) -> tuple[bool, str | None]:
    if not barbeiro_id or not data_hora_str:
        return True, None
    fmt_in = FMT if len(data_hora_str) == 19 else "%Y-%m-%d %H:%M"
    try:
        inicio_novo = datetime.strptime(data_hora_str, fmt_in)
    except (ValueError, TypeError):
        return True, None
    buffer   = int(get_config("buffer_minutos", barbearia_id, 10))
    fim_novo = inicio_novo + timedelta(minutes=duracao_min + buffer)

    # Filtrar apenas o dia relevante: conflito só é possível no mesmo dia
    # (marcações nunca ultrapassam meia-noite em prática de barbearia).
    # Sem este filtro, a query cresce indefinidamente com o histórico — O(n) por check.
    data_str = data_hora_str[:10]
    # Usar comparação de string em vez de date() para evitar drift UTC
    # (SQLite date() converte para UTC — barbearias em UTC-1 perdem a última hora do dia)
    data_fim = (datetime.strptime(data_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    q = ("SELECT a.*, s.duracao_min AS dur FROM agendamentos a "
         "LEFT JOIN servicos s ON a.servico_id = s.id "
         "WHERE a.barbeiro_id=? AND a.barbearia_id=? "
         "AND a.data_hora >= ? AND a.data_hora < ? "
         f"AND a.status NOT IN {_ST_EXCLUIDOS}")
    params = [barbeiro_id, barbearia_id, data_str, data_fim]
    if excluir_id:
        q += " AND a.id != ?"; params.append(excluir_id)
    with _read() as conn:
        rows = conn.execute(q, params).fetchall()

    for row in rows:
        dur = row["dur"] if row["dur"] else 30
        ref = row["inicio"] if row["status"] == ST_EM_ANDAMENTO and row["inicio"] else row["data_hora"]
        try:
            inicio_ex = datetime.strptime(ref, FMT)
        except (ValueError, TypeError):
            try:
                inicio_ex = datetime.strptime(ref, "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                continue
        fim_ex = inicio_ex + timedelta(minutes=dur + buffer)
        if inicio_novo < fim_ex and fim_novo > inicio_ex:
            return False, row["data_hora"][11:16]
    return True, None


def horarios_disponiveis(barbeiro_id: int | None, data_str: str, duracao_min: int, barbearia_id: int) -> list[dict]:
    # ── Cache: 15 s para hoje, 60 s para datas futuras ──────────────────────
    _hoje = _agora(barbearia_id=barbearia_id).strftime("%Y-%m-%d")
    _ttl  = 15 if data_str == _hoje else 60
    _key  = f"{barbearia_id}:{barbeiro_id}:{data_str}:{duracao_min}"
    _cached = _slots_cache_get(_key)
    if _cached is not None:
        return _cached

    resultado = _horarios_disponiveis_impl(barbeiro_id, data_str, duracao_min, barbearia_id)
    _slots_cache_set(_key, resultado, _ttl)
    return resultado


def _horarios_disponiveis_impl(barbeiro_id: int | None, data_str: str, duracao_min: int, barbearia_id: int) -> list[dict]:
    try:
        weekday = datetime.strptime(data_str, "%Y-%m-%d").weekday()
    except (ValueError, TypeError):
        return []
    horario = get_horario_dia(weekday, barbearia_id)
    if horario["fechado"] or dia_esta_fechado(data_str, barbearia_id):
        return []

    buffer  = int(get_config("buffer_minutos", barbearia_id, 10))
    max_dia = int(get_config("max_por_dia",    barbearia_id, 20))

    # ── UMA única ligação à BD para todas as queries ─────────────────────────
    with _read() as conn:
        q = (f"SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=? AND data_hora LIKE ? "
             f"AND status NOT IN {_ST_EXCLUIDOS}")
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
                f"AND a.status NOT IN {_ST_EXCLUIDOS}",
                (barbearia_id, barbeiro_id, f"{data_str}%")).fetchall()

        # Carregar ausências do barbeiro para este dia — UMA query em vez de 1 por slot
        ausencias_dia = []
        pausa_almoco_inicio = None
        pausa_almoco_fim    = None
        if barbeiro_id:
            aus_rows = conn.execute(
                "SELECT * FROM ausencias WHERE barbeiro_id=? "
                "AND data_inicio<=? AND data_fim>=?",
                (barbeiro_id, data_str, data_str)).fetchall()
            ausencias_dia = [dict(r) for r in aus_rows]

            barb_row = conn.execute(
                "SELECT pausa_almoco_inicio, pausa_almoco_fim FROM barbeiros WHERE id=?",
                (barbeiro_id,)).fetchone()
            if barb_row:
                pausa_almoco_inicio = barb_row["pausa_almoco_inicio"]
                pausa_almoco_fim    = barb_row["pausa_almoco_fim"]

    if total_dia >= max_dia:
        return []

    # ── Pré-processar agendamentos em intervalos [inicio, fim] ────────────────
    # Evita chamar verificar_disponibilidade() (1 query BD) por cada slot
    def _parse_dt(ref):
        try:
            return datetime.strptime(ref, FMT)
        except (ValueError, TypeError):
            try:
                return datetime.strptime(ref, "%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                return None

    intervalos = []   # lista de (inicio_dt, fim_dt, row_dict)
    for r in appt_rows:
        dur_r = r["dur"] if r["dur"] else 30
        ref_r = r["inicio"] if r["status"] == ST_EM_ANDAMENTO and r["inicio"] else r["data_hora"]
        ini_r = _parse_dt(ref_r)
        if ini_r is None:
            continue
        fim_r = ini_r + timedelta(minutes=dur_r + buffer)
        intervalos.append((ini_r, fim_r, dict(r)))

    def _slot_livre(slot_dt):
        """Verificação de conflito em memória — sem query à BD."""
        fim_slot = slot_dt + timedelta(minutes=duracao_min + buffer)
        for ini_r, fim_r, _ in intervalos:
            if slot_dt < fim_r and fim_slot > ini_r:
                return False
        return True

    # ── Verificação de ausências em memória ───────────────────────────────────
    def _hm(t):
        try:
            p = t.split(":")
            return int(p[0]) * 60 + int(p[1])
        except (ValueError, IndexError, AttributeError):
            return 0

    def _slot_em_ausencia(hora_str):
        """Verificação de ausência em memória — sem query à BD.
        Considera a DURAÇÃO do serviço: um serviço que começa antes da ausência
        mas a invade também é rejeitado (não basta olhar só a hora de início)."""
        h     = _hm(hora_str)
        h_fim = h + duracao_min
        for a in ausencias_dia:
            if not a.get("hora_inicio") and not a.get("hora_fim"):
                return True   # ausência de dia inteiro
            if a.get("hora_inicio") and a.get("hora_fim"):
                ini = _hm(a["hora_inicio"])
                fim = _hm(a["hora_fim"])
                if ini < fim:
                    # intervalo normal — overlap se [h, h+dur) intersecta [ini, fim)
                    if h < fim and h_fim > ini:
                        return True
                else:
                    # intervalo que atravessa a meia-noite — check por início
                    if h >= ini or h < fim:
                        return True
        return False

    abertura = datetime.strptime(f"{data_str} {horario['hora_abertura']}:00", FMT)
    fecho    = datetime.strptime(f"{data_str} {horario['hora_fecho']}:00",    FMT)
    agora    = _agora(barbearia_id=barbearia_id)

    # ── Pausa de almoço permanente ───────────────────────────────────────────
    _pausa_ini_min = None
    _pausa_fim_min = None
    if pausa_almoco_inicio and pausa_almoco_fim:
        try:
            pi = pausa_almoco_inicio.split(":")
            pf = pausa_almoco_fim.split(":")
            _pausa_ini_min = int(pi[0]) * 60 + int(pi[1])
            _pausa_fim_min = int(pf[0]) * 60 + int(pf[1])
        except (ValueError, IndexError, AttributeError):
            pass

    def _slot_em_pausa(hora_str):
        if _pausa_ini_min is None or _pausa_fim_min is None:
            return False
        try:
            p = hora_str.split(":")
            h = int(p[0]) * 60 + int(p[1])
        except (ValueError, IndexError):
            return False
        # Considera a DURAÇÃO: o serviço [h, h+dur) não pode invadir a pausa [ini, fim)
        return h < _pausa_fim_min and (h + duracao_min) > _pausa_ini_min

    # Gerar todos os slots de 10 em 10 minutos
    # Só incluir slots onde o serviço termina dentro do horário de fecho
    candidatos = {}
    slot = abertura
    while slot < fecho:
        if slot + timedelta(minutes=duracao_min) <= fecho:
            candidatos[slot.strftime("%H:%M")] = "normal"
        slot += timedelta(minutes=10)

    # Encaixes logo após cada agendamento
    # Só incluir se o serviço couber dentro do horário de fecho
    for ini_r, fim_r, r in intervalos:
        dur_r = r.get("dur") or 30
        apos     = ini_r + timedelta(minutes=dur_r + buffer)
        apos_str = apos.strftime("%H:%M")
        if (apos >= abertura and apos < fecho
                and apos + timedelta(minutes=duracao_min) <= fecho
                and apos_str not in candidatos):
            candidatos[apos_str] = "encaixe"

    # Espera estimada por slot
    _appts_sorted = [(ini_r, r.get("dur") or 30) for ini_r, _, r in intervalos]

    resultado = []
    for hora_str in sorted(candidatos):
        tipo = candidatos[hora_str]
        try:
            slot_dt = datetime.strptime(f"{data_str} {hora_str}:00", FMT)
        except ValueError:
            continue
        if data_str == agora.strftime("%Y-%m-%d") and slot_dt < agora - timedelta(minutes=5):
            continue
        if barbeiro_id and _slot_em_ausencia(hora_str):
            continue
        if _slot_em_pausa(hora_str):
            continue
        livre = _slot_livre(slot_dt)
        espera = sum(dur + buffer for ini, dur in _appts_sorted if agora <= ini < slot_dt)
        resultado.append({"hora": hora_str, "tipo": tipo if livre else "ocupado", "espera_min": espera})

    return resultado
