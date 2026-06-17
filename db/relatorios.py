# db/relatorios.py — Estatísticas, tendência, duração real

from datetime import datetime, timedelta
from db._conn import _read, _agora, FMT, ST_CONCLUIDO, ST_NAO_COMP

_S_CONC = f"'{ST_CONCLUIDO}'"  # 'concluido'
_S_NC   = f"'{ST_NAO_COMP}'"   # 'nao_compareceu'

from db.barbeiros import get_barbeiro


def estatisticas(barbearia_id: int, barbeiro_id: int | None = None) -> dict:
    from collections import Counter

    hoje   = _agora(barbearia_id)
    d_hoje = hoje.strftime("%Y-%m-%d")
    d_sem  = (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")
    d_mes  = hoje.strftime("%Y-%m-01")

    def resumo(rows):
        return {"clientes": len(rows), "valor": sum(r["valor"] or 0 for r in rows)}

    with _read() as conn:
        def query(desde):
            q = (f"SELECT * FROM agendamentos WHERE barbearia_id=? AND status={_S_CONC} "
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
        # Batch-fetch dos serviços necessários (evita N+1 queries)
        _sids_needed = [sid for sid, _ in contagem_servicos.most_common(5)]
        _servicos_map = {}
        if _sids_needed:
            placeholders = ",".join("?" * len(_sids_needed))
            for row in conn.execute(
                    f"SELECT * FROM servicos WHERE id IN ({placeholders})", _sids_needed):
                _servicos_map[row["id"]] = dict(row)
        top_servicos = []
        for sid, count in contagem_servicos.most_common(5):
            s = _servicos_map.get(sid)
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
            # Uma única query com GROUP BY em vez de N queries (uma por barbeiro)
            _bs_rows = conn.execute(f"""
                SELECT
                    b.id, b.nome,
                    SUM(CASE WHEN a.status={_S_CONC} AND a.data_hora >= ? THEN 1 ELSE 0 END)             AS clientes,
                    SUM(CASE WHEN a.status={_S_CONC} AND a.data_hora >= ? THEN COALESCE(a.valor,0) ELSE 0 END) AS valor,
                    SUM(CASE WHEN a.status={_S_CONC} AND a.data_hora >= ? THEN 1 ELSE 0 END)             AS clientes_sem,
                    SUM(CASE WHEN a.status={_S_CONC} AND a.data_hora >= ? THEN COALESCE(a.valor,0) ELSE 0 END) AS valor_sem,
                    ROUND(AVG(CASE WHEN a.avaliacao IS NOT NULL THEN a.avaliacao END), 1)                   AS avaliacao_media,
                    COUNT(CASE WHEN a.avaliacao IS NOT NULL THEN 1 END)                                     AS avaliacao_total
                FROM barbeiros b
                LEFT JOIN agendamentos a ON a.barbeiro_id = b.id AND a.barbearia_id = ?
                WHERE b.barbearia_id = ? AND b.role IN ('chefe','barbeiro')
                GROUP BY b.id, b.nome
                ORDER BY clientes DESC
            """, (f"{d_mes} 00:00:00", f"{d_mes} 00:00:00",
                  f"{d_sem} 00:00:00", f"{d_sem} 00:00:00",
                  barbearia_id, barbearia_id)).fetchall()
            barbeiros_stats = [{
                "id":               r["id"],
                "nome":             r["nome"],
                "clientes":         r["clientes"] or 0,
                "valor":            r["valor"] or 0,
                "clientes_sem":     r["clientes_sem"] or 0,
                "valor_sem":        r["valor_sem"] or 0,
                "avaliacao_media":  r["avaliacao_media"] or 0,
                "avaliacao_total":  r["avaliacao_total"] or 0,
            } for r in _bs_rows]

        horas    = [r["data_hora"][11:13] for r in todos_rows]
        hora_top = Counter(horas).most_common(1)[0] if horas else None

    return {
        "hoje": resumo(hoje_rows), "semana": resumo(sem_rows), "mes": resumo(mes_rows),
        "top_servicos": top_servicos, "barbeiros_stats": barbeiros_stats, "hora_top": hora_top,
    }


def estatisticas_detalhadas_barbeiro(barbeiro_id: int, barbearia_id: int) -> dict:
    from collections import Counter

    hoje   = _agora(barbearia_id)
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
                f"SELECT * FROM agendamentos WHERE barbearia_id=? AND status={_S_CONC} "
                "AND barbeiro_id=? AND data_hora >= ?",
                (barbearia_id, barbeiro_id, f"{desde} 00:00:00")).fetchall()

        hoje_rows  = query(d_hoje)
        sem_rows   = query(d_sem)
        mes_rows   = query(d_mes)
        todos_rows = query("2000-01-01")

        contagem = Counter(r["servico_id"] for r in todos_rows)
        # Batch-fetch dos serviços (evita N+1 queries)
        _sids2 = [sid for sid, _ in contagem.most_common(10)]
        _smap2 = {}
        if _sids2:
            ph2 = ",".join("?" * len(_sids2))
            for row in conn.execute(
                    f"SELECT * FROM servicos WHERE id IN ({ph2})", _sids2):
                _smap2[row["id"]] = dict(row)
        top_servicos, gargalhos = [], []
        for sid, count in contagem.most_common(10):
            s = _smap2.get(sid)
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
                except ValueError:
                    pass  # formato de data inválido — ignorar registo
        media_atraso = round(sum(atrasos) / len(atrasos), 1) if atrasos else 0

        nc = conn.execute(
            f"SELECT COUNT(*) FROM agendamentos WHERE barbearia_id=? "
            f"AND status={_S_NC} AND barbeiro_id=?",
            (barbearia_id, barbeiro_id)).fetchone()[0]

        recentes = conn.execute(
            f"SELECT * FROM agendamentos WHERE barbearia_id=? AND status={_S_CONC} "
            "AND barbeiro_id=? ORDER BY data_hora DESC LIMIT 15",
            (barbearia_id, barbeiro_id)).fetchall()

        # Batch-fetch dos serviços dos recentes (evita N+1 — 1 query em vez de 15)
        _rec_sids = list({r["servico_id"] for r in recentes if r["servico_id"]})
        _rec_smap = {}
        if _rec_sids:
            _ph = ",".join("?" * len(_rec_sids))
            for _sr in conn.execute(f"SELECT * FROM servicos WHERE id IN ({_ph})", _rec_sids):
                _rec_smap[_sr["id"]] = dict(_sr)

        recentes_list = []
        for r in recentes:
            s = _rec_smap.get(r["servico_id"])
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


# ── Tendências ────────────────────────────────────────────

def tendencia_semanal(barbearia_id: int, barbeiro_id: int | None = None, semanas: int = 10) -> list[dict]:
    """Retorna resumo dos últimos `semanas` semanas para gráfico de tendência.
    Devolve lista de {label, clientes, valor} ordenada por semana crescente."""
    # Usar _agora() com o fuso da barbearia em vez de date('now') do SQLite (que é UTC)
    cutoff = (_agora(barbearia_id=barbearia_id) - timedelta(weeks=semanas)).strftime("%Y-%m-%d")
    with _read() as conn:
        base = (
            "SELECT strftime('%Y-%W', data_hora) AS semana_key, "
            "       COUNT(*) AS clientes, "
            "       SUM(COALESCE(valor, 0)) AS valor "
            "FROM agendamentos "
            f"WHERE barbearia_id=? AND status={_S_CONC} "
            "  AND data_hora >= ? "
        )
        params = [barbearia_id, cutoff]
        if barbeiro_id:
            base += " AND barbeiro_id=? "; params.append(barbeiro_id)
        base += " GROUP BY semana_key ORDER BY semana_key"
        rows = conn.execute(base, params).fetchall()

    resultado = []
    for r in rows:
        try:
            # semana_key: "2026-18" → converter para "S18"
            # %W: dias antes da 1ª segunda-feira do ano caem na semana 0 — ignorar
            parts = r["semana_key"].split("-")
            if len(parts) == 2 and int(parts[1]) == 0:
                continue  # semana 0 inválida (dias de ano novo antes da 1ª 2ª-feira)
            label = f"S{int(parts[1])}" if len(parts) == 2 else r["semana_key"]
        except (IndexError, ValueError):
            label = r["semana_key"]
        resultado.append({
            "label":    label,
            "clientes": r["clientes"],
            "valor":    round(r["valor"] or 0, 2),
        })
    return resultado


# ── Helpers ────────────────────────────────────────────────

def duracao_real_minutos(inicio_str: str | None, fim_str: str | None) -> int | None:
    if not inicio_str or not fim_str:
        return None
    try:
        return int((datetime.strptime(fim_str, FMT) - datetime.strptime(inicio_str, FMT)).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def taxa_cancelamentos(barbearia_id: int, mes: str) -> dict:
    """Taxa de cancelamentos + não comparecimento num mês (YYYY-MM)."""
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status IN ('cancelado','nao_compareceu') THEN 1 ELSE 0 END) AS perdidos "
            "FROM agendamentos WHERE barbearia_id=? AND strftime('%Y-%m', data_hora)=? "
            "AND status != 'agendado'",
            (barbearia_id, mes)).fetchone()
    total    = (row["total"]    if row else 0) or 0
    perdidos = (row["perdidos"] if row else 0) or 0
    return {
        "total":    total,
        "perdidos": perdidos,
        "pct":      round(perdidos / total * 100) if total else 0,
    }


