"""
Testes automatizados para a CondoMarket API
Execute com: pytest tests/ -v
"""
import os
import pytest
from sqlalchemy import create_engine
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
