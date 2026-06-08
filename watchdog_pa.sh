#!/bin/bash
# watchdog_pa.sh — Monitoriza carecabarber.pythonanywhere.com
# Se /healthz não responder 200 em 10s → reload via API PythonAnywhere
#
# Crontab (corre a cada 5 minutos):
#   */5 * * * * /home/helder-neves/Documentos/barbearia/watchdog_pa.sh >> /tmp/watchdog_pa.log 2>&1
#
# Variáveis de ambiente necessárias (definir aqui ou em ~/.bashrc):
#   PA_TOKEN   — API token do PythonAnywhere (em Account → API Token)
#   PA_USER    — username PA (ex: carecabarber)
#   PA_DOMAIN  — domínio completo (ex: carecabarber.pythonanywhere.com)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PA_CREDS="$SCRIPT_DIR/.pythonanywhere"

# Ler credenciais do ficheiro .pythonanywhere (mesmo dir que o script)
if [ -f "$PA_CREDS" ]; then
    PA_TOKEN=$(grep '^API_TOKEN=' "$PA_CREDS" | cut -d= -f2)
    PA_USER=$(grep '^USER=' "$PA_CREDS" | cut -d= -f2)
    PA_DOMAIN=$(grep '^DOMAIN=' "$PA_CREDS" | cut -d= -f2)
else
    PA_TOKEN="${PA_TOKEN:-}"
    PA_USER="${PA_USER:-CarecaBarber}"
    PA_DOMAIN="${PA_DOMAIN:-carecabarber.pythonanywhere.com}"
fi

HEALTHZ_URL="https://${PA_DOMAIN}/healthz"
PA_API="https://www.pythonanywhere.com/api/v0/user/${PA_USER}/webapps/${PA_DOMAIN}/reload/"
LOG_FILE="/tmp/watchdog_pa.log"
LOCK_FILE="/tmp/watchdog_pa.lock"
COOLDOWN=300  # segundos entre reloads (evitar loop de reloads)

DATA=$(date '+%Y-%m-%d %H:%M:%S')

# ── Verificar token ──────────────────────────────────────────────────────────
if [ -z "$PA_TOKEN" ]; then
    echo "[$DATA] ERRO: PA_TOKEN não definido. Cria .pythonanywhere ou exporta PA_TOKEN." | tee -a "$LOG_FILE"
    exit 1
fi

# ── Ping /healthz ────────────────────────────────────────────────────────────
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$HEALTHZ_URL" 2>/dev/null)

if [ "$HTTP_STATUS" = "200" ]; then
    echo "[$DATA] OK — healthz $HTTP_STATUS" | tee -a "$LOG_FILE"
    exit 0
fi

echo "[$DATA] FALHOU — healthz devolveu '$HTTP_STATUS'" | tee -a "$LOG_FILE"

# ── Cooldown: evitar reloads em cascata ─────────────────────────────────────
if [ -f "$LOCK_FILE" ]; then
    LOCK_TIME=$(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
    AGORA=$(date +%s)
    DIFF=$((AGORA - LOCK_TIME))
    if [ "$DIFF" -lt "$COOLDOWN" ]; then
        echo "[$DATA] Cooldown activo (último reload há ${DIFF}s). A aguardar." | tee -a "$LOG_FILE"
        exit 0
    fi
fi

# ── Reload via API PythonAnywhere ────────────────────────────────────────────
echo "[$DATA] A fazer reload via PA API..." | tee -a "$LOG_FILE"

RELOAD_RESP=$(curl -s -w "\n%{http_code}" -X POST "$PA_API" \
    -H "Authorization: Token $PA_TOKEN" \
    --max-time 30 2>/dev/null)

RELOAD_CODE=$(echo "$RELOAD_RESP" | tail -1)
RELOAD_BODY=$(echo "$RELOAD_RESP" | head -1)

if [ "$RELOAD_CODE" = "200" ]; then
    echo "[$DATA] Reload OK ($RELOAD_CODE): $RELOAD_BODY" | tee -a "$LOG_FILE"
    touch "$LOCK_FILE"
else
    echo "[$DATA] Reload FALHOU ($RELOAD_CODE): $RELOAD_BODY" | tee -a "$LOG_FILE"
fi

# ── Notificação desktop (se correndo localmente) ─────────────────────────────
if command -v notify-send >/dev/null 2>&1; then
    notify-send "CarecaBarber Watchdog" "Site em baixo — reload executado ($RELOAD_CODE)" --urgency=critical 2>/dev/null || true
fi

exit 0
