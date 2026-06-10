#!/usr/bin/env python3
"""
Backup automático da base de dados SQLite da Barbearia.
Copia barbearia.db → ~/backups/barbearia_YYYYMMDD_HHMMSS.db usando a
SQLite Online Backup API (sqlite3.connect().backup()), que é segura mesmo
com escritas concorrentes em curso (WAL mode).
Mantém apenas os últimos MAX_BACKUPS backups (apaga os mais antigos).
Executa diariamente via PythonAnywhere Scheduled Tasks.
"""
import os
import glob
import sqlite3
from datetime import datetime

DB_SRC      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "barbearia.db")
BACKUP_DIR  = os.path.expanduser("~/backups")
MAX_BACKUPS = 30


def main():
    if not os.path.exists(DB_SRC):
        print(f"[backup] ERRO: base de dados não encontrada: {DB_SRC}")
        raise SystemExit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"barbearia_{timestamp}.db")

    # Usar a SQLite Online Backup API — segura com escritas concorrentes
    # (equivalente a .backup() do CLI do sqlite3)
    src_conn  = sqlite3.connect(DB_SRC)
    dest_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dest_conn, pages=256)   # copia em páginas de 256 (non-blocking)
        dest_conn.close()
        src_conn.close()
    except Exception:
        dest_conn.close()
        src_conn.close()
        # Apagar destino parcial para não deixar ficheiro corrompido
        if os.path.exists(dest):
            os.remove(dest)
        raise

    size_kb = os.path.getsize(dest) // 1024
    print(f"[backup] Cópia criada: {dest} ({size_kb} KB)")

    # Apagar backups mais antigos se passar do limite
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "barbearia_*.db")))
    excesso = backups[:max(0, len(backups) - MAX_BACKUPS)]
    for f in excesso:
        os.remove(f)
        print(f"[backup] Apagado backup antigo: {f}")

    restantes = min(len(backups), MAX_BACKUPS)
    print(f"[backup] Total de backups: {restantes}")


if __name__ == "__main__":
    main()
