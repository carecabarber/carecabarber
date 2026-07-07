import sys
import os
import time
import sqlite3

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# VAPID: lido por helpers_security.py no import — tem de estar definido ANTES do
# import da app. setdefault não sobrepõe se já estiver no ambiente do PythonAnywhere.
os.environ.setdefault("VAPID_PRIVATE_KEY", "om_CnOBDJSLlLHSqt0S_Z6AK16rYSzwf9Ebx8D_enQM")

# Sentry (observabilidade de erros em produção). Para ACTIVAR:
#   1. Criar projecto Flask em https://sentry.io e copiar o DSN.
#   2. Descomentar a linha abaixo e colar o DSN.
#   3. Reload da web app no PythonAnywhere.
# Confirmar: curl https://carecabarber.pythonanywhere.com/healthz → "sentry": true
# os.environ.setdefault("SENTRY_DSN", "https://<chave>@<org>.ingest.sentry.io/<id>")

# Caminho absoluto para a pasta do projeto
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Mudar para a pasta do projeto (importante para o SQLite e .secret_key)
os.chdir(project_home)

# Aguardar que a DB esteja legível antes de importar a app. Um worker ANTIGO que
# ainda corra a versão EXCLUSIVE segura o ficheiro até ser morto (até 60s de
# "mercy"); uma leitura a uma tabela real (sqlite_master) precisa de SHARED lock,
# logo BLOQUEIA enquanto esse worker existir e só passa quando ele morre.
# IMPORTANTE: NÃO usar locking_mode=EXCLUSIVE aqui — esta conexão é descartável e
# adquirir EXCLUSIVE deixaria o ficheiro preso para o próprio import a seguir.
# Quando todos os workers já forem não-EXCLUSIVE, este SELECT passa de imediato.
_DB_PATH = os.path.join(project_home, "barbearia.db")
for _i in range(45):          # max 90 s de espera (cobre os 60s de mercy + folga)
    _c = None
    try:
        _c = sqlite3.connect(_DB_PATH, timeout=2)
        _c.execute("PRAGMA busy_timeout=2000")
        _c.execute("SELECT 1 FROM sqlite_master LIMIT 1")  # precisa SHARED lock
        _c.close()
        break                 # DB legível — worker antigo já não segura o ficheiro
    except sqlite3.OperationalError:
        if _c is not None:
            try:
                _c.close()
            except sqlite3.OperationalError:
                pass
        if _i < 44:
            time.sleep(2)
        # na última tentativa deixa o import tentar na mesma

from app import app as application
