#!/usr/bin/env python3
"""backup_prod.py — Backup OFFSITE da base de dados de produção (PythonAnywhere).

Puxa a barbearia.db de produção via API do PythonAnywhere e guarda uma cópia
datada em ~/Documentos/barbearia/backups_prod/. Essa pasta está dentro de
~/Documentos/, que o backup do laptop (rclone → Dropbox) sincroniza — logo o
backup fica automaticamente offsite.

Cada cópia é validada com PRAGMA integrity_check antes de ser aceite; uma cópia
corrupta é descartada e o script falha (exit 1) sem apagar backups antigos.

Mantém os últimos MAX_BACKUPS. Uso:
    python3 scripts/backup_prod.py            # backup normal
    python3 scripts/backup_prod.py --check    # só verifica ligação + integridade do último
"""

import os, sys, ssl, glob, sqlite3, tempfile, argparse, urllib.request, urllib.error
from datetime import datetime

RAIZ        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF        = os.path.join(RAIZ, ".pythonanywhere")
BACKUP_DIR  = os.path.join(RAIZ, "backups_prod")
MAX_BACKUPS = 30
TIMEOUT     = 90


def ler_config():
    cfg = {}
    if not os.path.exists(CONF):
        print(f"❌  Configuração não encontrada: {CONF}", file=sys.stderr)
        sys.exit(1)
    with open(CONF) as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            cfg[k.strip()] = v.strip()
    if "USER" not in cfg or "API_TOKEN" not in cfg:
        print("❌  USER e API_TOKEN em falta em .pythonanywhere", file=sys.stderr)
        sys.exit(1)
    return cfg


def descarregar(user, token):
    url = (f"https://www.pythonanywhere.com/api/v0/user/{user}"
           f"/files/path/home/{user}/barbearia/barbearia.db")
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        print(f"❌  HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"❌  Rede: {e}", file=sys.stderr)
        sys.exit(1)


def validar(caminho):
    """Abre a DB e corre integrity_check + conta agendamentos. Devolve (ok, resumo)."""
    try:
        conn = sqlite3.connect(caminho)
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integ != "ok":
            return False, f"integrity_check: {integ}"
        n_ag  = conn.execute("SELECT COUNT(*) FROM agendamentos").fetchone()[0]
        n_bar = conn.execute("SELECT COUNT(*) FROM barbearias").fetchone()[0]
        conn.close()
        return True, f"{n_bar} barbearias, {n_ag} agendamentos"
    except Exception as e:
        return False, str(e)


def rodar(user, token):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dados = descarregar(user, token)

    # Escrever primeiro para ficheiro temporário e validar antes de aceitar
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=BACKUP_DIR)
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(dados)

    ok, resumo = validar(tmp)
    if not ok:
        os.remove(tmp)
        print(f"❌  Backup descartado — DB inválida ({resumo})", file=sys.stderr)
        sys.exit(1)

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"barbearia_prod_{ts}.db")
    os.rename(tmp, dest)
    print(f"✅  Backup OK: {os.path.basename(dest)}  ({len(dados)//1024} KB — {resumo})")

    # Rotação — manter só os últimos MAX_BACKUPS
    copias = sorted(glob.glob(os.path.join(BACKUP_DIR, "barbearia_prod_*.db")))
    for f in copias[:max(0, len(copias) - MAX_BACKUPS)]:
        os.remove(f)
        print(f"🗑️   Removido antigo: {os.path.basename(f)}")
    print(f"📦  Total de backups: {min(len(copias), MAX_BACKUPS)}  em {BACKUP_DIR}")


def verificar(user, token):
    """--check: testa ligação e valida o backup mais recente (sem criar novo)."""
    dados = descarregar(user, token)
    print(f"🔗  Ligação OK — produção: {len(dados)//1024} KB")
    copias = sorted(glob.glob(os.path.join(BACKUP_DIR, "barbearia_prod_*.db")))
    if not copias:
        print("⚠️   Ainda não há backups locais.")
        return
    ok, resumo = validar(copias[-1])
    estado = "✅ válido" if ok else f"❌ INVÁLIDO ({resumo})"
    print(f"Último backup: {os.path.basename(copias[-1])} — {estado}")
    if ok:
        print(f"   Conteúdo: {resumo}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Só verifica ligação + integridade do último backup")
    args = ap.parse_args()
    cfg = ler_config()
    if args.check:
        verificar(cfg["USER"], cfg["API_TOKEN"])
    else:
        rodar(cfg["USER"], cfg["API_TOKEN"])


if __name__ == "__main__":
    main()
