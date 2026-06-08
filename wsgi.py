import sys
import os

# Caminho absoluto para a pasta do projeto
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Mudar para a pasta do projeto (importante para o SQLite e .secret_key)
os.chdir(project_home)

from app import app as application
