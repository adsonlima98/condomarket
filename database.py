import os
import bcrypt
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, LargeBinary
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, deferred
from sqlalchemy.pool import NullPool

def _env(nome: str, padrao: str = None) -> str:
    """Le uma variavel de ambiente tratando valor VAZIO como ausente.

    O painel da Vercel permite criar variavel sem valor; os.environ.get(nome, padrao)
    so usa o padrao quando a variavel nao existe, e o '' derrubava o app
    (ex.: int('') em BCRYPT_ROUNDS).
    """
    valor = os.environ.get(nome, "").strip()
    return valor if valor else padrao


# ── Banco de dados ──────────────────────────────────────────────────────────
# Producao (Vercel): DATABASE_URL=postgresql://... — injetada pela integracao Neon.
# Desenvolvimento: sem DATABASE_URL, usa o SQLite local marketplace.db.
DATABASE_URL = _env("DATABASE_URL", "sqlite:///marketplace.db")

# Na Vercel o sistema de arquivos e somente leitura e nao persiste entre
# execucoes: um SQLite ali perderia os dados (ou nem abriria). Falha cedo e claro.
if os.environ.get("VERCEL") and DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "DATABASE_URL nao definida. Na Vercel o banco precisa ser PostgreSQL: "
        "conecte um banco Neon ao projeto em Storage (a variavel e criada sozinha)."
    )


def _normalizar_url(url: str) -> str:
    """Usa o driver psycopg 3 para qualquer URL PostgreSQL (inclusive o legado postgres://)."""
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo):]
    return url


DATABASE_URL = _normalizar_url(DATABASE_URL)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Serverless: cada instancia da funcao e efemera, entao o pool de conexoes
    # fica no pooler do provedor (Neon/PgBouncer), nao no processo — NullPool.
    # prepare_threshold=None desliga prepared statements automaticos do psycopg,
    # que quebram atras de PgBouncer em modo transacao.
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={"prepare_threshold": None},
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Fator de custo do bcrypt (12 em producao, 4 em testes para velocidade)
BCRYPT_ROUNDS = int(_env("BCRYPT_ROUNDS", "12"))


