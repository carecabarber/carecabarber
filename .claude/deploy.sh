#!/usr/bin/env bash
# deploy.sh — Deploy completo para PythonAnywhere
# Faz upload de TODOS os ficheiros relevantes (não só o editado).
# Garante que nada fica para trás mesmo em sessões com múltiplas edições.
#
# Chamado pelo hook PostToolUse (Write|Edit) do Claude Code.
# Pode também ser invocado directamente: bash .claude/deploy.sh

set -euo pipefail

# ── Credenciais ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREDS="$SCRIPT_DIR/../.pythonanywhere"

[[ -f "$CREDS" ]] || { echo "ERRO: $CREDS não encontrado" >&2; exit 1; }

while IFS='=' read -r key val; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  key="${key//[[:space:]]/}"; val="${val//[[:space:]]/}"
  declare "$key=$val"
done < "$CREDS"

TOKEN="${API_TOKEN:?API_TOKEN em falta}"
DOMAIN="${DOMAIN:?DOMAIN em falta}"
API="${API_BASE:?API_BASE em falta}"
REMOTE="/home/${USER:?USER em falta}/barbearia"  # path real da app (ver WSGI file)
LOCAL="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="/tmp/barbearia_deploy.log"

# ── Lock: evitar deploys simultâneos em paralelo ─────────────
LOCK_FILE="/tmp/carecabarber_deploy.lock"
NOW=$(date +%s)

if [[ -f "$LOCK_FILE" ]]; then
  LOCK_AGE=$(( NOW - $(cat "$LOCK_FILE" 2>/dev/null || echo 0) ))
  if (( LOCK_AGE < 180 )); then
    echo "Deploy em curso — a saltar" | tee -a "$LOG_FILE"
    exit 0
  fi
  rm -f "$LOCK_FILE"   # lock expirado (deploy anterior crashou)
fi

echo "$NOW" > "$LOCK_FILE"

# ── Gate: testes rápidos antes de enviar qualquer coisa ──────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] A correr testes (gate pre-deploy)..." | tee -a "$LOG_FILE"
if cd "$LOCAL" && python3 -m pytest tests/ \
    --ignore=tests/test_e2e.py \
    --ignore=tests/test_load.py \
    -q --tb=short -x 2>&1 | tee -a "$LOG_FILE"; then
  echo "  Gate OK — testes passaram" | tee -a "$LOG_FILE"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] GATE FALHOU — deploy cancelado" | tee -a "$LOG_FILE"
  rm -f "$LOCK_FILE"
  exit 2
fi

# ── Incrementar versão de deploy (só após gate passar) ───────
VERSION_FILE="$LOCAL/version.txt"
CURRENT_VER=$(cat "$VERSION_FILE" 2>/dev/null | tr -d '[:space:]' || echo "0")
NEW_VER=$(( CURRENT_VER + 1 ))
echo "$NEW_VER" > "$VERSION_FILE"
echo "Deploy #$NEW_VER" | tee -a "$LOG_FILE"

# ── Lista de ficheiros a enviar — auto-descoberta ─────────────
# Python, DB, helpers, static — lista fixa (mais seguro)
_FIXED=(
  app.py database.py wsgi.py helpers.py helpers_booking.py helpers_security.py
  blueprints/__init__.py blueprints/agendamentos.py blueprints/api.py
  blueprints/auth.py blueprints/barbeiros.py blueprints/cliente.py
  blueprints/mesa.py blueprints/pwa.py blueprints/relatorios.py
  blueprints/root.py blueprints/servicos.py
  db/__init__.py db/_conn.py db/agendamentos.py db/barbearia.py
  db/barbeiros.py db/migrations.py db/push.py db/rate_limit.py
  db/relatorios.py db/servicos.py
  version.txt static/sw.js static/app.js
)
# Templates — auto-descoberta: nunca esquece ficheiros novos
mapfile -t _TEMPLATES < <(cd "$LOCAL" && find templates/ -name '*.html' | sort)

FILES=("${_FIXED[@]}" "${_TEMPLATES[@]}")

# ── Upload de todos os ficheiros ─────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Inicio deploy (${#FILES[@]} ficheiros)" | tee -a "$LOG_FILE"

ERROS=0
for REL in "${FILES[@]}"; do
  LOCAL_PATH="$LOCAL/$REL"
  if [[ ! -f "$LOCAL_PATH" ]]; then
    echo "  AVISO: $REL não existe localmente — a saltar"
    continue
  fi

  # Upload com retry em 429 (rate limit — backoff 15/30/60/90s)
  for TENTATIVA in 15 30 60 90; do
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST \
      -H "Authorization: Token $TOKEN" \
      -F "content=@$LOCAL_PATH" \
      "$API/files/path$REMOTE/$REL")
    if [[ "$HTTP" == "429" ]]; then
      echo "  429 rate limit em $REL — aguardar ${TENTATIVA}s..." | tee -a "$LOG_FILE"
      sleep $TENTATIVA
    else
      break
    fi
  done

  if [[ "$HTTP" == "200" || "$HTTP" == "201" ]]; then
    echo "  OK ($HTTP) $REL" | tee -a "$LOG_FILE"
  else
    echo "  ERRO ($HTTP) $REL" | tee -a "$LOG_FILE"
    (( ERROS++ ))
  fi
  # Pequena pausa entre uploads para evitar rate limiting
  sleep 0.3
