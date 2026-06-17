#!/usr/bin/env bash
# deploy_all.sh — Deploy completo de TODOS os ficheiros do projecto para PythonAnywhere
#
# Uso: bash .claude/deploy_all.sh [--dry-run]
#   --dry-run  Lista os ficheiros sem fazer upload
#
# Deve ser corrido na raíz do projecto ou via .claude/deploy_all.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="$(cd "$SCRIPT_DIR/.." && pwd)"
CREDS="$LOCAL/.pythonanywhere"
DRY_RUN=0

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# ── Credenciais ───────────────────────────────────────────────
if [[ ! -f "$CREDS" ]]; then
  echo "❌ Ficheiro de credenciais não encontrado: $CREDS" >&2
  exit 1
fi

while IFS='=' read -r key val; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  key="${key//[[:space:]]/}"
  val="${val//[[:space:]]/}"
  declare "$key=$val"
done < "$CREDS"

TOKEN="${API_TOKEN:?Variável API_TOKEN não definida}"
USER_PA="${USER:?}"
REMOTE="/home/$USER_PA/barbearia"
DOMAIN="${DOMAIN:?}"
API="${API_BASE:?}"
LOG_FILE="/tmp/barbearia_deploy_all.log"

# ── Ficheiros a excluir ───────────────────────────────────────
# (padrões relativos à raíz do projecto)
EXCLUDES=(
  ".claude/"
  ".git/"
  "__pycache__/"
  "*.pyc"
  "*.db"
  "*.sqlite"
  ".secret_key"
  ".root_init_password"
  ".pythonanywhere"
  ".env"
  "backups/"
  "security.log"
  "qr_*.png"
  "qr_*.svg"
  "venv/"
)

# ── Construir lista de ficheiros ──────────────────────────────
FILES=()
while IFS= read -r -d '' f; do
  rel="${f#$LOCAL/}"

  # Verificar excluídos
  skip=0
  for pat in "${EXCLUDES[@]}"; do
    case "$rel" in
      ${pat}*|*/${pat}*) skip=1; break ;;
    esac
    # Verificar por extensão (glob simples)
    # shellcheck disable=SC2053
    [[ "$rel" == $pat ]] && { skip=1; break; }
  done

  [[ $skip -eq 1 ]] && continue
  FILES+=("$rel")
done < <(find "$LOCAL" -type f -print0 | sort -z)

TOTAL=${#FILES[@]}
echo "📦 Deploy completo — $TOTAL ficheiros para $DOMAIN"
echo "   Destino: $REMOTE"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
  echo "── DRY RUN ──────────────────────────────────────────────"
  for rel in "${FILES[@]}"; do echo "  $rel"; done
  echo "─────────────────────────────────────────────────────────"
  echo "Total: $TOTAL ficheiros (nenhum foi enviado)"
  exit 0
fi

# ── Upload ────────────────────────────────────────────────────
ERROS=0
OK=0
for rel in "${FILES[@]}"; do
  printf "  %-50s " "$rel"

  HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Authorization: Token $TOKEN" \
    -F "content=@$LOCAL/$rel" \
    "$API/files/path$REMOTE/$rel/")

  if [[ "$HTTP" == "200" || "$HTTP" == "201" ]]; then
    echo "✅"
    (( OK++ )) || true
  else
    echo "❌ HTTP $HTTP"
    (( ERROS++ )) || true
  fi
done

echo ""
echo "── Resultado ────────────────────────────────────────────"
echo "   OK: $OK  |  Erros: $ERROS  |  Total: $TOTAL"

# ── Reload ────────────────────────────────────────────────────
echo ""
echo "🔄 A recarregar a app…"
RELOAD_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
  -X POST \
  -H "Authorization: Token $TOKEN" \
  "$API/webapps/$DOMAIN/reload/")

if [[ "$RELOAD_HTTP" == "200" ]]; then
  sleep 4
  HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "https://$DOMAIN/login" 2>/dev/null || echo "000")

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] deploy_all OK=$OK ERR=$ERROS health=$HEALTH" >> "$LOG_FILE"

  if [[ "$HEALTH" == "200" || "$HEALTH" == "302" || "$HEALTH" == "303" ]]; then
    echo "✅ App online — HTTP $HEALTH"
  else
    echo "⚠️  App pode não estar a responder (HTTP $HEALTH)" >&2
  fi
else
  echo "❌ Reload falhou (HTTP $RELOAD_HTTP)" >&2
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] deploy_all RELOAD FALHOU HTTP=$RELOAD_HTTP" >> "$LOG_FILE"
  exit 1
fi

if [[ $ERROS -gt 0 ]]; then
  echo "⚠️  $ERROS ficheiro(s) com erro — verificar manualmente" >&2
  exit 1
fi
