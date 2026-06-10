#!/usr/bin/env python3
"""
Migração única: scrypt → pbkdf2:sha256
Cria passwords temporárias para contas com hash scrypt.
Executar UMA VEZ em PythonAnywhere. Resultado em /tmp/migracao_senhas.txt
"""
import sqlite3
import secrets
import string
import os

DB_PATH = "/home/CarecaBarber/barbearia/barbearia.db"
OUT_FILE = "/home/CarecaBarber/barbearia/migracao_senhas.txt"

# Importar werkzeug do virtualenv correcto
import sys
sys.path.insert(0, "/home/CarecaBarber/.local/lib/python3.13/site-packages")
from werkzeug.security import generate_password_hash

def gerar_senha_temp(n=12):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(n))

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, nome, role, username, password_hash FROM barbeiros WHERE password_hash IS NOT NULL"
).fetchall()

linhas = ["=== Migração de senhas scrypt → pbkdf2:sha256 ===\n"]
migradas = 0

for r in rows:
    ph = r["password_hash"] or ""
    if ph.startswith("scrypt:"):
        temp = gerar_senha_temp()
        novo_hash = generate_password_hash(temp, method="pbkdf2:sha256")
        conn.execute(
            "UPDATE barbeiros SET password_hash=? WHERE id=?",
            (novo_hash, r["id"])
        )
        msg = f"  {r['nome']} ({r['username']}, {r['role']}) → senha temporária: {temp}"
        linhas.append(msg)
        print(msg)
        migradas += 1

conn.commit()
conn.close()

if migradas:
    linhas.append(f"\n✅ {migradas} conta(s) migradas.")
    linhas.append("⚠️  Muda estas senhas imediatamente após o login!")
else:
    linhas.append("✅ Nenhuma conta com hash scrypt encontrada.")

with open(OUT_FILE, "w") as f:
    f.write("\n".join(linhas) + "\n")

print(f"\nResultado guardado em {OUT_FILE}")
