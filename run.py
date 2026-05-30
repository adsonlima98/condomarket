"""
CondoMarket - Inicializador
Execute: python run.py

Na primeira vez, rode antes:
    python setup.py
"""
import subprocess
import sys
import os
from pathlib import Path


def check_and_install(package, import_name=None):
    import_name = import_name or package
    try:
        __import__(import_name)
    except ImportError:
        print(f"Instalando {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package, "-q"])


def load_dotenv():
    """Carrega variaveis do arquivo .env."""
    base = Path(__file__).parent
    env_path = base / ".env"

    if env_path.is_dir():
        print("ERRO: '.env' e uma pasta, nao um arquivo!")
        print("Execute: python setup.py")
        sys.exit(1)

    if not env_path.is_file():
        print("Arquivo .env nao encontrado. Execute: python setup.py")
        return

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


if __name__ == "__main__":
    print("CondoMarket — iniciando...")

    check_and_install("fastapi")
    check_and_install("uvicorn")
    check_and_install("python-multipart", "multipart")
    check_and_install("python-jose[cryptography]", "jose")
    check_and_install("sqlalchemy", "sqlalchemy")
    check_and_install("bcrypt")
    check_and_install("slowapi")

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Carrega .env ANTES de importar qualquer modulo da aplicacao
    load_dotenv()

    db_path = Path(__file__).parent / "marketplace.db"
    if not db_path.exists():
        print("Banco de dados nao encontrado. Execute: python setup.py")
        sys.exit(1)

    print("Acesse: http://localhost:8000")
    print("Para parar: Ctrl+C")

    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
