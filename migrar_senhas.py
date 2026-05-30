"""
Script de migracao: converte senhas SHA-256 para bcrypt. (APENAS DESENVOLVIMENTO)
Execute UMA VEZ antes de iniciar o servidor apos a atualizacao de seguranca.

ATENCAO: As senhas listadas neste script (admin123, morador1, etc.) sao credenciais
         de demonstracao publicas. Nao use em producao. Em producao, defina
         ADMIN_PASSWORD via variavel de ambiente e mantenha SEED_DATA=false.

Uso:
    python migrar_senhas.py

Ou para recriar o banco do zero (apaga tudo e reinicia):
    python migrar_senhas.py --recriar
"""
import sys
import os

def migrar():
    """Converte os hashes SHA-256 existentes para bcrypt."""
    import sqlite3
    import bcrypt

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "marketplace.db")
    if not os.path.exists(db_path):
        print("marketplace.db nao encontrado. Execute o servidor uma vez para criar o banco.")
        return

    # Senhas dos usuarios de demonstracao (criados automaticamente pelo init_db)
    credenciais_demo = {
        "11999999991": "morador1",
        "11999999992": "morador2",
        "11999999993": "morador3",
        "11999999994": "vendedor1",
        "11999999995": "vendedor2",
        "11999999999": "admin123",
    }

    rounds = int(os.environ.get("BCRYPT_ROUNDS", "12"))
    conn = sqlite3.connect(db_path)
    migrados = 0

    for tel, senha in credenciais_demo.items():
        # Verifica se ja e bcrypt (comeca com $2b$)
        row = conn.execute("SELECT senha_hash FROM usuarios WHERE telefone=?", (tel,)).fetchone()
        if not row:
            continue
        if row[0].startswith("$2b$"):
            print(f"  {tel}: ja e bcrypt, ignorando.")
            continue
        novo_hash = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")
        conn.execute("UPDATE usuarios SET senha_hash=? WHERE telefone=?", (novo_hash, tel))
        migrados += 1
        print(f"  {tel} ({senha}): migrado para bcrypt OK")

    conn.commit()
    conn.close()
    print(f"\n{migrados} senha(s) migrada(s).")
    if migrados == 0:
        print("Nada a migrar (banco ja esta atualizado).")


def recriar():
    """Apaga o banco e recria com bcrypt e dados de demonstracao."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "marketplace.db")
    if os.path.exists(db_path):
        os.remove(db_path)
        print("marketplace.db removido.")

    os.environ["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "admin123")
    os.environ["SEED_DATA"] = "true"
    os.environ.setdefault("BCRYPT_ROUNDS", "12")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import init_db
    init_db()
    print("Banco recriado com sucesso!")
    print(f"Admin: telefone=11999999999 / senha={os.environ['ADMIN_PASSWORD']}")
    print("Morador demo: telefone=11999999991 / senha=morador1")
    print("Vendedor demo: telefone=11999999994 / senha=vendedor1")


if __name__ == "__main__":
    if "--recriar" in sys.argv:
        recriar()
    else:
        migrar()
