# helpers.py — Shim de compatibilidade.
#
# A implementação real divide-se em dois módulos:
#   helpers_booking.py  — validação, cache, booking, vocabulário, moedas
#   helpers_security.py — logging, rate limiting, auth, push, imagens
#
# Todo o código externo continua a fazer ``from helpers import ...`` sem alterações.

from html import escape as _html_escape  # re-exportado para blueprints/barbeiros.py

# ── helpers_booking ───────────────────────────────────────────────
from helpers_booking import (
    DIAS_PT, LOGOS_DIR, ALLOWED_LOGO_EXTS,
    _MAX_NOME, _MAX_TEL, _MAX_USERNAME, _MAX_MOTIVO,
    _DASHBOARD_CACHE_TTL, _BLOQ_CACHE_TTL, _CLEANUP_LOCK_TTL,
    _HISTORY_PER_PAGE,
    _DATA_RE, _HORA_RE, _USER_RE, _TEL_RE,
    bid,
    _agora,
    _val_data, _val_hora, _no_passado, _normalizar_tel, _limpar, _dentro_horario,
    _pcache, _pcache_lock, _PCACHE_MAX,
    _pc_get, _pc_set, _pc_del, _pc_evict, _invalidar_idx,
    _booking_lock,
    _parse_booking_form, _enriquecer_row, enriquecer_lista, enriquecer,
    VOCAB_TIPOS, _VOCAB_DEFAULT, _pluralize_pt, get_vocab,
    MOEDAS, _MOEDA_MAP,
)

# ── helpers_security ──────────────────────────────────────────────
from helpers_security import (
    _PUSH_OK, _VAPID_PRIVATE_KEY, _VAPID_PUBLIC_KEY, _VAPID_CLAIMS,
    _push_notif, _push_async, _push_one, _push_espera,
    _JsonFormatter, _make_json_handler,
    _slog, _blog_logger, _elog,
    _log, _blog, _audit,
    _ip_attempts, _ip_lock, _IP_MAX, _IP_WINDOW,
    _ip_backoff,
    _user_fails, _user_lock, _USER_MAX, _USER_LOCKOUT,
    _api_calls, _api_lock, _API_MAX, _API_WINDOW,
    _ip_ok, _ip_retry_after, _api_ok,
    _user_locked, _record_fail, _clear_fails,
    _DUMMY_HASH,
    _IMG_MAGIC, _mime_ok,
    _FOTO_MIME_OK, _FOTO_MAX_BYTES, _FOTO_MAGIC, _validar_imagem,
    _salvar_logo,
    _PDF_OK,
    staff_required, chefe_required, root_required,
    pode_gerir_agendamento,
)
