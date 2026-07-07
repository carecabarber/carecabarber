#!/usr/bin/env python3
"""scripts/relatorio_mensal.py — Relatório mensal automático por email.

Gera um resumo do mês anterior (clientes atendidos, receita, ticket médio,
repartição por barbeiro, top serviços) para cada barbearia activa e envia-o
por email ao destinatário configurado.

Uso:
    python scripts/relatorio_mensal.py                 # mês anterior, envia email
    python scripts/relatorio_mensal.py --dry-run       # imprime, NÃO envia
    python scripts/relatorio_mensal.py --mes 2026-05   # mês específico
    python scripts/relatorio_mensal.py --bid 1         # só uma barbearia

Configuração (variáveis de ambiente — reutiliza o padrão do monitor_pa.py):
    REPORT_EMAIL_TO     destinatário (fallback: MONITOR_EMAIL_TO)
    MONITOR_EMAIL_FROM  remetente / conta SMTP
    MONITOR_EMAIL_PASS  password (app password do Gmail, etc.)
    MONITOR_SMTP_HOST   default smtp.gmail.com
    MONITOR_SMTP_PORT   default 587

Agendar 1×/mês no PythonAnywhere (Scheduled Tasks), p.ex. dia 1 às 06:00:
    python /home/CarecaBarber/barbearia/scripts/relatorio_mensal.py
"""
import os
import sys
import smtplib
import argparse
from datetime import date
from email.mime.text import MIMEText
from pathlib import Path

# Permitir importar database.py a partir da raiz do projecto
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

import database as db  # noqa: E402

# ── Email (mesmo padrão que monitor_pa.py) ────────────────────
EMAIL_TO   = os.environ.get("REPORT_EMAIL_TO") or os.environ.get("MONITOR_EMAIL_TO", "")
EMAIL_FROM = os.environ.get("MONITOR_EMAIL_FROM", "")
EMAIL_PASS = os.environ.get("MONITOR_EMAIL_PASS", "")
SMTP_HOST  = os.environ.get("MONITOR_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("MONITOR_SMTP_PORT", "587"))


def _mes_anterior() -> str:
    """Devolve o mês anterior ao actual no formato YYYY-MM."""
    hoje = date.today()
    ano, mes = (hoje.year - 1, 12) if hoje.month == 1 else (hoje.year, hoje.month - 1)
    return f"{ano:04d}-{mes:02d}"


def _moeda(barbearia_id: int) -> str:
    try:
        _, moeda = db.get_planos_precos_barbearia(barbearia_id)
        return moeda or "ECV"
    except Exception:
        return "ECV"


def gerar_relatorio(mes: str, bid_filtro: int | None = None) -> str:
    """Constrói o corpo de texto do relatório para todas as barbearias activas."""
    barbearias = db.listar_barbearias(apenas_ativas=True)
    if bid_filtro:
        barbearias = [b for b in barbearias if b["id"] == bid_filtro]

    linhas = [f"📊 RELATÓRIO MENSAL — {mes}", "=" * 44, ""]
    if not barbearias:
        linhas.append("(Nenhuma barbearia activa.)")
        return "\n".join(linhas)

    for b in barbearias:
        bid = b["id"]
        r   = db.resumo_mensal(bid, mes)
        cur = _moeda(bid)
        linhas.append(f"🏠 {b['nome']}")
        linhas.append("-" * 44)
        if r["atendidos"] == 0 and r["cancelados"] == 0 and r["faltas"] == 0:
            linhas.append("  Sem actividade neste mês.")
            linhas.append("")
            continue
        linhas.append(f"  Clientes atendidos : {r['atendidos']}  (walk-ins: {r['walkins']})")
        linhas.append(f"  Receita            : {r['receita']:.0f} {cur}")
        linhas.append(f"  Ticket médio       : {r['ticket_medio']:.0f} {cur}")
        linhas.append(f"  Cancelados/faltas  : {r['cancelados']} / {r['faltas']}  ({r['taxa_perdidos']}% perdidos)")

        ativos = [pb for pb in r["por_barbeiro"] if (pb["atendidos"] or 0) > 0]
        if ativos:
            linhas.append("  Por barbeiro:")
            for pb in ativos:
                linhas.append(f"    • {pb['nome']}: {pb['atendidos']} clientes — {(pb['receita'] or 0):.0f} {cur}")

        if r["top_servicos"]:
            linhas.append("  Top serviços:")
            for s in r["top_servicos"]:
                linhas.append(f"    • {s['nome']}: {s['n']}×  ({(s['receita'] or 0):.0f} {cur})")
        linhas.append("")

    return "\n".join(linhas)


def _enviar_email(assunto: str, corpo: str) -> bool:
    if not (EMAIL_TO and EMAIL_FROM and EMAIL_PASS):
        print("⚠️  Email não configurado (REPORT_EMAIL_TO / MONITOR_EMAIL_FROM / MONITOR_EMAIL_PASS). "
              "Use --dry-run para ver o relatório.", file=sys.stderr)
        return False
    try:
        msg = MIMEText(corpo)
        msg["Subject"] = assunto
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"❌ Falha ao enviar email: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Relatório mensal por email")
    parser.add_argument("--mes", help="Mês no formato YYYY-MM (default: mês anterior)")
    parser.add_argument("--bid", type=int, help="Só esta barbearia (id)")
    parser.add_argument("--dry-run", action="store_true", help="Imprime, não envia email")
    args = parser.parse_args()

    mes = args.mes or _mes_anterior()
    corpo = gerar_relatorio(mes, args.bid)

    if args.dry_run:
        print(corpo)
        return 0

    assunto = f"📊 [Barbearia] Relatório mensal — {mes}"
    if _enviar_email(assunto, corpo):
        print(f"✅ Relatório de {mes} enviado para {EMAIL_TO}.")
        return 0
    # Sem email configurado/falhou → imprime na mesma para não perder o relatório
    print(corpo)
    return 1


if __name__ == "__main__":
    sys.exit(main())