def top_clientes(barbearia_id: int, limite: int = 10) -> list[dict]:
    """Top N clientes por número de visitas concluídas."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT cliente, telefone, COUNT(*) AS visitas, MAX(data_hora) AS ultima_visita "
            "FROM agendamentos "
            "WHERE barbearia_id=? AND status IN ('concluido','walkin') "
            "GROUP BY COALESCE(telefone, cliente) "
            "ORDER BY visitas DESC LIMIT ?",
            (barbearia_id, limite)).fetchall()
    return [dict(r) for r in rows]


def visitas_cliente(barbearia_id: int, telefone: str) -> int:
    """Número de visitas concluídas de um cliente (por telefone)."""
    if not telefone:
        return 0
    with _read() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM agendamentos "
            "WHERE barbearia_id=? AND telefone=? AND status IN ('concluido','walkin')",
            (barbearia_id, telefone)).fetchone()
    return row[0] if row else 0


def analytics_clientes(barbearia_id: int, limite: int = 100,
                        periodo_dias: int | None = None) -> list[dict]:
    """Analytics por cliente: LTV, frequência média, serviço favorito.

    periodo_dias — se definido, limita a janela de análise a este nº de dias.
    """
    desde_str = ""
    params_base = [barbearia_id]
    if periodo_dias and periodo_dias > 0:
        desde = (_agora(barbearia_id) - timedelta(days=periodo_dias)).strftime("%Y-%m-%d")
        desde_str = f" AND a.data_hora >= '{desde} 00:00:00'"

    with _read() as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(a.telefone, a.cliente) AS chave,
                a.cliente,
                a.telefone,
                COUNT(*) AS visitas,
                SUM(COALESCE(a.valor,0)) AS ltv,
                MAX(a.data_hora) AS ultima_visita,
                MIN(a.data_hora) AS primeira_visita,
                COALESCE((
                    SELECT COUNT(*) FROM fidelidade_resets fr
                    WHERE fr.barbearia_id=a.barbearia_id
                      AND fr.telefone=a.telefone
                ), 0) AS resets_feitos
            FROM agendamentos a
            WHERE a.barbearia_id=? AND a.status IN ('concluido','walkin'){desde_str}
            GROUP BY COALESCE(a.telefone, a.cliente)
            ORDER BY visitas DESC, ltv DESC
            LIMIT ?
            """,
            (barbearia_id, limite)).fetchall()

        # Serviço favorito por cliente (subquery separada para simplicidade)
        servico_fav: dict = {}
        fav_rows = conn.execute(
            f"""
            SELECT COALESCE(a.telefone, a.cliente) AS chave, s.nome AS servico_nome, COUNT(*) AS n
            FROM agendamentos a
            LEFT JOIN servicos s ON s.id=a.servico_id
            WHERE a.barbearia_id=? AND a.status IN ('concluido','walkin')
              AND s.nome IS NOT NULL{desde_str}
            GROUP BY chave, a.servico_id
            ORDER BY chave, n DESC
            """,
            (barbearia_id,)).fetchall()
        for r in fav_rows:
            if r["chave"] not in servico_fav:
                servico_fav[r["chave"]] = r["servico_nome"]

    result = []
    for r in rows:
        d = dict(r)
        chave = d["chave"]
        d["servico_favorito"] = servico_fav.get(chave)
        # Frequência média em dias (entre primeira e última visita, dividido por visitas-1)
        if d["visitas"] > 1 and d["primeira_visita"] and d["ultima_visita"]:
            try:
                _fmt = "%Y-%m-%d %H:%M:%S"
                dias = (datetime.strptime(d["ultima_visita"], _fmt) -
                        datetime.strptime(d["primeira_visita"], _fmt)).days
                d["freq_dias"] = round(dias / (d["visitas"] - 1), 1) if dias > 0 else None
            except (ValueError, TypeError):
                d["freq_dias"] = None
        else:
            d["freq_dias"] = None
        result.append(d)
    return result

