"""
CondoMarket - Configuracao inicial (APENAS DESENVOLVIMENTO)
Execute UMA VEZ antes de rodar o servidor:
    python setup.py

ATENCAO: Este script usa credenciais de demonstracao fixas (admin123, morador1, etc.)
         que sao publicas e conhecidas. Nao use em producao com SEED_DATA=true.
         Em producao, defina ADMIN_PASSWORD via variavel de ambiente e mantenha SEED_DATA=false.
"""
import os
import sys
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
DB_PATH  = os.path.join(BASE_DIR, "marketplace.db")


def criar_env():
    """Garante que .env e um arquivo de texto valido."""
    if os.path.isdir(ENV_PATH):
        import shutil
        shutil.rmtree(ENV_PATH)
        print("[OK] Pasta .env removida e substituida por arquivo.")

    if os.path.isfile(ENV_PATH):
        print("[OK] Arquivo .env ja existe.")
        return

    import secrets
    chave = secrets.token_hex(32)
    conteudo = (
        "# CondoMarket - configuracoes locais\n"
        "# NUNCA envie este arquivo para o GitHub\n\n"
        f"SECRET_KEY={chave}\n"
        "ADMIN_PASSWORD=admin123\n"
        "ADMIN_PHONE=11999999999\n"
        "SEED_DATA=true\n"
        "BCRYPT_ROUNDS=12\n"
        "PASSWORD_MIN_LEN=6\n"
    )
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(conteudo)
    print("[OK] Arquivo .env criado com SECRET_KEY segura.")


def carregar_env():
    """Carrega variaveis do .env para os.environ."""
    if not os.path.isfile(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value
    print("[OK] Variaveis do .env carregadas.")


def recriar_banco():
    """Apaga e recria o banco com senhas bcrypt."""
    # Remove banco antigo (pode estar corrompido)
    for path in [DB_PATH, DB_PATH + "-journal", DB_PATH + "-wal", DB_PATH + "-shm"]:
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"[OK] {os.path.basename(path)} removido.")
            except PermissionError:
                print(f"[ERRO] Nao foi possivel remover {os.path.basename(path)}.")
                print()
                print("  O arquivo esta sendo usado por outro programa.")
                print("  Feche o servidor (Ctrl+C) e delete manualmente:")
                print(f"  {path}")
                print()
                sys.exit(1)
            except Exception as e:
                print(f"[ERRO] {e}")
                sys.exit(1)

    # Remove modulos em cache para recarregar do zero
    for mod in list(sys.modules.keys()):
        if "database" in mod or "api" in mod:
            del sys.modules[mod]

    sys.path.insert(0, BASE_DIR)

    import importlib
    db_mod = importlib.import_module("database")
    db_mod.init_db()
    print("[OK] Banco recriado com senhas bcrypt.")


def verificar_banco():
    """Confere integridade do banco recem-criado."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA integrity_check")
        total = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        conn.close()
        print(f"[OK] Banco integro com {total} usuarios.")
        return True
    except Exception as e:
        print(f"[ERRO] Banco com problema: {e}")
        return False


def verificar_logins():
    """Testa as credenciais de demonstracao."""
    for mod in list(sys.modules.keys()):
        if "database" in mod:
            del sys.modules[mod]

    import importlib
    db_mod = importlib.import_module("database")

    testes = [
        ("11999999999", "admin123",  "Administrador"),
        ("11999999991", "morador1",  "Morador demo"),
        ("11999999994", "vendedor1", "Vendedor demo"),
    ]

    conn = sqlite3.connect(DB_PATH)
    print()
    print("Verificando logins...")
    todos_ok = True
    for tel, senha, label in testes:
        row = conn.execute(
            "SELECT senha_hash FROM usuarios WHERE telefone=?", (tel,)
        ).fetchone()
        if not row:
            print(f"  [FALHOU] {label}: usuario {tel} nao encontrado")
            todos_ok = False
            continue
        if db_mod.verificar_senha(senha, row[0]):
            print(f"  [OK] {label:20s}  tel={tel}  senha={senha}")
        else:
            h = row[0][:10] if row[0] else "vazio"
            tipo = "bcrypt" if row[0] and row[0].startswith("$2") else "SHA-256 (incompativel)"
            print(f"  [FALHOU] {label}: hash={h}... tipo={tipo}")
            todos_ok = False
    conn.close()
    return todos_ok


if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  CondoMarket - Configuracao inicial")
    print("=" * 55)
    print()

    criar_env()
    carregar_env()
    recriar_banco()

    if not verificar_banco():
        print("[ERRO] Banco corrompido apos criacao. Verifique o disco.")
        sys.exit(1)

    tudo_ok = verificar_logins()

    print()
    print("=" * 55)
    if tudo_ok:
        print("  Configuracao concluida com sucesso!")
        print()
        print("  Inicie o servidor:")
        print("      python run.py")
        print()
        print("  Credenciais:")
        print("    Administrador : 11999999999 / admin123")
        print("    Morador demo  : 11999999991 / morador1")
        print("    Vendedor demo : 11999999994 / vendedor1")
    else:
        print("  ATENCAO: alguns logins falharam.")
        print("  Tente rodar novamente: python setup.py")
    print("=" * 55)
    print()