def hash_senha(senha: str) -> str:
    """Gera hash bcrypt da senha. Use SEMPRE esta funcao."""
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Verifica senha contra hash bcrypt."""
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8"))
    except Exception:
        return False


class Condominio(Base):
    __tablename__ = "condominios"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    token_acesso = Column(String, nullable=False, unique=True)
    usuarios = relationship("Usuario", back_populates="condominio")


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, nullable=False)
    bloco = deferred(Column(String, nullable=False))
    apartamento = deferred(Column(String, nullable=False))
    telefone = Column(String, nullable=False, unique=True)
    senha_hash = Column(String, nullable=False)
    condominio_id = Column(Integer, ForeignKey("condominios.id"), nullable=False)
    is_vendedor = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    condominio = relationship("Condominio", back_populates="usuarios")
    perfil_comercial = relationship("PerfilComercial", back_populates="usuario", uselist=False)


class PerfilComercial(Base):
    __tablename__ = "perfis_comerciais"
    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=False)
    nome_negocio = Column(String, nullable=False)
    descricao = Column(String, nullable=True)
    categoria = Column(String, nullable=False)
    whatsapp = Column(String, nullable=False)
    pdf_cardapio = Column(String, nullable=True)
    usuario = relationship("Usuario", back_populates="perfil_comercial")


class CardapioPdf(Base):
    """Cardapio em PDF do prestador, guardado no proprio banco.

    Fica no banco (e nao em disco) porque a Vercel nao tem disco persistente.
    Tabela separada para que listar prestadores nunca carregue os bytes do PDF.
    """
    __tablename__ = "cardapios_pdf"
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    conteudo = deferred(Column(LargeBinary, nullable=False))


def get_session():
    return SessionLocal()


def validar_token(token: str) -> bool:
    session = get_session()
    try:
        condominio = session.query(Condominio).filter(
            Condominio.token_acesso == token.strip().upper()
        ).first()
        return condominio is not None
    finally:
        session.close()


def buscar_vendedores_por_condominio(condominio_id: int) -> list:
    session = get_session()
    try:
        from sqlalchemy.orm import joinedload
        perfis = (
            session.query(PerfilComercial)
            .join(Usuario, PerfilComercial.usuario_id == Usuario.id)
            .filter(Usuario.condominio_id == condominio_id)
            .options(joinedload(PerfilComercial.usuario))
            .all()
        )
        for perfil in perfis:
            session.expunge(perfil)
            if perfil.usuario:
                session.expunge(perfil.usuario)
                perfil.usuario.bloco = "CONFIDENCIAL"
                perfil.usuario.apartamento = "CONFIDENCIAL"
        return perfis
    finally:
        session.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        # ── Admin ──────────────────────────────────────────────────────────
        # Senha do admin vem de variavel de ambiente ADMIN_PASSWORD
        # Se nao definida, gera senha aleatoria e imprime UMA VEZ no console
        admin_pwd = _env("ADMIN_PASSWORD")
        admin_tel = _env("ADMIN_PHONE", "11999999999")

        admin_existente = session.query(Usuario).filter_by(telefone=admin_tel).first()
        if not admin_existente:
            if not admin_pwd:
                import secrets
                admin_pwd = secrets.token_urlsafe(16)
                print("=" * 60)
                print("ATENCAO: ADMIN_PASSWORD nao definida.")
                print(f"Senha gerada automaticamente: {admin_pwd}")
                print(f"Telefone admin: {admin_tel}")
                print("Salve esta senha! Ela nao sera exibida novamente.")
                print("=" * 60)

            # Condominio padrao para o admin (necessario pelo FK)
            cond_admin = session.query(Condominio).first()
            if not cond_admin:
                import secrets
                token_inicial = secrets.token_urlsafe(8).upper()
                cond_admin = Condominio(nome="Condominio Principal", token_acesso=token_inicial)
                session.add(cond_admin)
                session.commit()
                session.refresh(cond_admin)
                print(f"Condominio inicial criado com token: {token_inicial}")

            admin = Usuario(
                nome="Administrador Geral",
                telefone=admin_tel,
                senha_hash=hash_senha(admin_pwd),
                is_admin=True,
                is_vendedor=False,
                bloco="ADM",
                apartamento="0",
                condominio_id=cond_admin.id,
            )
            session.add(admin)
            session.commit()

        # ── Dados de demonstracao (somente se SEED_DATA=true) ──────────────
        if _env("SEED_DATA", "false").lower() == "true":
            _seed_demo_data(session)

    finally:
        session.close()


def _seed_demo_data(session):
    """Insere dados de demonstracao. Ative com SEED_DATA=true."""
    cond = session.query(Condominio).filter(Condominio.token_acesso == "RECANTO2026").first()
    if not cond:
        cond = Condominio(nome="Recanto das Flores", token_acesso="RECANTO2026")
        session.add(cond)
        session.commit()
        session.refresh(cond)

        usuarios_demo = [
            ("Joao Silva",      "A", "101", "11999999991", hash_senha("morador1"),  False),
            ("Maria Souza",     "B", "202", "11999999992", hash_senha("morador2"),  False),
            ("Carlos Oliveira", "C", "303", "11999999993", hash_senha("morador3"),  False),
            ("Ana Doces",       "A", "404", "11999999994", hash_senha("vendedor1"), True),
            ("Pedro Hamburguer","B", "505", "11999999995", hash_senha("vendedor2"), True),
        ]
        objs = []
        for nome, bloco, apto, tel, pwd_hash, vend in usuarios_demo:
            if not session.query(Usuario).filter_by(telefone=tel).first():
                objs.append(Usuario(nome=nome, bloco=bloco, apartamento=apto,
                    telefone=tel, senha_hash=pwd_hash,
                    condominio_id=cond.id, is_vendedor=vend))
        session.add_all(objs)
        session.commit()

        v1 = session.query(Usuario).filter_by(telefone="11999999994").first()
        v2 = session.query(Usuario).filter_by(telefone="11999999995").first()
        if v1 and not session.query(PerfilComercial).filter_by(usuario_id=v1.id).first():
            session.add(PerfilComercial(usuario_id=v1.id, nome_negocio="Doces da Ana",
                descricao="Bolos e doces artesanais sob encomenda.",
                categoria="Alimentacao", whatsapp="11999999994"))
        if v2 and not session.query(PerfilComercial).filter_by(usuario_id=v2.id).first():
            session.add(PerfilComercial(usuario_id=v2.id, nome_negocio="Burguer do Pedro",
                descricao="Hamburgueres artesanais na brasa.",
                categoria="Alimentacao", whatsapp="11999999995"))
        session.commit()


if __name__ == "__main__":
    init_db()