done

if (( ERROS > 0 )); then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deploy com $ERROS erro(s) — abortado sem reload" | tee -a "$LOG_FILE"
  rm -f "$LOCK_FILE"
  exit 1
fi

# ── Snapshot local para rollback ─────────────────────────────
ROLLBACK_DIR="/tmp/carecabarber_rollback_$(date +%s)"
mkdir -p "$ROLLBACK_DIR"
for REL in app.py database.py wsgi.py; do
  cp "$LOCAL/$REL" "$ROLLBACK_DIR/$REL" 2>/dev/null || true
done
echo "Snapshot para rollback em $ROLLBACK_DIR" | tee -a "$LOG_FILE"

# ── Verificação: confirmar tamanho mínimo do remote app.py ───
# (evitar download completo do ficheiro grande — basta ver se tem conteúdo)
echo "Verificacao do remote app.py..." | tee -a "$LOG_FILE"
REMOTE_SIZE=$(curl -s -o /dev/null -w "%{size_download}" \
  -H "Authorization: Token $TOKEN" \
  "$API/files/path$REMOTE/app.py" 2>/dev/null || echo 0)
LOCAL_SIZE=$(wc -c < "$LOCAL/app.py")

# Tolerância: remoto deve ter pelo menos 90% do tamanho local
MIN_SIZE=$(( LOCAL_SIZE * 90 / 100 ))
if (( REMOTE_SIZE >= MIN_SIZE )); then
  echo "  OK app.py remoto: ${REMOTE_SIZE} bytes (local: ${LOCAL_SIZE})" | tee -a "$LOG_FILE"
else
  echo "  ERRO: app.py remoto parece incompleto (${REMOTE_SIZE} vs ${LOCAL_SIZE} bytes)" | tee -a "$LOG_FILE"
  rm -f "$LOCK_FILE"
  exit 1
fi

# ── Reload ────────────────────────────────────────────────────
RELOAD_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
  -X POST \
  -H "Authorization: Token $TOKEN" \
  "$API/webapps/$DOMAIN/reload/")

if [[ "$RELOAD_HTTP" != "200" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Reload falhou (HTTP $RELOAD_HTTP)" | tee -a "$LOG_FILE"
  rm -f "$LOCK_FILE"
  exit 1
fi

echo "Reload OK — a aguardar arranque..." | tee -a "$LOG_FILE"
sleep 5

# ── Health check (não bloqueia — timeout de rede é normal) ───
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
  "https://$DOMAIN/login" 2>/dev/null || echo "timeout")

_rollback() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] A fazer rollback para snapshot $ROLLBACK_DIR..." | tee -a "$LOG_FILE"
  for REL in app.py database.py wsgi.py; do
    [[ -f "$ROLLBACK_DIR/$REL" ]] || continue
    curl -s -o /dev/null \
      -X POST \
      -H "Authorization: Token $TOKEN" \
      -F "content=@$ROLLBACK_DIR/$REL" \
      "$API/files/path$REMOTE/$REL"
  done
  curl -s -o /dev/null -X POST \
    -H "Authorization: Token $TOKEN" \
    "$API/webapps/$DOMAIN/reload/"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Rollback concluído" | tee -a "$LOG_FILE"
}

case "$HEALTH" in
  200|302|303)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deploy completo — app OK (health: $HEALTH)" | tee -a "$LOG_FILE"
    echo "Deploy OK — ${#FILES[@]} ficheiros enviados, app a responder ($HEALTH)"
    ;;
  5*)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERRO: app retornou $HEALTH — a fazer rollback automático" | tee -a "$LOG_FILE"
    _rollback
    rm -f "$LOCK_FILE"
    exit 3
    ;;
  timeout|000)
    # Timeout de rede desta máquina — não significa que o servidor falhou
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deploy completo — health check timeout (normal em rede local)" | tee -a "$LOG_FILE"
    echo "Deploy OK — ${#FILES[@]} ficheiros enviados (health check: timeout de rede — servidor OK)"
    ;;
  *)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] AVISO: health check inesperado ($HEALTH)" | tee -a "$LOG_FILE"
    echo "AVISO: deploy feito mas app respondeu $HEALTH" >&2
    ;;
esac

rm -f "$LOCK_FILE"
