# db/barbearia.py — Barbearias, configurações, planos, horários, ausências

import sqlite3
from datetime import datetime, timedelta, date
from db._conn import (
    _read, _write, _agora, FMT, invalidar_cache_slots, slug_unico,
    normalizar_dominio,
    _HORARIO_PADRAO,
    get_config, set_config,
    _tz_cache, _tz_cache_lock,
)


def listar_barbearias(apenas_ativas: bool = False) -> list[dict]:
    with _read() as conn:
        q = "SELECT * FROM barbearias"
        if apenas_ativas:
            q += " WHERE ativa=1"
        rows = conn.execute(q + " ORDER BY nome").fetchall()
    return [dict(r) for r in rows]


def get_barbearia(id: int) -> dict | None:
    with _read() as conn:
        row = conn.execute("SELECT * FROM barbearias WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def get_barbearia_por_slug(slug: str) -> dict | None:
    with _read() as conn:
        row = conn.execute("SELECT * FROM barbearias WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def get_barbearia_por_dominio(dominio: str | None) -> dict | None:
    """Resolve um estabelecimento pelo seu domínio próprio.

    Só devolve resultado se o domínio estiver VERIFICADO (dominio_verificado=1) —
    segurança: um domínio por confirmar nunca encaminha tráfego.
    """
    dom = normalizar_dominio(dominio)
    if not dom:
        return None
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM barbearias WHERE dominio=? AND dominio_verificado=1", (dom,)
        ).fetchone()
    return dict(row) if row else None


def set_dominio(barbearia_id: int, dominio: str | None) -> str | None:
    """Define (ou limpa, com None) o domínio próprio de um estabelecimento.

    Ao alterar o domínio a verificação é sempre reposta a 0 — tem de ser
    re-confirmado pelo root. Devolve o domínio normalizado guardado (ou None).
    Levanta ValueError se o domínio já pertencer a outro estabelecimento.
    """
    dom = normalizar_dominio(dominio)
    with _write() as conn:
        if dom:
            existe = conn.execute(
                "SELECT id FROM barbearias WHERE dominio=? AND id!=?",
                (dom, barbearia_id)).fetchone()
            if existe:
                raise ValueError(
                    f"O domínio '{dom}' já está associado a outro estabelecimento.")
        conn.execute(
            "UPDATE barbearias SET dominio=?, dominio_verificado=0 WHERE id=?",
            (dom, barbearia_id))
    return dom


def verificar_dominio(barbearia_id: int, verificado: bool = True) -> None:
    """Marca (ou desmarca) o domínio de um estabelecimento como verificado.

    Acção do root, após confirmar que o DNS aponta correctamente para o serviço.
    """
    with _write() as conn:
        conn.execute(
            "UPDATE barbearias SET dominio_verificado=? WHERE id=?",
            (1 if verificado else 0, barbearia_id))


_TIPOS_VALIDOS = {'barbearia', 'salao_estetica', 'spa', 'clinica', 'outro'}

def criar_barbearia(nome: str, tipo: str = 'barbearia', vocab_custom_json: str | None = None) -> int:
    tipo = tipo if tipo in _TIPOS_VALIDOS else 'barbearia'
    if tipo != 'outro':
        vocab_custom_json = None  # só faz sentido para 'outro'
    with _write() as conn:
        cur = conn.execute(
            "INSERT INTO barbearias (nome, tipo, vocab_custom) VALUES (?, ?, ?)",
            (nome, tipo, vocab_custom_json)
        )
        bid = cur.lastrowid
        for dia, ab, fecho, fechado in _HORARIO_PADRAO:
            conn.execute(
                "INSERT INTO horario_funcionamento (barbearia_id,dia_semana,hora_abertura,hora_fecho,fechado) VALUES (?,?,?,?,?)",
                (bid, dia, ab, fecho, fechado))
        for chave, valor in [("buffer_minutos", "10"), ("max_por_dia", "20")]:
            conn.execute(
                "INSERT INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
                (bid, chave, valor))
        slug = slug_unico(nome)
        conn.execute("UPDATE barbearias SET slug=? WHERE id=?", (slug, bid))
    return bid


def set_tipo_barbearia(barbearia_id: int, tipo: str) -> None:
    """Actualiza o tipo de negócio de um estabelecimento."""
    tipo = tipo if tipo in _TIPOS_VALIDOS else 'barbearia'
    with _write() as conn:
        conn.execute("UPDATE barbearias SET tipo=? WHERE id=?", (tipo, barbearia_id))


def set_vocab_custom(barbearia_id: int, vocab_custom_json: str | None) -> None:
    """Guarda (ou apaga) o vocabulário personalizado de um estabelecimento 'outro'."""
    with _write() as conn:
        conn.execute(
            "UPDATE barbearias SET vocab_custom=? WHERE id=?",
            (vocab_custom_json, barbearia_id)
        )


# ── Plano / Subscrição ─────────────────────────────────────

# Planos disponíveis: código → (nome, dias)
PLANOS = {
    "1m":  ("1 Mês",    30),
    "3m":  ("3 Meses",  90),
    "6m":  ("6 Meses", 180),
    "1y":  ("1 Ano",   365),
}
# Plano de experiência: gratuito, duração fixa, não entra no editor de preços
PLANO_EXP = ("exp", "Experiência", 15)   # (codigo, nome, dias)

_PLANO_BID = 0   # barbearia_id=0 → config global do root


def get_planos_precos() -> dict[str, int]:
    """Devolve dict {codigo: preco_inteiro} para os 4 planos."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT chave, valor FROM configuracoes WHERE barbearia_id=? AND chave LIKE 'plano_preco_%'",
            (_PLANO_BID,)).fetchall()
    precos = {c: int(v) for c, v in ((r["chave"].replace("plano_preco_", ""), r["valor"]) for r in rows)
              if v and v.isdigit()}
    # Defaults 0 para planos sem preço definido
    return {cod: precos.get(cod, 0) for cod in PLANOS}


def set_plano_preco(codigo: str, preco: int) -> bool:
    """Define o preço de um plano (inteiro). Retorna False se código inválido."""
    if codigo not in PLANOS:
        return False
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
            (_PLANO_BID, f"plano_preco_{codigo}", str(max(0, int(preco)))))
    return True


def get_planos_precos_barbearia(barbearia_id: int) -> tuple[dict[str, dict], str]:
    """Preços específicos de uma barbearia. Devolve dict {codigo: {preco, moeda}}.
    Se a barbearia não tiver preço definido para um plano, usa o global."""
    globais = get_planos_precos()
    with _read() as conn:
        rows = conn.execute(
            "SELECT chave, valor FROM configuracoes "
            "WHERE barbearia_id=? AND chave LIKE 'bplano_%'",
            (barbearia_id,)).fetchall()
        moeda_row = conn.execute(
            "SELECT valor FROM configuracoes WHERE barbearia_id=? AND chave='bplano_moeda'",
            (barbearia_id,)).fetchone()
    moeda_padrao = moeda_row["valor"] if moeda_row else "ECV"
    precos_b = {}
    for r in rows:
        chave = r["chave"]
        if chave.startswith("bplano_preco_"):
            cod = chave.replace("bplano_preco_", "")
            if cod in PLANOS and r["valor"] and r["valor"].isdigit():
                precos_b[cod] = int(r["valor"])
    # Construir resultado: preço específico ou global, moeda desta barbearia
    resultado = {}
    for cod in PLANOS:
        preco = precos_b.get(cod, globais.get(cod, 0))
        resultado[cod] = {"preco": preco, "moeda": moeda_padrao}
    return resultado, moeda_padrao


def set_plano_preco_barbearia(barbearia_id: int, codigo: str, preco: int, moeda: str = "ECV") -> bool:
    """Define preço e moeda de um plano para uma barbearia específica."""
    if codigo not in PLANOS:
        return False
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
            (barbearia_id, f"bplano_preco_{codigo}", str(max(0, int(preco)))))
        conn.execute(
            "INSERT OR REPLACE INTO configuracoes (barbearia_id,chave,valor) VALUES (?,?,?)",
            (barbearia_id, "bplano_moeda", moeda))
    return True


def _plano_info(codigo: str) -> tuple[str, int] | None:
    """Devolve (nome, dias) para qualquer código válido (inclui plano exp)."""
    if codigo in PLANOS:
        return PLANOS[codigo]
    if codigo == PLANO_EXP[0]:
        return (PLANO_EXP[1], PLANO_EXP[2])
    return None


def registar_pagamento(barbearia_id: int, codigo_plano: str = "1m") -> dict | bool:
    """Regista pagamento pelo código do plano ('1m','3m','6m','1y','exp').
    Reactiva a barbearia se estava inactiva.
    Devolve dict com {ok, nome_plano, dias, expira_em} ou False se inválido.

    Plano 'exp' (Experiência) é sem prazo: plano_expira_em=NULL em barbearias,
    sentinel '9999-12-31' em pagamentos.expira_em (para satisfazer registo histórico).
    """
    from datetime import date, timedelta
    info = _plano_info(codigo_plano)
    if not info:
        return False
    nome_plano, dias = info
    is_exp = (codigo_plano == PLANO_EXP[0])
    # Buscar preço ANTES da transação de escrita (evita abrir _read dentro de _write)
    # Usa preço específico da barbearia se existir; senão o global
    if is_exp:
        preco, moeda = 0, "ECV"
    else:
        precos_b, moeda = get_planos_precos_barbearia(barbearia_id)
        entry = precos_b.get(codigo_plano, {})
        preco = entry.get("preco", 0) if isinstance(entry, dict) else 0
    with _write() as conn:
        row = conn.execute(
            "SELECT plano_expira_em, ativa FROM barbearias WHERE id=?",
            (barbearia_id,)).fetchone()
        if not row:
            return False
        hoje = date.today().isoformat()
        expira_atual = row["plano_expira_em"]
        # NULL sozinho não basta para bloquear — pode ser a barbearia recém-criada
        # sem nenhum plano atribuído. Só bloqueia se for realmente um trial activo.
        codigo_atual = _codigo_plano_atual(conn, barbearia_id)
        is_current_trial = (codigo_atual == PLANO_EXP[0])
        if row["ativa"] and (
            (expira_atual and expira_atual >= hoje)
            or (expira_atual is None and is_current_trial)
        ):
            return {"erro": "plano_ativo", "expira_em": expira_atual}
        if is_exp:
            # Plano de experiência — sem prazo; root cancela quando quiser
            nova_expiracao_db = None          # barbearias.plano_expira_em
            nova_expiracao_pg = "9999-12-31"  # pagamentos.expira_em (sentinel)
        else:
            nova_expiracao_db = (date.today() + timedelta(days=dias)).isoformat()
            nova_expiracao_pg = nova_expiracao_db
        conn.execute(
            "UPDATE barbearias SET plano_expira_em=?, ativa=1 WHERE id=?",
            (nova_expiracao_db, barbearia_id))
        conn.execute(
            "INSERT INTO pagamentos (barbearia_id, codigo_plano, nome_plano, dias, preco, moeda, expira_em, registado_em) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (barbearia_id, codigo_plano, nome_plano, dias, preco,
             moeda if not is_exp else "ECV",
             nova_expiracao_pg, datetime.now().strftime(FMT)))
    return {"ok": True, "nome_plano": nome_plano, "dias": dias, "expira_em": nova_expiracao_pg}


def _codigo_plano_atual(conn, barbearia_id: int) -> str | None:
    """Devolve o codigo_plano do pagamento mais recente, ou None."""
    r = conn.execute(
        "SELECT codigo_plano FROM pagamentos WHERE barbearia_id=? "
        "ORDER BY registado_em DESC LIMIT 1", (barbearia_id,)).fetchone()
    return r["codigo_plano"] if r else None


def verificar_plano(barbearia_id: int) -> dict | None:
    """Retorna dict com estado do plano: ativo, dias_restantes, expira_em, codigo_plano.
    None se barbearia não existe. plano_expira_em=NULL → sem limite."""
    from datetime import date
    with _read() as conn:
        row = conn.execute(
            "SELECT ativa, plano_expira_em FROM barbearias WHERE id=?",
            (barbearia_id,)).fetchone()
        if not row:
            return None
        codigo = _codigo_plano_atual(conn, barbearia_id)
    expira = row["plano_expira_em"]
    if expira is None:
        is_trial = (codigo == PLANO_EXP[0])
        return {"ativo": bool(row["ativa"]), "dias_restantes": None,
                "expira_em": None, "sem_limite": True, "trial": is_trial,
                "codigo_plano": codigo}
    hoje = date.today().isoformat()
    dias_rest = (date.fromisoformat(expira) - date.fromisoformat(hoje)).days
    return {
        "ativo":          bool(row["ativa"]) and dias_rest >= 0,
        "dias_restantes": dias_rest,
        "expira_em":      expira,
        "sem_limite":     False,
        "trial":          (codigo == PLANO_EXP[0]),
        "codigo_plano":   codigo,
    }


def listar_pagamentos(barbearia_id: int) -> list[dict]:
    """Histórico de pagamentos de uma barbearia, do mais recente para o mais antigo."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM pagamentos WHERE barbearia_id=? ORDER BY registado_em DESC",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def verificar_todos_planos() -> dict[int, dict]:
    """Devolve dict {barbearia_id: plano_info} para TODAS as barbearias.
    Uma única query em vez de N queries — evita busy_timeout stack no dashboard root.
    Inclui codigo_plano do pagamento mais recente para distinguir trial de pago."""
    from datetime import date
    hoje = date.today().isoformat()
    with _read() as conn:
        rows = conn.execute(
            "SELECT id, ativa, plano_expira_em FROM barbearias").fetchall()
        # Buscar codigo_plano mais recente por barbearia numa só query
        pg_rows = conn.execute(
            "SELECT barbearia_id, codigo_plano FROM pagamentos "
            "WHERE id IN (SELECT MAX(id) FROM pagamentos GROUP BY barbearia_id)"
        ).fetchall()
    codigos = {r["barbearia_id"]: r["codigo_plano"] for r in pg_rows}
    resultado = {}
    for row in rows:
        expira = row["plano_expira_em"]
        cod = codigos.get(row["id"])
        if expira is None:
            resultado[row["id"]] = {
                "ativo": bool(row["ativa"]), "dias_restantes": None,
                "expira_em": None, "sem_limite": True,
                "trial": (cod == PLANO_EXP[0]), "codigo_plano": cod}
        else:
            dias_rest = (date.fromisoformat(expira) - date.fromisoformat(hoje)).days
            resultado[row["id"]] = {
                "ativo":          bool(row["ativa"]) and dias_rest >= 0,
                "dias_restantes": dias_rest,
                "expira_em":      expira,
                "sem_limite":     False,
                "trial":          (cod == PLANO_EXP[0]),
                "codigo_plano":   cod,
            }
    return resultado


def listar_todos_pagamentos() -> dict[int, list[dict]]:
    """Devolve dict {barbearia_id: [pagamentos]} para TODAS as barbearias.
    Uma única query em vez de N queries — evita busy_timeout stack no dashboard root."""
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM pagamentos ORDER BY registado_em DESC").fetchall()
    resultado: dict = {}
    for row in rows:
        bid = row["barbearia_id"]
        if bid not in resultado:
            resultado[bid] = []
        resultado[bid].append(dict(row))
    return resultado


def cancelar_plano(barbearia_id: int) -> bool:
    """Cancela o plano activo: expira ontem e desactiva a barbearia imediatamente.
    Usar ontem (não hoje) para que registar_pagamento permita novo plano no mesmo dia."""
    from datetime import date, timedelta
    ontem = (date.today() - timedelta(days=1)).isoformat()
    with _write() as conn:
        conn.execute(
            "UPDATE barbearias SET plano_expira_em=?, ativa=0 WHERE id=?",
            (ontem, barbearia_id))
    return True


def desativar_planos_expirados() -> None:
    """Desactiva barbearias cujo plano_expira_em já passou. Chamado 1×/dia pela thread de limpeza."""
    from datetime import date
    hoje = date.today().isoformat()
    with _write() as conn:
        conn.execute(
            "UPDATE barbearias SET ativa=0 "
            "WHERE plano_expira_em IS NOT NULL AND plano_expira_em < ? AND ativa=1",
            (hoje,))


def toggle_barbearia(id: int) -> None:
    with _write() as conn:
        conn.execute("UPDATE barbearias SET ativa = 1 - ativa WHERE id=?", (id,))


def editar_barbearia(id: int, nome: str) -> None:
    novo_slug = slug_unico(nome, excluir_id=id)
    with _write() as conn:
        conn.execute("UPDATE barbearias SET nome=?, slug=? WHERE id=?", (nome, novo_slug, id))


def set_logo(barbearia_id: int, filename: str) -> None:
    with _write() as conn:
        conn.execute("UPDATE barbearias SET logo=? WHERE id=?", (filename, barbearia_id))


# ── Configurações ──────────────────────────────────────────




# ── Horário de funcionamento ───────────────────────────────

def get_horario(barbearia_id: int) -> list[dict]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM horario_funcionamento WHERE barbearia_id=? ORDER BY dia_semana",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def set_horario_dia(dia_semana: int, hora_abertura: str, hora_fecho: str, fechado: bool | int, barbearia_id: int) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO horario_funcionamento "
            "(barbearia_id,dia_semana,hora_abertura,hora_fecho,fechado) VALUES (?,?,?,?,?)",
            (barbearia_id, dia_semana, hora_abertura, hora_fecho, 1 if fechado else 0))
    invalidar_cache_slots(barbearia_id)


