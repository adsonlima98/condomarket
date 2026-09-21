"""
Testes automatizados para a CondoMarket API
Execute com: pytest tests/ -v
"""
import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

# Configuracoes para ambiente de teste
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["BCRYPT_ROUNDS"] = "4"   # bcrypt rapido para testes
os.environ["PASSWORD_MIN_LEN"] = "4"
os.environ["SEED_DATA"] = "true"    # carrega dados de demo
os.environ["TESTING"] = "true"    # desabilita rate limiter

# Substitui o engine por banco em memoria antes de importar a api
import database as _db

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# SQLite ignora chaves estrangeiras por padrao; o PostgreSQL de producao nao.
# Ligar aqui faz os testes pegarem violacoes de FK antes do deploy.
@event.listens_for(_TEST_ENGINE, "connect")
def _ligar_foreign_keys(dbapi_conn, _record):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


_db.engine = _TEST_ENGINE
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

from api import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_db():
    """Recria o schema completo antes de cada teste."""
    _db.Base.metadata.drop_all(bind=_TEST_ENGINE)
    _db.Base.metadata.create_all(bind=_TEST_ENGINE)
    os.environ["ADMIN_PASSWORD"] = "admin123"
    _db.init_db()
    client.cookies.clear()  # evita vazamento de cookies entre testes
    yield


