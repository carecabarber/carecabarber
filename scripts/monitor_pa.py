#!/usr/bin/env python3
"""monitor_pa.py — Verifica se a app PythonAnywhere está online.

Ping ao /healthz a cada execução (via systemd timer).
Se falhar → notifica via:
  1. notify-send (desktop — se sessão gráfica activa)
  2. /tmp/barbearia_down.flag (para outros scripts verificarem)
  3. Email SMTP (se MONITOR_EMAIL_FROM + MONITOR_EMAIL_TO definidos)

Usar:
  python3 scripts/monitor_pa.py            # run once
  python3 scripts/monitor_pa.py --status   # mostra estado do último check
"""
import os
import sys
import json
import time
import socket
import smtplib
import argparse
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

# ── Configuração ──────────────────────────────────────────────
URL             = os.environ.get("MONITOR_URL", "https://carecabarber.pythonanywhere.com/healthz")
TIMEOUT         = int(os.environ.get("MONITOR_TIMEOUT", "15"))
FLAG_FILE       = Path("/tmp/barbearia_down.flag")
STATE_FILE      = Path(os.path.expanduser("~/.cache/barbearia_monitor_state.json"))
MAX_RETRIES     = 2          # tenta N vezes antes de alarmar
RETRY_DELAY     = 5          # segundos entre tentativas
# Email (opcional — não obrigatório)
EMAIL_FROM      = os.environ.get("MONITOR_EMAIL_FROM", "")
EMAIL_TO        = os.environ.get("MONITOR_EMAIL_TO", "")
EMAIL_PASS      = os.environ.get("MONITOR_EMAIL_PASS", "")
SMTP_HOST       = os.environ.get("MONITOR_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.environ.get("MONITOR_SMTP_PORT", "587"))
# Só alarma se estiver em baixo N vezes seguidas (evita falsos positivos)
DOWN_THRESHOLD  = int(os.environ.get("MONITOR_DOWN_THRESHOLD", "2"))


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"consecutive_down": 0, "last_down": None, "last_up": None,
                "alerted": False}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, default=str))


def _check_once() -> tuple[bool, str]:
    """Devolve (ok, detalhe)."""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "barbearia-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.loads(r.read())
            if body.get("status") == "ok":
                return True, f"HTTP 200 — db={body.get('db')} uptime={body.get('uptime_s')}s"
            return False, f"HTTP 200 mas status≠ok: {body}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except socket.timeout:
        return False, f"Timeout após {TIMEOUT}s"
    except Exception as e:
        return False, f"Erro inesperado: {e}"


def _ping_with_retry() -> tuple[bool, str]:
    for attempt in range(MAX_RETRIES):
        ok, detail = _check_once()
        if ok:
            return True, detail
        if attempt < MAX_RETRIES - 1:
            _log(f"Tentativa {attempt+1} falhou ({detail}) — a aguardar {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    return False, detail


def _notify_desktop(title: str, body: str) -> None:
    try:
        os.system(f'notify-send "{title}" "{body}" 2>/dev/null')
    except Exception:
        pass


def _send_email(subject: str, body: str) -> bool:
    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASS):
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        _log(f"Email falhou: {e}")
        return False


def _alarme_down(detail: str) -> None:
    _log(f"🔴 ALARME: App em baixo — {detail}")
    FLAG_FILE.write_text(f"DOWN since {datetime.now().isoformat()}\n{detail}\n")
    _notify_desktop("🔴 Barbearia PA em baixo!", detail[:100])
    sent = _send_email(
        subject="🔴 [Barbearia] App em baixo",
        body=f"URL: {URL}\nDetalhe: {detail}\nHora: {datetime.now().isoformat()}\n"
    )
    if sent:
        _log("Email de alerta enviado.")


def _alarme_recuperou() -> None:
    _log("🟢 App recuperou — a limpar flag.")
    FLAG_FILE.unlink(missing_ok=True)
    _notify_desktop("🟢 Barbearia PA voltou!", "App está online novamente.")
    _send_email(
        subject="🟢 [Barbearia] App voltou",
        body=f"URL: {URL}\nHora: {datetime.now().isoformat()}\n"
    )


def run() -> int:
    now = datetime.now().isoformat()
    state = _load_state()

    ok, detail = _ping_with_retry()

    if ok:
        _log(f"🟢 OK — {detail}")
        was_down = state.get("alerted", False)
        state["consecutive_down"] = 0
        state["last_up"] = now
        if was_down:
            state["alerted"] = False
            _alarme_recuperou()
    else:
        state["consecutive_down"] = state.get("consecutive_down", 0) + 1
        state["last_down"] = now
        _log(f"🔴 FALHA ({state['consecutive_down']}x seguidas) — {detail}")
        if state["consecutive_down"] >= DOWN_THRESHOLD and not state.get("alerted"):
            state["alerted"] = True
            _alarme_down(detail)

    _save_state(state)
    return 0 if ok else 1


def status() -> None:
    state = _load_state()
    print(json.dumps(state, indent=2, default=str))
    if FLAG_FILE.exists():
        print(f"\n⚠️  FLAG activa:\n{FLAG_FILE.read_text()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor PythonAnywhere")
    parser.add_argument("--status", action="store_true", help="Mostra estado do último check")
    args = parser.parse_args()

    if args.status:
        status()
    else:
        sys.exit(run())