def get_horario_dia(dia_semana: int, barbearia_id: int) -> dict:
    with _read() as conn:
        row = conn.execute(
            "SELECT * FROM horario_funcionamento WHERE barbearia_id=? AND dia_semana=?",
            (barbearia_id, dia_semana)).fetchone()
    return dict(row) if row else {"hora_abertura": "08:00", "hora_fecho": "19:00", "fechado": 0}


# ── Dias fechados ──────────────────────────────────────────

def listar_dias_fechados(barbearia_id: int) -> list[dict]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM dias_fechados WHERE barbearia_id=? ORDER BY data",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]


def adicionar_dia_fechado(data: str, motivo: str, barbearia_id: int) -> None:
    try:
        with _write() as conn:
            conn.execute(
                "INSERT INTO dias_fechados (barbearia_id,data,motivo) VALUES (?,?,?)",
                (barbearia_id, data, motivo))
        invalidar_cache_slots(barbearia_id)
    except sqlite3.IntegrityError:
        pass  # dia já fechado (UNIQUE constraint)


def remover_dia_fechado(id: int) -> None:
    with _write() as conn:
        row = conn.execute("SELECT barbearia_id FROM dias_fechados WHERE id=?", (id,)).fetchone()
        bid = row["barbearia_id"] if row else None
        conn.execute("DELETE FROM dias_fechados WHERE id=?", (id,))
    if bid:
        invalidar_cache_slots(bid)


