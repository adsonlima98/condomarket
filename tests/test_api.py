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
        r = _importar_api(VERCEL="1", DATABASE_URL="postgresql://u:s@h/db", SECRET_KEY="x" * 64)
        assert r.returncode == 0, r.stderr[-400:]
