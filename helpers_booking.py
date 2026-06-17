# helpers_booking.py — Validação, cache, booking, vocabulário, moedas
# Sem dependência de helpers_security; pode ser importado independentemente.

from flask import session, request, url_for
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from collections import defaultdict
import threading
import time
import re
import json
import os

import database as db
from database import (ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO,
                      ST_CANCELADO, ST_NAO_COMP, ST_WALKIN)

# ── Constantes ─────────────────────────────────────────────────
DIAS_PT           = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
LOGOS_DIR         = os.path.join(os.path.dirname(__file__), "static", "logos")
ALLOWED_LOGO_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}

_MAX_NOME    = 100
_MAX_TEL     = 20
_MAX_USERNAME = 50
_MAX_MOTIVO  = 300

_DASHBOARD_CACHE_TTL = 8
_BLOQ_CACHE_TTL      = 30
_CLEANUP_LOCK_TTL    = 300

_HISTORY_PER_PAGE = 50

_DATA_RE  = re.compile(r'^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$')
_HORA_RE  = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
_USER_RE  = re.compile(r'^[a-zA-Z0-9_.-]{3,50}$')
_TEL_RE   = re.compile(r'^[\d\s\+\-\(\)]{7,20}$')


# ── Sessão ──────────────────────────────────────────────────────

def bid() -> int | None:
    return session.get("barbearia_id")


# ── Fuso horário ────────────────────────────────────────────────