def get_token(telefone: str, senha: str) -> str:
    resp = client.post("/api/auth/login", json={"telefone": telefone, "senha": senha})
    assert resp.status_code == 200, f"Login falhou ({resp.status_code}): {resp.text}"
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:

    def test_login_valido_morador(self):
        resp = client.post("/api/auth/login", json={"telefone": "11999999991", "senha": "morador1"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["is_vendedor"] is False
        assert data["user"]["is_admin"] is False

    def test_login_valido_admin(self):
        resp = client.post("/api/auth/login", json={"telefone": "11999999999", "senha": "admin123"})
        assert resp.status_code == 200
        assert resp.json()["user"]["is_admin"] is True

    def test_login_invalido(self):
        resp = client.post("/api/auth/login", json={"telefone": "11999999991", "senha": "errada"})
        assert resp.status_code == 401

    def test_login_telefone_inexistente(self):
        resp = client.post("/api/auth/login", json={"telefone": "00000000000", "senha": "abc"})
        assert resp.status_code == 401

    def test_validate_token_valido(self):
        resp = client.post("/api/auth/validate-token", json={"token": "RECANTO2026"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert "Recanto" in data["condominio_nome"]

    def test_validate_token_invalido(self):
        resp = client.post("/api/auth/validate-token", json={"token": "TOKEN_ERRADO"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_validate_token_case_insensitive(self):
        resp = client.post("/api/auth/validate-token", json={"token": "recanto2026"})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_register_novo_usuario(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026",
            "nome": "Novo Morador",
            "telefone": "11988880000",
            "senha": "senha123",
            "bloco": "D",
            "apartamento": "401",
        })
        assert resp.status_code == 200
        assert "sucesso" in resp.json()["message"].lower()

    def test_register_token_invalido(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "INVALIDO",
            "nome": "Xy", "telefone": "11900000001",
            "senha": "senha123", "bloco": "A", "apartamento": "1",
        })
        assert resp.status_code == 400

    def test_register_telefone_duplicado(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026",
            "nome": "Copia",
            "telefone": "11999999991",  # ja existe
            "senha": "senha123", "bloco": "A", "apartamento": "1",
        })
        assert resp.status_code == 400

    def test_register_senha_curta(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026",
            "nome": "Teste",
            "telefone": "11900000099",
            "senha": "ab",  # abaixo do minimo
            "bloco": "A", "apartamento": "1",
        })
        assert resp.status_code == 422

    def test_register_telefone_invalido(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026",
            "nome": "Teste",
            "telefone": "abc_nao_e_numero",
            "senha": "senha123", "bloco": "A", "apartamento": "1",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------

class TestMarketplace:

    def test_listar_vendors_autenticado(self):
        token = get_token("11999999991", "morador1")
        resp = client.get("/api/marketplace/vendors", headers=auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2
        for v in data:
            assert "bloco" not in v
            assert "senha_hash" not in v

    def test_listar_vendors_sem_token(self):
        resp = client.get("/api/marketplace/vendors")
        assert resp.status_code == 401

    def test_listar_vendors_token_invalido(self):
        resp = client.get("/api/marketplace/vendors",
                          headers={"Authorization": "Bearer token_falso"})
        assert resp.status_code == 401

    def test_filtrar_vendors_por_busca(self):
        token = get_token("11999999991", "morador1")
        resp = client.get("/api/marketplace/vendors?search=Ana", headers=auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all("ana" in v["nome_negocio"].lower() or "ana" in v["descricao"].lower()
                   for v in data)

    def test_listar_categorias(self):
        token = get_token("11999999991", "morador1")
        resp = client.get("/api/marketplace/categories", headers=auth(token))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Vendor
# ---------------------------------------------------------------------------

class TestVendor:

    def test_get_perfil_existente(self):
        token = get_token("11999999994", "vendedor1")
        resp = client.get("/api/vendor/profile", headers=auth(token))
        assert resp.status_code == 200
        assert resp.json()["nome_negocio"] == "Doces da Ana"

    def test_get_perfil_sem_perfil(self):
        # Registra um vendedor mas nao cria perfil comercial
        client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026", "nome": "Vendedor Sem Perfil",
            "telefone": "11977770000", "senha": "senha123",
            "bloco": "F", "apartamento": "601", "is_vendedor": True,
        })
        token = get_token("11977770000", "senha123")
        resp = client.get("/api/vendor/profile", headers=auth(token))
        assert resp.status_code == 204

    def test_criar_perfil(self):
        client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026", "nome": "Novo Vendedor",
            "telefone": "11988880099", "senha": "senha123",
            "bloco": "E", "apartamento": "501", "is_vendedor": True,
        })
        token = get_token("11988880099", "senha123")
        resp = client.post("/api/vendor/profile", headers=auth(token), json={
            "nome_negocio": "Loja Teste", "categoria": "Alimentacao",
            "descricao": "Descricao teste", "whatsapp": "11988880099",
        })
        assert resp.status_code == 200
        assert "sucesso" in resp.json()["message"].lower()

    def test_criar_perfil_duplicado(self):
        token = get_token("11999999994", "vendedor1")
        resp = client.post("/api/vendor/profile", headers=auth(token), json={
            "nome_negocio": "Outro", "categoria": "Outros", "whatsapp": "11999999994",
        })
        assert resp.status_code == 400

    def test_atualizar_perfil(self):
        token = get_token("11999999994", "vendedor1")
        resp = client.put("/api/vendor/profile", headers=auth(token), json={
            "nome_negocio": "Doces da Ana - Atualizado",
            "categoria": "Alimentacao", "descricao": "Nova desc",
            "whatsapp": "11999999994",
        })
        assert resp.status_code == 200
        get_resp = client.get("/api/vendor/profile", headers=auth(token))
        assert get_resp.json()["nome_negocio"] == "Doces da Ana - Atualizado"


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class TestAdmin:

    def test_stats(self):
        token = get_token("11999999999", "admin123")
        resp = client.get("/api/admin/stats", headers=auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["condominios"] >= 1
        assert data["usuarios"] >= 1

    def test_stats_sem_permissao(self):
        token = get_token("11999999991", "morador1")
        resp = client.get("/api/admin/stats", headers=auth(token))
        assert resp.status_code == 403

    def test_listar_condominios(self):
        token = get_token("11999999999", "admin123")
        resp = client.get("/api/admin/condominiums", headers=auth(token))
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1
        assert len(resp.json()["items"]) >= 1

    def test_criar_condominio(self):
        token = get_token("11999999999", "admin123")
        resp = client.post("/api/admin/condominiums", headers=auth(token),
                           json={"nome": "Residencial Novo", "token_acesso": "NOVO2026"})
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_criar_condominio_token_duplicado(self):
        token = get_token("11999999999", "admin123")
        resp = client.post("/api/admin/condominiums", headers=auth(token),
                           json={"nome": "Outro", "token_acesso": "RECANTO2026"})
        assert resp.status_code == 400

    def test_listar_usuarios(self):
        token = get_token("11999999999", "admin123")
        resp = client.get("/api/admin/users", headers=auth(token))
        assert resp.status_code == 200
        assert resp.json()["total"] >= 6

    def test_deletar_usuario(self):
        token = get_token("11999999999", "admin123")
        users = client.get("/api/admin/users", headers=auth(token)).json()["items"]
        morador = next(u for u in users if not u["is_admin"] and not u["is_vendedor"])
        resp = client.delete(f"/api/admin/users/{morador['id']}", headers=auth(token))
        assert resp.status_code == 200

    def test_deletar_condominio_com_usuarios_falha(self):
        token = get_token("11999999999", "admin123")
        condos = client.get("/api/admin/condominiums", headers=auth(token)).json()["items"]
        resp = client.delete(f"/api/admin/condominiums/{condos[0]['id']}", headers=auth(token))
        assert resp.status_code == 400

    def test_deletar_condominio_vazio(self):
        token = get_token("11999999999", "admin123")
        create = client.post("/api/admin/condominiums", headers=auth(token),
                             json={"nome": "Para Deletar", "token_acesso": "DEL2026"})
        condo_id = create.json()["id"]
        resp = client.delete(f"/api/admin/condominiums/{condo_id}", headers=auth(token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_ok(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["database"] == "ok"


# ---------------------------------------------------------------------------
# Troca de senha
# ---------------------------------------------------------------------------

class TestChangePassword:

    def test_trocar_senha_valida(self):
        token = get_token("11999999991", "morador1")
        resp = client.post("/api/auth/change-password", headers=auth(token), json={
            "senha_atual": "morador1",
            "senha_nova": "novaSenha99",
        })
        assert resp.status_code == 200
        assert "sucesso" in resp.json()["message"].lower()
        # Confirma que nova senha funciona
        assert client.post("/api/auth/login",
            json={"telefone": "11999999991", "senha": "novaSenha99"}).status_code == 200

    def test_trocar_senha_atual_errada(self):
        token = get_token("11999999991", "morador1")
        resp = client.post("/api/auth/change-password", headers=auth(token), json={
            "senha_atual": "senhaErrada",
            "senha_nova": "novaSenha99",
        })
        assert resp.status_code == 400

    def test_trocar_senha_nova_curta(self):
        token = get_token("11999999991", "morador1")
        resp = client.post("/api/auth/change-password", headers=auth(token), json={
            "senha_atual": "morador1",
            "senha_nova": "ab",   # abaixo do minimo
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Paginacao
# ---------------------------------------------------------------------------

class TestPaginacao:

    def test_listar_condominios_paginado(self):
        token = get_token("11999999999", "admin123")
        resp = client.get("/api/admin/condominiums?skip=0&limit=5", headers=auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_listar_usuarios_paginado(self):
        token = get_token("11999999999", "admin123")
        resp = client.get("/api/admin/users?skip=0&limit=3", headers=auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 6
        assert len(data["items"]) <= 3

    def test_listar_usuarios_segunda_pagina(self):
        token = get_token("11999999999", "admin123")
        todos = client.get("/api/admin/users?skip=0&limit=100", headers=auth(token)).json()
        pag1  = client.get("/api/admin/users?skip=0&limit=3", headers=auth(token)).json()
        pag2  = client.get("/api/admin/users?skip=3&limit=3", headers=auth(token)).json()
        ids_pag1 = {u["id"] for u in pag1["items"]}
        ids_pag2 = {u["id"] for u in pag2["items"]}
        assert ids_pag1.isdisjoint(ids_pag2), "Paginas nao devem ter usuarios duplicados"


# ---------------------------------------------------------------------------
# Rotacao de token de condominio
# ---------------------------------------------------------------------------

class TestRotacaoToken:

    def test_rotacionar_token(self):
        token = get_token("11999999999", "admin123")
        condos = client.get("/api/admin/condominiums", headers=auth(token)).json()
        condo = condos["items"][0]
        token_antigo = condo["token_acesso"]

        resp = client.post(f"/api/admin/condominiums/{condo['id']}/rotate-token",
                           headers=auth(token))
        assert resp.status_code == 200
        token_novo = resp.json()["token_acesso"]
        assert token_novo != token_antigo

        # Token antigo nao deve mais funcionar
        val = client.post("/api/auth/validate-token", json={"token": token_antigo})
        assert val.json()["valid"] is False

        # Token novo deve funcionar
        val2 = client.post("/api/auth/validate-token", json={"token": token_novo})
        assert val2.json()["valid"] is True

    def test_rotacionar_token_sem_permissao(self):
        token = get_token("11999999991", "morador1")
        resp = client.post("/api/admin/condominiums/1/rotate-token", headers=auth(token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

class TestLogout:

    def test_logout(self):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Cardapio em PDF (guardado no banco — a Vercel nao tem disco persistente)
# ---------------------------------------------------------------------------

class TestCardapioPdf:

    PDF = b"%PDF-1.4\n% cardapio de teste\n"

    def _upload(self, token, conteudo=None, tipo="application/pdf"):
        arquivo = conteudo if conteudo is not None else self.PDF
        return client.post(
            "/api/vendor/profile/pdf",
            files={"file": ("cardapio.pdf", arquivo, tipo)},
            headers=auth(token),
        )

    def test_upload_e_download_do_proprio_pdf(self):
        token = get_token("11999999994", "vendedor1")
        assert self._upload(token).status_code == 200
        resp = client.get("/api/vendor/profile/pdf", headers=auth(token))
        assert resp.status_code == 200
        assert resp.content == self.PDF
        assert resp.headers["content-type"] == "application/pdf"

    def test_tem_pdf_reflete_upload_e_remocao(self):
        token = get_token("11999999994", "vendedor1")
        assert client.get("/api/vendor/profile", headers=auth(token)).json()["tem_pdf"] is False
        self._upload(token)
        assert client.get("/api/vendor/profile", headers=auth(token)).json()["tem_pdf"] is True
        assert client.delete("/api/vendor/profile/pdf", headers=auth(token)).status_code == 200
        assert client.get("/api/vendor/profile", headers=auth(token)).json()["tem_pdf"] is False

    def test_morador_do_mesmo_condominio_baixa_pdf_do_prestador(self):
        self._upload(get_token("11999999994", "vendedor1"))
        tm = get_token("11999999991", "morador1")
        vendors = client.get("/api/marketplace/vendors", headers=auth(tm)).json()
        ana = next(v for v in vendors if v["nome_negocio"] == "Doces da Ana")
        assert ana["tem_pdf"] is True
        resp = client.get(f"/api/marketplace/vendor/{ana['id']}/pdf", headers=auth(tm))
        assert resp.status_code == 200
        assert resp.content == self.PDF

    def test_prestador_sem_pdf_aparece_com_tem_pdf_falso(self):
        tm = get_token("11999999991", "morador1")
        vendors = client.get("/api/marketplace/vendors", headers=auth(tm)).json()
        assert vendors and all(v["tem_pdf"] is False for v in vendors)

    def test_novo_upload_substitui_o_anterior(self):
        token = get_token("11999999994", "vendedor1")
        self._upload(token, b"%PDF-1.4 primeiro")
        self._upload(token, b"%PDF-1.4 segundo")
        assert client.get("/api/vendor/profile/pdf", headers=auth(token)).content == b"%PDF-1.4 segundo"

    def test_pdf_acima_de_4mb_e_rejeitado(self):
        # A Vercel limita o corpo da requisicao a 4,5 MB; o limite do app fica abaixo disso.
        token = get_token("11999999994", "vendedor1")
        grande = b"%PDF" + b"0" * (4 * 1024 * 1024)
        assert self._upload(token, grande).status_code == 400
        assert client.get("/api/vendor/profile", headers=auth(token)).json()["tem_pdf"] is False

    def test_arquivo_que_nao_e_pdf_e_rejeitado(self):
        token = get_token("11999999994", "vendedor1")
        assert self._upload(token, b"nao sou pdf", tipo="text/plain").status_code == 400

    def test_admin_remove_prestador_que_tem_pdf(self):
        tv = get_token("11999999994", "vendedor1")
        self._upload(tv)
        vendor_id = client.get("/api/auth/me", headers=auth(tv)).json()["id"]
        ta = get_token("11999999999", "admin123")
        resp = client.delete(f"/api/admin/users/{vendor_id}", headers=auth(ta))
        assert resp.status_code == 200
        ids = [u["id"] for u in client.get("/api/admin/users", headers=auth(ta)).json()["items"]]
        assert vendor_id not in ids


# ---------------------------------------------------------------------------
# SPA — a rota curinga nunca pode servir arquivos do projeto
# ---------------------------------------------------------------------------

class TestSpa:

    def test_raiz_serve_o_index(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()

    def test_rota_do_frontend_serve_o_index(self):
        assert client.get("/marketplace").text == client.get("/").text

    @pytest.mark.parametrize("caminho", [
        "/api.py", "/database.py", "/.env", "/.env.example",
        "/marketplace.db", "/requirements.txt", "/tests/test_api.py",
    ])
    def test_nao_expoe_arquivos_do_projeto(self, caminho):
        # Antes da correcao, GET /api.py devolvia o codigo-fonte e GET /.env os segredos.
        assert client.get(caminho).text == client.get("/").text

    def test_rota_de_api_inexistente_devolve_404_e_nao_html(self):
        resp = client.get("/api/nao-existe")
        assert resp.status_code == 404
        assert "<html" not in resp.text.lower()


# ---------------------------------------------------------------------------
# Configuracao do banco (PostgreSQL em producao)
# ---------------------------------------------------------------------------

class TestUrlDoBanco:

    @pytest.mark.parametrize("entrada", [
        "postgres://u:s@host/db",       # formato legado (Heroku e afins)
        "postgresql://u:s@host/db",     # formato que o Neon entrega
    ])
    def test_postgres_usa_driver_psycopg3(self, entrada):
        assert _db._normalizar_url(entrada) == "postgresql+psycopg://u:s@host/db"

    def test_url_ja_normalizada_nao_muda(self):
        url = "postgresql+psycopg://u:s@host/db?sslmode=require"
        assert _db._normalizar_url(url) == url

    def test_sqlite_nao_muda(self):
        assert _db._normalizar_url("sqlite:///marketplace.db") == "sqlite:///marketplace.db"


# ---------------------------------------------------------------------------
# Variaveis de ambiente vazias (painel da Vercel permite criar sem valor)
# ---------------------------------------------------------------------------

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _importar_api(**env):
    """Importa a api num processo limpo, com as variaveis de ambiente dadas."""
    ambiente = {k: v for k, v in os.environ.items() if k not in ("VERCEL", "DATABASE_URL")}
    ambiente.update(env, PYTHONIOENCODING="utf-8")
    # utf-8 explicito: o caminho do projeto tem acento e o Windows decodificaria em cp1252
    return subprocess.run([sys.executable, "-c", "import api"], cwd=RAIZ, env=ambiente,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


class TestVariaveisDeAmbiente:

    def test_variavel_vazia_usa_o_padrao_em_vez_de_quebrar(self):
        # Em producao, BCRYPT_ROUNDS="" derrubava o app: int('') -> ValueError
        r = _importar_api(BCRYPT_ROUNDS="", PASSWORD_MIN_LEN="", ADMIN_PHONE="", DATABASE_URL="")
        assert r.returncode == 0, r.stderr[-400:]

    def test_na_vercel_sem_secret_key_falha_com_mensagem_clara(self):
        # Sem SECRET_KEY fixa, cada instancia serverless gera a sua e os usuarios
        # sao deslogados ao acaso. Melhor nao subir.
        r = _importar_api(VERCEL="1", DATABASE_URL="postgresql://u:s@h/db", SECRET_KEY="")
        assert r.returncode != 0
        assert "SECRET_KEY" in r.stderr

    def test_na_vercel_com_tudo_definido_sobe(self):
        r = _importar_api(VERCEL="1", DATABASE_URL="postgresql://u:s@h/db", SECRET_KEY="x" * 64,
                          ALLOWED_ORIGINS="https://exemplo.com")
        assert r.returncode == 0, r.stderr[-400:]

    def test_na_vercel_sem_allowed_origins_falha(self):
        # Auditoria 2026-09-21: producao no ar com Access-Control-Allow-Origin: *
        # porque so havia um warning no log. Agora o deploy quebra.
        r = _importar_api(VERCEL="1", DATABASE_URL="postgresql://u:s@h/db", SECRET_KEY="x" * 64,
                          ALLOWED_ORIGINS="")
        assert r.returncode != 0
        assert "ALLOWED_ORIGINS" in r.stderr

    def test_na_vercel_com_allowed_origins_curinga_falha(self):
        r = _importar_api(VERCEL="1", DATABASE_URL="postgresql://u:s@h/db", SECRET_KEY="x" * 64,
                          ALLOWED_ORIGINS="*")
        assert r.returncode != 0
        assert "ALLOWED_ORIGINS" in r.stderr

    def test_fora_da_vercel_sem_allowed_origins_ainda_sobe(self):
        """Local continua funcionando sem configurar nada."""
        r = _importar_api(ALLOWED_ORIGINS="")
        assert r.returncode == 0, r.stderr[-400:]


class TestSenhaComVariavelVazia:
    """Em producao PASSWORD_MIN_LEN existia VAZIA no painel: os validadores de senha
    faziam int('') a cada requisicao e cadastro/troca de senha davam 422 para todos."""

    @pytest.fixture(autouse=True)
    def _min_len_vazio(self, monkeypatch):
        monkeypatch.setenv("PASSWORD_MIN_LEN", "")

    def test_cadastro_funciona(self):
        resp = client.post("/api/auth/register", json={
            "token_condominio": "RECANTO2026", "nome": "Novo Morador",
            "telefone": "11988887777", "senha": "senha-bem-longa",
            "bloco": "A", "apartamento": "1",
        })
        assert resp.status_code == 200, resp.text

    def test_troca_de_senha_funciona(self):
        token = get_token("11999999991", "morador1")
        resp = client.post("/api/auth/change-password", headers=auth(token),
                           json={"senha_atual": "morador1", "senha_nova": "nova-senha-longa"})
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Admin: edicao de usuarios e condominios
# ---------------------------------------------------------------------------

class TestAdminEdicaoUsuario:
    """PUT /api/admin/users/{id} - o admin corrige cadastros errados."""

    def _listar(self, token):
        return client.get("/api/admin/users", headers=auth(token)).json()["items"]

    def _morador(self, token):
        return next(u for u in self._listar(token) if not u["is_admin"] and not u["is_vendedor"])

    def _prestador(self, token):
        return next(u for u in self._listar(token) if u["is_vendedor"])

    def _corpo(self, token, u, **mudancas):
        """Monta o corpo completo a partir de um usuario ja listado."""
        condos = client.get("/api/admin/condominiums", headers=auth(token)).json()["items"]
        condo_id = next(c["id"] for c in condos if c["nome"] == u["condominio"])
        corpo = {"nome": u["nome"], "telefone": u["telefone"], "bloco": u["bloco"],
                 "apartamento": u["apartamento"], "condominio_id": condo_id,
                 "is_vendedor": u["is_vendedor"]}
        corpo.update(mudancas)
        return corpo

    def test_editar_dados_basicos(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, nome="Joao da Silva Corrigido",
                                           telefone="11900000001", bloco="D", apartamento="909"))
        assert resp.status_code == 200, resp.text

        editado = next(x for x in self._listar(token) if x["id"] == u["id"])
        assert editado["nome"] == "Joao da Silva Corrigido"
        assert editado["telefone"] == "11900000001"
        assert editado["bloco"] == "D"
        assert editado["apartamento"] == "909"

    def test_telefone_novo_serve_para_login(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                   json=self._corpo(token, u, telefone="11900000002"))
        client.cookies.clear()
        resp = client.post("/api/auth/login", json={"telefone": "11900000002", "senha": "morador1"})
        assert resp.status_code == 200

    def test_telefone_duplicado_recusado(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, telefone="11999999992"))
        assert resp.status_code == 400

    def test_manter_o_proprio_telefone_e_permitido(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, nome="So Trocou o Nome"))
        assert resp.status_code == 200

    def test_usuario_inexistente(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put("/api/admin/users/999999", headers=auth(token),
                          json=self._corpo(token, u))
        assert resp.status_code == 404

    def test_nao_edita_outro_admin(self):
        token = get_token("11999999999", "admin123")
        adm = next(x for x in self._listar(token) if x["is_admin"])
        resp = client.put(f"/api/admin/users/{adm['id']}", headers=auth(token),
                          json=self._corpo(token, adm, nome="Tentativa"))
        assert resp.status_code == 400

    def test_morador_nao_pode_editar(self):
        admin_token = get_token("11999999999", "admin123")
        u = self._morador(admin_token)
        corpo = self._corpo(admin_token, u, nome="Invasao")
        client.cookies.clear()
        token = get_token("11999999991", "morador1")
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token), json=corpo)
        assert resp.status_code == 403

    def test_condominio_inexistente_recusado(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, condominio_id=999999))
        assert resp.status_code == 400

    def test_mudar_de_condominio(self):
        token = get_token("11999999999", "admin123")
        novo = client.post("/api/admin/condominiums", headers=auth(token),
                           json={"nome": "Vila Nova", "token_acesso": "VILA2026"}).json()
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, condominio_id=novo["id"]))
        assert resp.status_code == 200
        assert next(x for x in self._listar(token) if x["id"] == u["id"])["condominio"] == "Vila Nova"

    def test_prestador_vira_morador_apaga_perfil_e_pdf(self):
        token = get_token("11999999999", "admin123")
        p = next(u for u in self._listar(token) if u["telefone"] == "11999999994")
        corpo = self._corpo(token, p, is_vendedor=False)

        # Garante que ele tem PDF antes da conversao
        client.cookies.clear()
        vend_token = get_token("11999999994", "vendedor1")
        envio = client.post("/api/vendor/profile/pdf", headers=auth(vend_token),
                            files={"file": ("cardapio.pdf", b"%PDF-1.4 teste", "application/pdf")})
        assert envio.status_code == 200, envio.text

        client.cookies.clear()
        token = get_token("11999999999", "admin123")
        resp = client.put(f"/api/admin/users/{p['id']}", headers=auth(token), json=corpo)
        assert resp.status_code == 200, resp.text

        session = _db.SessionLocal()
        try:
            assert session.query(_db.PerfilComercial).filter_by(usuario_id=p["id"]).first() is None
            assert session.query(_db.CardapioPdf).filter_by(usuario_id=p["id"]).first() is None
        finally:
            session.close()

    def test_morador_vira_prestador(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, is_vendedor=True))
        assert resp.status_code == 200
        assert next(x for x in self._listar(token) if x["id"] == u["id"])["is_vendedor"] is True

    def test_redefinir_senha_permite_login(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, senha_nova="senha-nova-123"))
        assert resp.status_code == 200

        client.cookies.clear()
        ok = client.post("/api/auth/login",
                         json={"telefone": u["telefone"], "senha": "senha-nova-123"})
        assert ok.status_code == 200
        client.cookies.clear()
        velha = client.post("/api/auth/login",
                            json={"telefone": u["telefone"], "senha": "morador1"})
        assert velha.status_code == 401

    def test_senha_curta_recusada(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        # PASSWORD_MIN_LEN=4 nos testes
        resp = client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                          json=self._corpo(token, u, senha_nova="x"))
        assert resp.status_code == 422

    def test_sem_senha_nova_mantem_a_antiga(self):
        token = get_token("11999999999", "admin123")
        u = self._morador(token)
        client.put(f"/api/admin/users/{u['id']}", headers=auth(token),
                   json=self._corpo(token, u, nome="Outro Nome"))
        client.cookies.clear()
        resp = client.post("/api/auth/login", json={"telefone": u["telefone"], "senha": "morador1"})
        assert resp.status_code == 200


class TestAdminEdicaoCondominio:
    """PUT /api/admin/condominiums/{id}"""

    def _listar(self, token):
        return client.get("/api/admin/condominiums", headers=auth(token)).json()["items"]

    def _condo(self, token, nome="Recanto das Flores"):
        return next(c for c in self._listar(token) if c["nome"] == nome)

    def test_editar_nome_e_token(self):
        token = get_token("11999999999", "admin123")
        c = self._condo(token)
        resp = client.put(f"/api/admin/condominiums/{c['id']}", headers=auth(token),
                          json={"nome": "Recanto das Flores II", "token_acesso": "recanto2027"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["token_acesso"] == "RECANTO2027"  # normalizado para maiusculo

        editado = next(x for x in self._listar(token) if x["id"] == c["id"])
        assert editado["nome"] == "Recanto das Flores II"
        assert editado["token_acesso"] == "RECANTO2027"

    def test_token_antigo_para_de_valer_no_cadastro(self):
        token = get_token("11999999999", "admin123")
        c = self._condo(token)
        client.put(f"/api/admin/condominiums/{c['id']}", headers=auth(token),
                   json={"nome": c["nome"], "token_acesso": "TROCADO2026"})
        antigo = client.post("/api/auth/validate-token", json={"token": "RECANTO2026"})
        novo = client.post("/api/auth/validate-token", json={"token": "TROCADO2026"})
        assert antigo.json()["valid"] is False
        assert novo.json()["valid"] is True

    def test_manter_o_proprio_token_e_permitido(self):
        token = get_token("11999999999", "admin123")
        c = self._condo(token)
        resp = client.put(f"/api/admin/condominiums/{c['id']}", headers=auth(token),
                          json={"nome": "Nome Novo", "token_acesso": c["token_acesso"]})
        assert resp.status_code == 200

    def test_token_de_outro_condominio_recusado(self):
        token = get_token("11999999999", "admin123")
        client.post("/api/admin/condominiums", headers=auth(token),
                    json={"nome": "Outro", "token_acesso": "OUTRO2026"})
        c = self._condo(token)
        resp = client.put(f"/api/admin/condominiums/{c['id']}", headers=auth(token),
                          json={"nome": c["nome"], "token_acesso": "OUTRO2026"})
        assert resp.status_code == 400

    def test_condominio_inexistente(self):
        token = get_token("11999999999", "admin123")
        resp = client.put("/api/admin/condominiums/999999", headers=auth(token),
                          json={"nome": "X", "token_acesso": "X2026"})
        assert resp.status_code == 404

    def test_morador_nao_pode_editar(self):
        admin_token = get_token("11999999999", "admin123")
        c = self._condo(admin_token)
        client.cookies.clear()
        token = get_token("11999999991", "morador1")
        resp = client.put(f"/api/admin/condominiums/{c['id']}", headers=auth(token),
                          json={"nome": "Invadido", "token_acesso": "HACK2026"})
        assert resp.status_code == 403

    def test_erro_de_exclusao_informa_quantos_usuarios(self):
        token = get_token("11999999999", "admin123")
        c = self._condo(token)
        resp = client.delete(f"/api/admin/condominiums/{c['id']}", headers=auth(token))
        assert resp.status_code == 400
        assert "5" in resp.json()["detail"]  # os 5 usuarios demo do Recanto


class TestHeadersDeSeguranca:
    """Auditoria de 2026-09-21: producao respondia sem nenhum header de seguranca."""

    ESPERADOS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    def test_headers_presentes_no_html(self):
        r = client.get("/")
        for nome, valor in self.ESPERADOS.items():
            assert r.headers.get(nome) == valor, f"{nome} ausente ou diferente"
        assert "Content-Security-Policy" in r.headers
        assert "Permissions-Policy" in r.headers

    def test_headers_presentes_na_api(self):
        r = client.get("/api/health")
        for nome, valor in self.ESPERADOS.items():
            assert r.headers.get(nome) == valor

    def test_headers_presentes_em_resposta_de_erro(self):
        """401 tambem passa pelo middleware — erro nao pode escapar sem header."""
        r = client.get("/api/auth/me")
        assert r.status_code == 401
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_csp_bloqueia_iframe_e_recurso_externo(self):
        csp = client.get("/").headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp   # clickjacking
        assert "default-src 'self'" in csp       # nada de dominio externo
        assert "object-src 'none'" in csp

    def test_csp_permite_o_que_o_index_html_usa(self):
        """CSP restritiva demais quebraria o app: ha <script>/<style> inline,
        27 onclick= e o download do PDF via blob:."""
        csp = client.get("/").headers["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "style-src 'self' 'unsafe-inline'" in csp
        assert "blob:" in csp