def dia_esta_fechado(data_str: str, barbearia_id: int) -> bool:
    with _read() as conn:
        row = conn.execute(
            "SELECT id FROM dias_fechados WHERE barbearia_id=? AND data=?",
            (barbearia_id, data_str)).fetchone()
    return row is not None


# ── Ausências de barbeiros ─────────────────────────────────

def listar_ausencias(barbearia_id: int, barbeiro_id: int | None = None) -> list[dict]:
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


def criar_ausencia(barbeiro_id: int, data_inicio: str, data_fim: str, tipo: str,
                   motivo: str = "", hora_inicio: str | None = None, hora_fim: str | None = None) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT INTO ausencias (barbeiro_id,data_inicio,data_fim,tipo,motivo,hora_inicio,hora_fim) VALUES (?,?,?,?,?,?,?)",
            (barbeiro_id, data_inicio, data_fim, tipo, motivo, hora_inicio, hora_fim))
        barb = conn.execute("SELECT barbearia_id FROM barbeiros WHERE id=?", (barbeiro_id,)).fetchone()
        bid  = barb["barbearia_id"] if barb else None
    if bid:
        invalidar_cache_slots(bid)


def apagar_ausencia(id: int) -> None:
    with _write() as conn:
        row = conn.execute(
            "SELECT b.barbearia_id FROM ausencias a "
            "JOIN barbeiros b ON a.barbeiro_id=b.id WHERE a.id=?", (id,)).fetchone()
        bid = row["barbearia_id"] if row else None
        conn.execute("DELETE FROM ausencias WHERE id=?", (id,))
    if bid:
        invalidar_cache_slots(bid)