def _agora(barbearia_id=None) -> datetime:
    """Hora actual no fuso da barbearia activa."""
    _bid = barbearia_id
    if _bid is None:
        try:
            _bid = session.get("barbearia_id")
        except RuntimeError:
            pass
    if _bid is not None:
        try:
            tz_name = db.get_barbearia_tz(_bid)
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:
            pass
    try:
        return datetime.now(ZoneInfo("Atlantic/Cape_Verde")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


# ── Validação ───────────────────────────────────────────────────

def _val_data(v: str) -> bool:
    return bool(v and _DATA_RE.match(v))


def _val_hora(v: str) -> bool:
    return bool(v and _HORA_RE.match(v))


def _no_passado(data: str, hora: str, barbearia_id: int | None = None) -> bool:
    try:
        alvo = datetime.strptime(f"{data} {hora}:00", "%Y-%m-%d %H:%M:%S")
        return alvo < _agora(barbearia_id)
    except (ValueError, TypeError):
        return True


def _normalizar_tel(v: str) -> str:
    return db.normalizar_tel(v or "")


def _limpar(v: str, maxlen: int = _MAX_NOME) -> str:
    return (v or "").strip()[:maxlen]


def _dentro_horario(data: str, hora: str, duracao_min: int, barbearia_id: int) -> tuple[bool, str | None]:
    try:
        weekday = datetime.strptime(data, "%Y-%m-%d").weekday()
    except (ValueError, TypeError):
        return False, "Data inválida."
    if db.dia_esta_fechado(data, barbearia_id):
        return False, "A barbearia está fechada nesse dia."
    horario = db.get_horario_dia(weekday, barbearia_id)
    if horario.get("fechado"):
        return False, "A barbearia está fechada nesse dia da semana."
    try:
        FMT = "%Y-%m-%d %H:%M:%S"
        slot_ini = datetime.strptime(f"{data} {hora}:00", FMT)
        slot_fim = slot_ini + timedelta(minutes=duracao_min)
        abertura = datetime.strptime(f"{data} {horario['hora_abertura']}:00", FMT)
        fecho    = datetime.strptime(f"{data} {horario['hora_fecho']}:00",    FMT)
    except (ValueError, TypeError):
        return False, "Hora inválida."
    if slot_ini < abertura:
        return False, f"A barbearia abre às {horario['hora_abertura']}. Escolhe um horário a partir dessa hora."
    if slot_ini >= fecho:
        return False, f"A barbearia fecha às {horario['hora_fecho']}. Escolhe um horário anterior."
    if slot_fim > fecho:
        return False, f"O serviço ultrapassaria o horário de fecho ({horario['hora_fecho']}). Escolhe um horário mais cedo."
    return True, None


# ── Cache em memória ─────────────────────────────────────────────────────────
# Cada entrada: (val, exp_monotonic, bust_ts)
#   bust_ts — mtime do ficheiro /tmp/ccb_bust_{bid} no momento do set.
#   Se outro worker invalidar a cache (toca no ficheiro), o mtime muda e a
#   entrada é tratada como miss mesmo que ainda não tenha expirado.
#   Funciona porque o PythonAnywhere usa tmpfs em /tmp (ramdisk) — getmtime é
#   uma lookup de inode em memória, praticamente gratuito.

_pcache: dict = {}
_pcache_lock  = threading.Lock()
_PCACHE_MAX   = 500

def _bust_path(bid: int) -> str:
    return f"/tmp/ccb_bust_{bid}"

def _bust_mtime(bid: int) -> float:
    """Devolve o mtime do ficheiro de bust — 0.0 se não existir."""
    try:
        return os.path.getmtime(_bust_path(bid))
    except OSError:
        return 0.0

def _bust_touch(bid: int) -> None:
    """Toca no ficheiro de bust — sinaliza invalidação a todos os workers."""
    try:
        with open(_bust_path(bid), 'w') as _f:
            _f.write(str(time.time()))
    except OSError:
        pass

def _bust_bid_from_key(key: str) -> int | None:
    """Extrai barbearia_id de uma chave de cache com escopo de barbearia.

    Estrutura das chaves:
      idx_ag:{bid}:{data}        resumo:{bid}:{data}
      bloq:{bid}:{data}          novos:{bid}:{data}   lemb:{bid}:{data}
      estado:{role}:{bid}:{data}  ← barbearia_id na posição 2
    """
    parts = key.split(':', 2)
    prefix = parts[0] if parts else ''
    if prefix in ('idx_ag', 'resumo', 'bloq', 'novos', 'lemb'):
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    if prefix == 'estado' and len(parts) > 2:
        mid = parts[1]  # 'chefe' | 'barb' | 'cli'
        rest = parts[2].split(':', 1)
        return int(rest[0]) if rest and rest[0].isdigit() else None
    return None


def _pc_get(key: str) -> object | None:
    with _pcache_lock:
        e = _pcache.get(key)
        if not e:
            return None
        val, exp, bust_at_set = e
        if time.monotonic() >= exp:
            return None
        bid = _bust_bid_from_key(key)
        if bid is not None and _bust_mtime(bid) > bust_at_set:
            return None   # outro worker invalidou entretanto
        return val


def _pc_set(key: str, val: object, ttl: int | float) -> None:
    bid = _bust_bid_from_key(key)
    bust_at_set = _bust_mtime(bid) if bid is not None else 0.0
    with _pcache_lock:
        if len(_pcache) >= _PCACHE_MAX:
            oldest = sorted(_pcache, key=lambda k: _pcache[k][1])[:100]
            for k in oldest:
                del _pcache[k]
        _pcache[key] = (val, time.monotonic() + ttl, bust_at_set)


def _pc_del(prefix: str) -> None:
    with _pcache_lock:
        keys = [k for k in _pcache if k.startswith(prefix)]
        for k in keys:
            del _pcache[k]


def _pc_evict() -> None:
    now = time.monotonic()
    with _pcache_lock:
        expired = [k for k, e in _pcache.items() if now >= e[1]]
        for k in expired:
            del _pcache[k]


def _invalidar_idx(barbearia_id: int) -> None:
    _pc_del(f"idx_ag:{barbearia_id}:")
    _pc_del(f"resumo:{barbearia_id}:")
    _pc_del(f"bloq:{barbearia_id}:")
    _pc_del(f"estado:chefe:{barbearia_id}:")
    _pc_del(f"estado:barb:{barbearia_id}:")
    _pc_del(f"estado:cli:{barbearia_id}:")
    _pc_del(f"novos:{barbearia_id}:")
    _pc_del(f"lemb:{barbearia_id}:")
    _bust_touch(barbearia_id)   # sinaliza invalidação a todos os workers via /tmp
    db.invalidar_cache_slots(barbearia_id)


# ── Booking lock ────────────────────────────────────────────────

_booking_lock = threading.Lock()


# ── Helpers de booking ──────────────────────────────────────────

def _parse_booking_form(barbearia_id: int, barbearia: dict | None = None, default_sid: int | None = None, default_bid: int | None = None) -> tuple:
    try:
        sid = int(request.form.get("servico_id") or default_sid or 0)
    except (ValueError, TypeError):
        sid = int(default_sid) if default_sid else 0
    try:
        _raw = request.form.get("barbeiro_id") or None
        bid_ = int(_raw) if _raw else (int(default_bid) if default_bid else None)
    except (ValueError, TypeError):
        bid_ = int(default_bid) if default_bid else None
    data = _limpar(request.form.get("data", ""), 10)
    hora = _limpar(request.form.get("hora", ""), 5)
    if not sid or not data or not hora:
        return sid, bid_, data, hora, None, None, "Preenche todos os campos obrigatórios."
    if not _val_data(data) or not _val_hora(hora):
        return sid, bid_, data, hora, None, None, "Data ou hora inválida."
    if _no_passado(data, hora):
        return sid, bid_, data, hora, None, None, "Não podes agendar no passado."
    dh = f"{data} {hora}:00"
    s = db.servico_por_id(sid)
    if not s or s.get("barbearia_id") != barbearia_id:
        return sid, bid_, data, hora, dh, None, "Serviço inválido."
    if bid_:
        _bv = db.get_barbeiro(bid_)
        if not _bv or _bv.get("barbearia_id") != barbearia_id:
            _vprof = get_vocab(
                barbearia.get("tipo") if barbearia else None,
                barbearia.get("vocab_custom") if barbearia else None
            ).get("profissional", "Profissional")
            return sid, bid_, data, hora, dh, s, f"{_vprof} inválido."
    return sid, bid_, data, hora, dh, s, None


def _enriquecer_row(row: dict, servico: dict | None = None, barbeiro: dict | None = None, num_visitas: int = 0) -> dict:
    s = servico
    b = barbeiro
    row["servico_nome"]     = s["nome"]         if s else "Desconhecido"
    row["duracao_estimada"] = s["duracao_min"]   if s else 0
    row["preco"]            = s.get("preco", 0)  if s else 0
    row["valor"]            = row.get("valor") or 0
    row["telefone"]         = row.get("telefone") or ""
    row["barbeiro_nome"]    = (b["nome"] if b else
                               row.get("barbeiro_nome_snap") or "—")
    row["duracao_real"]     = db.duracao_real_minutos(row.get("inicio"), row.get("fim")) or 0
    row["num_visitas"]      = num_visitas
    dh = row.get("data_hora") or ""
    row["hora"] = dh[11:16] if len(dh) >= 16 else ""
    row["data"] = dh[:10]
    row["tipo"] = row.get("tipo") or "agendado"
    if row.get("inicio") and not row.get("fim"):
        try:
            inicio = datetime.strptime(row["inicio"], "%Y-%m-%d %H:%M:%S")
            row["segundos_decorridos"] = max(0, int((_agora() - inicio).total_seconds()))
            fim_est = inicio + timedelta(minutes=row["duracao_estimada"])
            row["hora_fim_estimada"] = fim_est.strftime("%H:%M")
        except (ValueError, TypeError):
            row["segundos_decorridos"] = 0
            row["hora_fim_estimada"] = None
    else:
        row["segundos_decorridos"] = 0
        row["hora_fim_estimada"] = None
    try:
        hm = datetime.strptime(dh, "%Y-%m-%d %H:%M:%S")
        row["minutos_ate"] = int((hm - _agora()).total_seconds() / 60)
    except ValueError:
        row["minutos_ate"] = 999
    token = row.get("token_reagendar")
    if token and row.get("status") in (ST_AGENDADO, ST_WALKIN):
        row["link_reagendar"] = url_for("reagendar_link", token=token, _external=True)
    else:
        row["link_reagendar"] = None
    return row


def enriquecer_lista(agendamentos: list) -> list[dict]:
    if not agendamentos:
        return []
    rows = [dict(a) for a in agendamentos]
    barbearia_id = rows[0].get("barbearia_id")

    sids = list({r["servico_id"] for r in rows if r.get("servico_id")})
    smap = db.get_servicos_por_ids(sids)

    bids = list({r["barbeiro_id"] for r in rows if r.get("barbeiro_id")})
    bmap = db.get_barbeiros_por_ids(bids)

    tels = list({r["telefone"] for r in rows if r.get("telefone")})
    visitas_map = db.contar_visitas_batch(tels, barbearia_id) if tels and barbearia_id else {}

    return [
        _enriquecer_row(
            r,
            servico    = smap.get(r.get("servico_id")),
            barbeiro   = bmap.get(r.get("barbeiro_id")),
            num_visitas= visitas_map.get(r.get("telefone"), 0),
        )
        for r in rows
    ]


def enriquecer(agendamento: dict | None) -> dict | None:
    return enriquecer_lista([agendamento])[0] if agendamento else None


# ── Vocabulário adaptativo ──────────────────────────────────────

VOCAB_TIPOS = {
    'barbearia': {
        'tipo_label':      'Barbearia',
        'estabelecimento': 'Barbearia',
        'estabelecimentos':'Barbearias',
        'profissional':    'Barbeiro',
        'profissionais':   'Barbeiros',
        'servico':         'Serviço',
        'servicos':        'Serviços',
        'agendamento':     'Marcação',
        'agendamentos':    'Marcações',
        'mesa_icon':       '✦',
    },
    'salao_estetica': {
        'tipo_label':      'Salão de Estética',
        'estabelecimento': 'Salão',
        'estabelecimentos':'Salões',
        'profissional':    'Esteticista',
        'profissionais':   'Esteticistas',
        'servico':         'Serviço',
        'servicos':        'Serviços',
        'agendamento':     'Marcação',
        'agendamentos':    'Marcações',
        'mesa_icon':       '✦',
    },
    'spa': {
        'tipo_label':      'Spa / Bem-estar',
        'estabelecimento': 'Spa',
        'estabelecimentos':'Spas',
        'profissional':    'Terapeuta',
        'profissionais':   'Terapeutas',
        'servico':         'Serviço',
        'servicos':        'Serviços',
        'agendamento':     'Marcação',
        'agendamentos':    'Marcações',
        'mesa_icon':       '✦',
    },
    'clinica': {
        'tipo_label':      'Clínica de Estética',
        'estabelecimento': 'Clínica',
        'estabelecimentos':'Clínicas',
        'profissional':    'Técnico',
        'profissionais':   'Técnicos',
        'servico':         'Serviço',
        'servicos':        'Serviços',
        'agendamento':     'Consulta',
        'agendamentos':    'Consultas',
        'mesa_icon':       '✦',
    },
    'outro': {
        'tipo_label':      'Outro',
        'estabelecimento': 'Estabelecimento',
        'estabelecimentos':'Estabelecimentos',
        'profissional':    'Profissional',
        'profissionais':   'Profissionais',
        'servico':         'Serviço',
        'servicos':        'Serviços',
        'agendamento':     'Marcação',
        'agendamentos':    'Marcações',
        'mesa_icon':       '✦',
    },
}
_VOCAB_DEFAULT = VOCAB_TIPOS['barbearia']


def _pluralize_pt(word: str) -> str:
    if not word:
        return word
    w = word.strip()
    lo = w.lower()
    if lo.endswith('ão'):
        return w[:-2] + 'ões'
    if lo.endswith('al'):
        return w[:-2] + 'ais'
    if lo.endswith('el'):
        return w[:-2] + 'éis'
    if lo.endswith('ol'):
        return w[:-2] + 'óis'
    if lo.endswith('ul'):
        return w[:-2] + 'uis'
    if lo.endswith('il'):
        return w[:-2] + 'is'
    if lo.endswith('or') or lo.endswith('az') or lo.endswith('ez') or lo.endswith('iz'):
        return w + 'es'
    if lo.endswith('s'):
        return w
    return w + 's'


def get_vocab(tipo: str | None, vocab_custom=None) -> dict:
    base = dict(VOCAB_TIPOS.get(tipo or 'barbearia', _VOCAB_DEFAULT))
    if tipo == 'outro' and vocab_custom:
        if isinstance(vocab_custom, str):
            try:
                vocab_custom = json.loads(vocab_custom)
            except Exception:
                vocab_custom = {}
        if isinstance(vocab_custom, dict):
            tl  = vocab_custom.get('tipo_label') or base['tipo_label']
            prf = vocab_custom.get('profissional') or base['profissional']
            svc = vocab_custom.get('servico') or base['servico']
            agd = vocab_custom.get('agendamento') or base['agendamento']
            base.update({
                'tipo_label':      tl,
                'estabelecimento': tl,
                'estabelecimentos':_pluralize_pt(tl),
                'profissional':    prf,
                'profissionais':   _pluralize_pt(prf),
                'servico':         svc,
                'servicos':        _pluralize_pt(svc),
                'agendamento':     agd,
                'agendamentos':    _pluralize_pt(agd),
            })
    return base


# ── Moedas ──────────────────────────────────────────────────────

MOEDAS = [
    ("ECV",  "ECV",  "Escudo Cabo-verdiano (ECV)"),
    ("EUR",  "€",    "Euro (€)"),
    ("USD",  "$",    "Dólar Americano ($)"),
    ("GBP",  "£",    "Libra Esterlina (£)"),
    ("BRL",  "R$",   "Real Brasileiro (R$)"),
    ("AOA",  "Kz",   "Kwanza Angolano (Kz)"),
    ("MZN",  "MT",   "Metical Moçambicano (MT)"),
    ("XOF",  "FCFA", "Franco CFA (FCFA)"),
    ("CHF",  "CHF",  "Franco Suíço (CHF)"),
    ("CAD",  "CA$",  "Dólar Canadiano (CA$)"),
]
_MOEDA_MAP = {cod: sim for cod, sim, _ in MOEDAS}
