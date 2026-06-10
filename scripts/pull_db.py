#!/usr/bin/env python3
"""pull_db.py — Descarrega a base de dados de produção (PythonAnywhere) para o ambiente local.

Uso:
    python3 scripts/pull_db.py            # descarrega para barbearia.db (sobrescreve)
    python3 scripts/pull_db.py --backup   # faz backup da DB local antes de sobrescrever
    python3 scripts/pull_db.py --dry-run  # só verifica ligação, não descarrega

A configuração (token, user, domain) é lida de .pythonanywhere no raiz do projecto.
"""

import os, sys, shutil, argparse, configparser, urllib.request, urllib.error
from datetime import datetime

# ── Localizar raiz do projecto ────────────────────────────────────────────────
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(RAIZ, ".pythonanywhere")
DB_LOCAL = os.path.join(RAIZ, "barbearia.db")

def ler_config():
    """Lê USER e API_TOKEN de .pythonanywhere (formato KEY=VALUE)."""
    cfg = {}
    if not os.path.exists(CONF):
        print(f"❌  Ficheiro de configuração não encontrado: {CONF}", file=sys.stderr)
        sys.exit(1)
    with open(CONF) as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            if "=" in linha:
                k, v = linha.split("=", 1)
                cfg[k.strip()] = v.strip()
    if "USER" not in cfg or "API_TOKEN" not in cfg:
        print("❌  USER e API_TOKEN em falta em .pythonanywhere", file=sys.stderr)
        sys.exit(1)
    return cfg

def descarregar_db(user: str, token: str, dry_run=False, backup=False):
    url  = f"https://www.pythonanywhere.com/api/v0/user/{user}/files/path/home/{user}/barbearia/barbearia.db"
    req  = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})

    print(f"🔗  A ligar a {url.split('/api')[0]}…")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            tamanho = resp.headers.get("Content-Length", "?")
            print(f"✅  Ligação OK — tamanho: {tamanho} bytes")
            if dry_run:
                print("ℹ️   --dry-run: nenhum ficheiro alterado.")
                return
            dados = resp.read()
    except urllib.error.HTTPError as e:
        print(f"❌  HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌  Erro de rede: {e.reason}", file=sys.stderr)
        sys.exit(1)

    # Backup da DB local antes de sobrescrever
    if backup and os.path.exists(DB_LOCAL):
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = os.path.join(RAIZ, f"barbearia.db.bak_{ts}")
        shutil.copy2(DB_LOCAL, bak)
        print(f"📦  Backup local: {bak}")

    with open(DB_LOCAL, "wb") as f:
        f.write(dados)
    print(f"✅  DB descarregada → {DB_LOCAL}  ({len(dados):,} bytes)")

    # Correr init_db para garantir que migrações locais estão actualizadas
    try:
        sys.path.insert(0, RAIZ)
        import database as db
        db.init_db()
        print("✅  Migrações locais verificadas (init_db OK)")
    except Exception as e:
        print(f"⚠️   init_db: {e}")

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup",  action="store_true", help="Faz backup da DB local antes de sobrescrever")
    ap.add_argument("--dry-run", action="store_true", help="Verifica ligação sem descarregar")
    args = ap.parse_args()

    cfg = ler_config()
    descarregar_db(cfg["USER"], cfg["API_TOKEN"],
                   dry_run=args.dry_run, backup=args.backup)

if __name__ == "__main__":
    main()
