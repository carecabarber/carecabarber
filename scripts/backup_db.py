#!/usr/bin/env python3
"""scripts/backup_db.py — Backup automático da BD SQLite para directório configurável.

Uso:
    python scripts/backup_db.py [--dest /caminho/backups] [--keep 30]

Configuração em PythonAnywhere (Scheduled Tasks):
    python /home/<user>/barbearia/scripts/backup_db.py

Retém os últimos `--keep` dias de backups e apaga os mais antigos.
Cria um ficheiro de snapshot com SQLite Online Backup API (seguro em concurrent writes).
"""

import argparse
import os
import sys
import sqlite3
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# ── Localizar a BD principal ──────────────────────────────────────────────────
_HERE   = Path(__file__).resolve().parent.parent          # raiz do projecto
_DB     = _HERE / "barbearia.db"
_RL_DB  = _HERE / "rate_limit.db"


def backup(src: Path, dest_dir: Path, tag: str) -> Path:
    """Cria um backup seguro via SQLite Online Backup API. Devolve o caminho do ficheiro criado."""
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"{src.stem}_{tag}_{ts}.db"
    src_conn  = sqlite3.connect(str(src))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn, pages=100)   # 100 páginas por passo — cede GIL entre passos
        dest_conn.execute("PRAGMA integrity_check(1)")
    finally:
        dest_conn.close()
        src_conn.close()
    return dest


def purge_old(dest_dir: Path, prefix: str, keep_days: int) -> list[Path]:
    """Remove backups mais antigos que `keep_days` dias. Devolve lista de ficheiros apagados."""
    cutoff  = datetime.now() - timedelta(days=keep_days)
    removed = []
    for f in sorted(dest_dir.glob(f"{prefix}_*.db")):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                removed.append(f)
        except OSError:
            pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup da BD barbearia")
    parser.add_argument("--dest",  default=str(_HERE / "backups"),
                        help="Directório de destino dos backups (default: <projecto>/backups)")
    parser.add_argument("--keep",  type=int, default=30,
                        help="Dias de backups a reter (default: 30)")
    parser.add_argument("--no-rl", action="store_true",
                        help="Não fazer backup da rate_limit.db (contém apenas dados efémeros)")
    args = parser.parse_args()

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    erros = []

    # ── Backup da BD principal ────────────────────────────────────────────────
    if _DB.exists():
        try:
            out = backup(_DB, dest_dir, "barbearia")
            size_kb = out.stat().st_size // 1024
            print(f"✓ {out.name}  ({size_kb} KB)")
        except Exception as e:
            print(f"✗ Erro ao fazer backup de {_DB.name}: {e}", file=sys.stderr)
            erros.append(e)
    else:
        print(f"⚠ BD não encontrada: {_DB}", file=sys.stderr)

    # ── Backup da rate_limit.db (opcional) ────────────────────────────────────
    if not args.no_rl and _RL_DB.exists():
        try:
            out = backup(_RL_DB, dest_dir, "rate_limit")
            size_kb = out.stat().st_size // 1024
            print(f"✓ {out.name}  ({size_kb} KB)")
        except Exception as e:
            print(f"⚠ rate_limit.db backup falhou (não crítico): {e}", file=sys.stderr)

    # ── Purgar backups antigos ─────────────────────────────────────────────────
    removed = purge_old(dest_dir, "barbearia", args.keep)
    if removed:
        print(f"🗑  Removidos {len(removed)} backup(s) com mais de {args.keep} dias")
    removed_rl = purge_old(dest_dir, "rate_limit", args.keep)
    if removed_rl:
        print(f"🗑  Removidos {len(removed_rl)} backup(s) rate_limit antigos")

    # ── Resumo ─────────────────────────────────────────────────────────────────
    backups = sorted(dest_dir.glob("barbearia_*.db"))
    print(f"\n📦 {len(backups)} backup(s) retidos em {dest_dir}")
    if backups:
        print(f"   Mais recente : {backups[-1].name}")
        print(f"   Mais antigo  : {backups[0].name}")

    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