def ausencia_ativa(barbeiro_id: int, data_str: str, hora_str: str | None = None) -> dict | None:
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
        except (ValueError, IndexError, AttributeError):
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


def barbeiro_ausente(barbeiro_id: int, data_str: str, hora_str: str | None = None) -> bool:
    return ausencia_ativa(barbeiro_id, data_str, hora_str) is not None


# ── Bloqueio de clientes ──────────────────────────────────────

def cliente_bloquear(barbearia_id: int, telefone: str, motivo: str = "") -> None:
    from db._conn import normalizar_tel, _agora, FMT
    tel = normalizar_tel(telefone) or telefone
    with _write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clientes_bloqueados "
            "(barbearia_id, telefone, motivo, bloqueado_em) VALUES (?, ?, ?, ?)",
            (barbearia_id, tel, (motivo or "")[:200], _agora().strftime(FMT)))


def cliente_desbloquear(id: int) -> None:
    with _write() as conn:
        conn.execute("DELETE FROM clientes_bloqueados WHERE id=?", (id,))


def cliente_bloqueado(barbearia_id, telefone) -> bool:
    from db._conn import normalizar_tel
    tel = normalizar_tel(telefone) or telefone
    with _read() as conn:
        row = conn.execute(
            "SELECT id FROM clientes_bloqueados WHERE barbearia_id=? AND telefone=?",
            (barbearia_id, tel)).fetchone()
    return row is not None


def clientes_bloqueados_listar(barbearia_id: int) -> list[dict]:
    with _read() as conn:
        rows = conn.execute(
            "SELECT * FROM clientes_bloqueados WHERE barbearia_id=? ORDER BY bloqueado_em DESC",
            (barbearia_id,)).fetchall()
    return [dict(r) for r in rows]

