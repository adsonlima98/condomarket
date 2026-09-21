"""
CondoMarket - FastAPI backend
"""
import os
import secrets
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import undefer
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import (
    SessionLocal, init_db,
    Condominio, Usuario, PerfilComercial, CardapioPdf,
    buscar_vendedores_por_condominio,
    hash_senha, verificar_senha, _env,
)

from jose import jwt
from datetime import datetime, timedelta

# ── Seguranca ─────────────────────────────────────────────────────────────────
SECRET_KEY = _env("SECRET_KEY", "")
# Na Vercel cada instancia da funcao geraria a sua propria chave aleatoria: um token
# emitido por uma instancia seria recusado por outra, e os usuarios seriam deslogados
# ao acaso. Melhor nao subir.
if os.environ.get("VERCEL") and not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY nao definida (ou vazia). Na Vercel defina em Settings > Environment "
        "Variables um valor longo e aleatorio: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    warnings.warn(
        "SECRET_KEY nao definida via variavel de ambiente. "
        "Uma chave temporaria foi gerada — todos os tokens JWT serao invalidados ao reiniciar. "
        "Defina SECRET_KEY=<chave-segura> antes de ir para producao.",
        stacklevel=2,
    )

ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 1
# A Vercel limita o corpo de requisicao e de resposta de uma funcao a 4,5 MB.
# O upload e o download do PDF passam inteiros pela funcao, entao o teto fica abaixo.
PDF_MAX_SIZE_BYTES = 4 * 1024 * 1024
PASSWORD_MIN_LEN = int(_env("PASSWORD_MIN_LEN", "8"))
COOKIE_NAME = "cm_auth"
IS_TESTING = os.environ.get("TESTING") == "true"
INDEX_HTML = Path(__file__).parent / "index.html"

# ── Rate limiter ──────────────────────────────────────────────────────────────
if IS_TESTING:
    from unittest.mock import MagicMock
    limiter = MagicMock()
    limiter.limit = lambda *a, **kw: (lambda f: f)
else:
    limiter = Limiter(key_func=get_remote_address)


# ── JWT helpers ───────────────────────────────────────────────────────────────
def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(days=TOKEN_TTL_DAYS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> int:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    return int(payload["sub"])


def get_current_user_id(
    authorization: Optional[str] = Header(default=None),
    cm_auth: Optional[str] = Cookie(default=None),
) -> int:
    """Aceita JWT via header Authorization: Bearer <token> OU via cookie httpOnly cm_auth."""
    token = None
    if authorization:
        try:
            scheme, tok = authorization.split()
            if scheme.lower() == "bearer":
                token = tok
        except Exception:
            pass
    if token is None and cm_auth:
        token = cm_auth
    if token is None:
        raise HTTPException(status_code=401, detail="Autenticacao necessaria")
    try:
        return _decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalido ou expirado")


def require_admin(user_id: int = Depends(get_current_user_id)) -> int:
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_admin:
            raise HTTPException(status_code=403, detail="Acesso negado")
        return user_id
    finally:
        session.close()


def _set_auth_cookie(response: Response, token: str) -> None:
    """Define cookie httpOnly — o token nunca fica exposto ao JavaScript."""
    secure = not IS_TESTING  # apenas HTTPS em producao
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=TOKEN_TTL_DAYS * 86400,
        path="/",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="CondoMarket", lifespan=lifespan)

# ── CORS ──────────────────────────────────────────────────────────────────────
_raw_origins = _env("ALLOWED_ORIGINS", "")
# Um aviso no log nao impediu que a producao subisse com CORS liberado para qualquer
# origem (auditoria de 2026-09-21). Mesmo tratamento dado a SECRET_KEY: na Vercel, falha
# rapida — e melhor o deploy quebrar do que servir o app com a origem aberta.
if os.environ.get("VERCEL") and (not _raw_origins or _raw_origins.strip() == "*"):
    raise RuntimeError(
        "ALLOWED_ORIGINS nao definida (ou igual a '*'). Na Vercel defina em Settings > "
        "Environment Variables a origem do site, ex.: "
        "ALLOWED_ORIGINS=https://condomarket-three.vercel.app"
    )
if not _raw_origins or _raw_origins.strip() == "*":
    warnings.warn(
        "ALLOWED_ORIGINS nao definida ou configurada como '*'. "
        "Defina ALLOWED_ORIGINS=https://seudominio.com antes de ir para producao.",
        stacklevel=2,
    )
ALLOWED_ORIGINS = _raw_origins.split(",") if _raw_origins and _raw_origins.strip() != "*" else ["*"]
# allow_credentials exige origens explicitas (nao pode ser "*")
_credentials_ok = ALLOWED_ORIGINS != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=_credentials_ok,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not IS_TESTING:
    app.state.limiter = limiter

    def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        """Igual ao handler do slowapi, mas informando quando tentar de novo."""
        resp = _rate_limit_exceeded_handler(request, exc)
        # "10 per 1 minute" -> a janela e de 1 minuto; e o unico intervalo em uso aqui.
        resp.headers["Retry-After"] = "60"
        return resp

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


# ── Headers de seguranca ──────────────────────────────────────────────────────
# O app nao carrega nenhum recurso externo (sem CDN, sem Google Fonts), entao
# 'self' basta. 'unsafe-inline' e obrigatorio enquanto o index.html tiver o bloco
# <script>/<style> embutido e os 27 onclick= no HTML.
# ponytail: CSP com 'unsafe-inline' nao barra XSS inline — barra script externo,
# clickjacking e exfiltracao para outro dominio. Para barrar inline tambem, trocar
# os onclick= por addEventListener e migrar para CSP com nonce.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",           # frame-ancestors para navegador antigo
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}


@app.middleware("http")
async def adicionar_headers_de_seguranca(request: Request, call_next):
    response = await call_next(request)
    for nome, valor in _SECURITY_HEADERS.items():
        response.headers.setdefault(nome, valor)
    return response


# ── Modelos Pydantic ──────────────────────────────────────────────────────────
class LoginBody(BaseModel):
    telefone: str
    senha: str

    @field_validator("telefone")
    @classmethod
    def telefone_valido(cls, v):
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 20):
            raise ValueError("Telefone deve conter apenas digitos (8-20 caracteres)")
        return v

    @field_validator("senha")
    @classmethod
    def senha_nao_vazia(cls, v):
        if not v:
            raise ValueError("Senha nao pode ser vazia")
        return v


class RegisterBody(BaseModel):
    nome: str
    telefone: str
    senha: str
    token_condominio: str
    bloco: Optional[str] = None
    apartamento: Optional[str] = None
    is_vendedor: bool = False

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v):
        v = v.strip()
        if len(v) < 2 or len(v) > 100:
            raise ValueError("Nome deve ter entre 2 e 100 caracteres")
        return v

    @field_validator("telefone")
    @classmethod
    def telefone_valido(cls, v):
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 20):
            raise ValueError("Telefone deve conter apenas digitos (8-20 caracteres)")
        return v

    @field_validator("senha")
    @classmethod
    def senha_forte(cls, v):
        min_len = int(_env("PASSWORD_MIN_LEN", "8"))
        if len(v) < min_len:
            raise ValueError(f"Senha deve ter no minimo {min_len} caracteres")
        return v

    @field_validator("token_condominio")
    @classmethod
    def token_valido(cls, v):
        v = v.strip().upper()
        if not v:
            raise ValueError("Token do condominio nao pode ser vazio")
        return v

    @field_validator("bloco", "apartamento")
    @classmethod
    def campo_curto(cls, v):
        if v and len(v) > 20:
            raise ValueError("Campo muito longo (max 20 caracteres)")
        return v


class VendorProfileBody(BaseModel):
    nome_negocio: str
    descricao: Optional[str] = ""
    categoria: str
    whatsapp: str

    @field_validator("nome_negocio", "categoria")
    @classmethod
    def nao_vazio(cls, v):
        if not v or not v.strip():
            raise ValueError("Campo obrigatorio")
        if len(v) > 100:
            raise ValueError("Campo muito longo (max 100 caracteres)")
        return v.strip()

    @field_validator("descricao")
    @classmethod
    def descricao_tamanho(cls, v):
        if v and len(v) > 500:
            raise ValueError("Descricao muito longa (max 500 caracteres)")
        return v or ""

    @field_validator("whatsapp")
    @classmethod
    def whatsapp_valido(cls, v):
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 20):
            raise ValueError("WhatsApp deve conter apenas digitos (8-20 caracteres)")
        return v


class CondominiumBody(BaseModel):
    nome: str
    token_acesso: str

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v):
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError("Nome invalido (1-100 caracteres)")
        return v

    @field_validator("token_acesso")
    @classmethod
    def token_valido(cls, v):
        v = v.strip().upper()
        if not v or len(v) > 30:
            raise ValueError("Token invalido (1-30 caracteres)")
        return v


class ValidateTokenBody(BaseModel):
    token: str


class ChangePasswordBody(BaseModel):
    senha_atual: str
    senha_nova: str

    @field_validator("senha_nova")
    @classmethod
    def senha_forte(cls, v):
        min_len = int(_env("PASSWORD_MIN_LEN", "8"))
        if len(v) < min_len:
            raise ValueError(f"Nova senha deve ter no minimo {min_len} caracteres")
        return v


class AdminProfileBody(BaseModel):
    nome: str
    telefone: str
    senha_atual: Optional[str] = None
    senha_nova: Optional[str] = None

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v):
        v = v.strip()
        if len(v) < 2 or len(v) > 100:
            raise ValueError("Nome deve ter entre 2 e 100 caracteres")
        return v

    @field_validator("telefone")
    @classmethod
    def telefone_valido(cls, v):
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 20):
            raise ValueError("Telefone deve conter apenas digitos (8-20 caracteres)")
        return v

    @field_validator("senha_nova")
    @classmethod
    def senha_forte(cls, v):
        if v is None:
            return v
        min_len = int(_env("PASSWORD_MIN_LEN", "8"))
        if len(v) < min_len:
            raise ValueError(f"Nova senha deve ter no minimo {min_len} caracteres")
        return v


class AdminUserUpdateBody(BaseModel):
    """Edicao de um usuario qualquer pelo administrador.

    Nao existe campo is_admin de proposito: o cargo de administrador nao se
    concede nem se remove por aqui, so pela ADMIN_PASSWORD no deploy. Assim um
    XSS no painel (ja houve um) nao consegue fabricar um administrador.
    """
    nome: str
    telefone: str
    bloco: str
    apartamento: str
    condominio_id: int
    is_vendedor: bool = False
    senha_nova: Optional[str] = None

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, v):
        v = v.strip()
        if len(v) < 2 or len(v) > 100:
            raise ValueError("Nome deve ter entre 2 e 100 caracteres")
        return v

    @field_validator("telefone")
    @classmethod
    def telefone_valido(cls, v):
        v = v.strip()
        if not v.isdigit() or not (8 <= len(v) <= 20):
            raise ValueError("Telefone deve conter apenas digitos (8-20 caracteres)")
        return v

    @field_validator("bloco", "apartamento")
    @classmethod
    def campo_obrigatorio_curto(cls, v):
        v = (v or "").strip()
        if not v or len(v) > 20:
            raise ValueError("Campo obrigatorio (max 20 caracteres)")
        return v

    @field_validator("senha_nova")
    @classmethod
    def senha_forte(cls, v):
        if not v:
            return None
        min_len = int(_env("PASSWORD_MIN_LEN", "8"))
        if len(v) < min_len:
            raise ValueError(f"Nova senha deve ter no minimo {min_len} caracteres")
        return v


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/api/health")
def health_check():
    """Endpoint usado por plataformas de nuvem para verificar se o servico esta no ar."""
    session = SessionLocal()
    try:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "database": str(e)})
    finally:
        session.close()


# ── Auth ──────────────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginBody):
    session = SessionLocal()
    try:
        u = session.query(Usuario).filter_by(telefone=body.telefone).first()
        senha_ok = verificar_senha(body.senha, u.senha_hash) if u else False
        if not u or not senha_ok:
            raise HTTPException(status_code=401, detail="Credenciais invalidas")
        token = create_token(u.id)
        user_data = {"id": u.id, "nome": u.nome, "telefone": u.telefone,
                     "is_admin": u.is_admin, "is_vendedor": u.is_vendedor,
                     "condominio_id": u.condominio_id}
        resp = JSONResponse({"token": token, "user": user_data})
        _set_auth_cookie(resp, token)
        return resp
    finally:
        session.close()


@app.post("/api/auth/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterBody):
    session = SessionLocal()
    try:
        cond = session.query(Condominio).filter_by(token_acesso=body.token_condominio).first()
        if not cond:
            raise HTTPException(status_code=400, detail="Token de acesso invalido")
        if session.query(Usuario).filter_by(telefone=body.telefone).first():
            raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
        u = Usuario(
            nome=body.nome, telefone=body.telefone, senha_hash=hash_senha(body.senha),
            bloco=body.bloco or "", apartamento=body.apartamento or "",
            is_vendedor=body.is_vendedor, is_admin=False, condominio_id=cond.id,
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        token = create_token(u.id)
        user_data = {"id": u.id, "nome": u.nome, "telefone": u.telefone,
                     "is_admin": u.is_admin, "is_vendedor": u.is_vendedor,
                     "condominio_id": u.condominio_id}
        resp = JSONResponse({"message": "Cadastro realizado com sucesso", "token": token,
                             "user": user_data})
        _set_auth_cookie(resp, token)
        return resp
    finally:
        session.close()


@app.post("/api/auth/logout")
def logout():
    """Invalida o cookie de autenticacao no navegador."""
    resp = JSONResponse({"ok": True, "message": "Logout realizado"})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


@app.post("/api/auth/validate-token")
@limiter.limit("20/minute")
def validate_token(request: Request, body: ValidateTokenBody):
    session = SessionLocal()
    try:
        token_upper = body.token.strip().upper()
        cond = session.query(Condominio).filter_by(token_acesso=token_upper).first()
        if not cond:
            return {"valid": False, "condominio_nome": None}
        return {"valid": True, "condominio_nome": cond.nome}
    finally:
        session.close()


@app.get("/api/auth/me")
def me(user_id: int = Depends(get_current_user_id)):
    """Retorna dados do usuario autenticado. Util para restaurar sessao via cookie no carregamento."""
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        return {"id": u.id, "nome": u.nome, "telefone": u.telefone,
                "is_admin": u.is_admin, "is_vendedor": u.is_vendedor,
                "condominio_id": u.condominio_id}
    finally:
        session.close()


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordBody, user_id: int = Depends(get_current_user_id)):
    """Permite ao usuario autenticado trocar sua propria senha."""
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        if not verificar_senha(body.senha_atual, u.senha_hash):
            raise HTTPException(status_code=400, detail="Senha atual incorreta")
        u.senha_hash = hash_senha(body.senha_nova)
        session.commit()
        return {"ok": True, "message": "Senha alterada com sucesso"}
    finally:
        session.close()


# ── Cardapio em PDF (guardado no banco — a Vercel nao tem disco persistente) ──
def _ids_com_pdf(session, usuario_ids) -> set:
    """Quais destes usuarios tem cardapio. Uma consulta so, sem carregar os bytes."""
    if not usuario_ids:
        return set()
    linhas = (session.query(CardapioPdf.usuario_id)
              .filter(CardapioPdf.usuario_id.in_(usuario_ids)).all())
    return {uid for (uid,) in linhas}


def _tem_pdf(session, usuario_id: int) -> bool:
    return bool(_ids_com_pdf(session, [usuario_id]))


def _resposta_pdf(session, usuario_id: int, nome_padrao: str) -> Response:
    pdf = (session.query(CardapioPdf)
           .options(undefer(CardapioPdf.conteudo))
           .filter_by(usuario_id=usuario_id).first())
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF nao encontrado")
    profile = session.query(PerfilComercial).filter_by(usuario_id=usuario_id).first()
    nome = f"{profile.nome_negocio}.pdf" if profile else nome_padrao
    # filename* (RFC 5987) aceita acentos no nome do negocio e neutraliza aspas e quebras de linha
    return Response(
        content=pdf.conteudo,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(nome)}"},
    )


def _vendor_json(p, ids_com_pdf: set) -> dict:
    return {"id": p.usuario_id, "nome_negocio": p.nome_negocio,
            "descricao": p.descricao or "", "categoria": p.categoria,
            "whatsapp": p.whatsapp, "tem_pdf": p.usuario_id in ids_com_pdf}


# ── Marketplace ───────────────────────────────────────────────────────────────
@app.get("/api/marketplace/categories")
def list_categories(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        rows = session.query(PerfilComercial.categoria).distinct().all()
        return sorted([r[0] for r in rows if r[0]])
    finally:
        session.close()


@app.get("/api/marketplace/vendors")
def list_vendors(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        if u.is_admin:
            perfis = session.query(PerfilComercial).all()
        elif not u.condominio_id:
            return []
        else:
            perfis = buscar_vendedores_por_condominio(u.condominio_id)
        com_pdf = _ids_com_pdf(session, [p.usuario_id for p in perfis])
        return [_vendor_json(p, com_pdf) for p in perfis]
    finally:
        session.close()


@app.get("/api/marketplace/vendor/{vendor_id}/pdf")
def download_vendor_pdf(vendor_id: int, user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        requester = session.get(Usuario, user_id)
        vendor_user = session.get(Usuario, vendor_id)
        if not requester or not vendor_user:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        if not requester.is_admin and requester.condominio_id != vendor_user.condominio_id:
            raise HTTPException(status_code=403, detail="Acesso negado")
        return _resposta_pdf(session, vendor_id, "cardapio.pdf")
    finally:
        session.close()


# ── Perfil do vendedor ────────────────────────────────────────────────────────
@app.get("/api/vendor/profile")
def get_vendor_profile(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        profile = session.query(PerfilComercial).filter_by(usuario_id=user_id).first()
        if not profile:
            return Response(status_code=204)
        return {"nome_negocio": profile.nome_negocio, "descricao": profile.descricao or "",
                "categoria": profile.categoria, "whatsapp": profile.whatsapp,
                "tem_pdf": _tem_pdf(session, user_id)}
    finally:
        session.close()


@app.post("/api/vendor/profile")
def create_vendor_profile(body: VendorProfileBody, user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        if session.query(PerfilComercial).filter_by(usuario_id=user_id).first():
            raise HTTPException(status_code=400, detail="Perfil ja existe. Use PUT para atualizar.")
        profile = PerfilComercial(usuario_id=user_id, nome_negocio=body.nome_negocio,
            descricao=body.descricao, categoria=body.categoria, whatsapp=body.whatsapp)
        session.add(profile)
        session.commit()
        return {"message": "Perfil criado com sucesso", "ok": True,
                "tem_pdf": _tem_pdf(session, user_id)}
    finally:
        session.close()


@app.put("/api/vendor/profile")
def update_vendor_profile(body: VendorProfileBody, user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        profile = session.query(PerfilComercial).filter_by(usuario_id=user_id).first()
        if profile:
            profile.nome_negocio = body.nome_negocio
            profile.descricao = body.descricao
            profile.categoria = body.categoria
            profile.whatsapp = body.whatsapp
        else:
            profile = PerfilComercial(usuario_id=user_id, nome_negocio=body.nome_negocio,
                descricao=body.descricao, categoria=body.categoria, whatsapp=body.whatsapp)
            session.add(profile)
        session.commit()
        return {"message": "Perfil atualizado com sucesso", "ok": True,
                "tem_pdf": _tem_pdf(session, user_id)}
    finally:
        session.close()


@app.post("/api/vendor/profile/pdf")
async def upload_pdf(file: UploadFile = File(...), user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        if file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Apenas arquivos PDF sao aceitos")
        contents = await file.read()
        if len(contents) > PDF_MAX_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="PDF excede o limite de 4 MB")
        # session.merge faz insert ou update pela chave primaria (usuario_id):
        # um novo upload substitui o cardapio anterior
        session.merge(CardapioPdf(usuario_id=user_id, conteudo=contents))
        session.commit()
        return {"ok": True, "message": "PDF enviado com sucesso"}
    finally:
        session.close()


@app.get("/api/vendor/profile/pdf")
def download_own_pdf(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        return _resposta_pdf(session, user_id, "meu_cardapio.pdf")
    finally:
        session.close()


@app.delete("/api/vendor/profile/pdf")
def delete_pdf(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        session.query(CardapioPdf).filter_by(usuario_id=user_id).delete()
        session.commit()
        return {"ok": True, "message": "PDF removido"}
    finally:
        session.close()


# ── Admin ─────────────────────────────────────────────────────────────────────
@app.get("/api/admin/stats")
def admin_stats(admin_id: int = Depends(require_admin)):
    session = SessionLocal()
    try:
        total_condominios = session.query(Condominio).count()
        total_usuarios = session.query(Usuario).count()
        total_moradores = session.query(Usuario).filter(
            Usuario.is_admin == False, Usuario.is_vendedor == False).count()
        total_prestadores = session.query(Usuario).filter(Usuario.is_vendedor == True).count()
        return {"condominios": total_condominios, "usuarios": total_usuarios,
                "moradores": total_moradores, "prestadores": total_prestadores}
    finally:
        session.close()


@app.get("/api/admin/condominiums")
def list_condominiums(
    skip: int = 0, limit: int = 100,
    admin_id: int = Depends(require_admin),
):
    """Lista condominios com paginacao (skip/limit)."""
    session = SessionLocal()
    try:
        total = session.query(Condominio).count()
        condominios = session.query(Condominio).offset(skip).limit(limit).all()
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [{"id": c.id, "nome": c.nome, "token_acesso": c.token_acesso}
                      for c in condominios],
        }
    finally:
        session.close()


@app.post("/api/admin/condominiums")
def create_condominium(req: CondominiumBody, admin_id: int = Depends(require_admin)):
    session = SessionLocal()
    try:
        if session.query(Condominio).filter_by(token_acesso=req.token_acesso).first():
            raise HTTPException(status_code=400, detail="Token ja esta em uso")
        novo = Condominio(nome=req.nome, token_acesso=req.token_acesso)
        session.add(novo)
        session.commit()
        session.refresh(novo)
        return {"id": novo.id, "nome": novo.nome, "token_acesso": novo.token_acesso}
    finally:
        session.close()


@app.delete("/api/admin/condominiums/{condo_id}")
def delete_condominium(condo_id: int, admin_id: int = Depends(require_admin)):
    session = SessionLocal()
    try:
        cond = session.query(Condominio).filter_by(id=condo_id).first()
        if not cond:
            raise HTTPException(status_code=404, detail="Condominio nao encontrado")
        vinculados = session.query(Usuario).filter_by(condominio_id=condo_id).count()
        if vinculados:
            raise HTTPException(
                status_code=400,
                detail=f"Nao e possivel excluir: {vinculados} usuario(s) ainda pertencem a este "
                       f"condominio. Mova-os para outro condominio ou exclua-os antes.")
        session.delete(cond)
        session.commit()
        return {"ok": True, "message": "Condominio removido com sucesso"}
    finally:
        session.close()


@app.put("/api/admin/condominiums/{condo_id}")
def update_condominium(condo_id: int, req: CondominiumBody, admin_id: int = Depends(require_admin)):
    """Renomeia o condominio e/ou troca o token de acesso.

    Trocar o token invalida o anterior: quem ainda nao se cadastrou precisa do novo.
    """
    session = SessionLocal()
    try:
        cond = session.query(Condominio).filter_by(id=condo_id).first()
        if not cond:
            raise HTTPException(status_code=404, detail="Condominio nao encontrado")
        if req.token_acesso != cond.token_acesso:
            em_uso = session.query(Condominio).filter_by(token_acesso=req.token_acesso).first()
            if em_uso and em_uso.id != condo_id:
                raise HTTPException(status_code=400, detail="Token ja esta em uso por outro condominio")
        cond.nome = req.nome
        cond.token_acesso = req.token_acesso
        session.commit()
        return {"id": condo_id, "nome": req.nome, "token_acesso": req.token_acesso,
                "message": "Condominio atualizado com sucesso"}
    finally:
        session.close()


@app.post("/api/admin/condominiums/{condo_id}/rotate-token")
def rotate_condominium_token(condo_id: int, admin_id: int = Depends(require_admin)):
    """Gera um novo token de acesso para o condominio (invalida o anterior)."""
    session = SessionLocal()
    try:
        cond = session.query(Condominio).filter_by(id=condo_id).first()
        if not cond:
            raise HTTPException(status_code=404, detail="Condominio nao encontrado")
        novo_token = secrets.token_urlsafe(8).upper()
        # Garante unicidade
        while session.query(Condominio).filter_by(token_acesso=novo_token).first():
            novo_token = secrets.token_urlsafe(8).upper()
        cond.token_acesso = novo_token
        session.commit()
        return {"id": cond.id, "nome": cond.nome, "token_acesso": novo_token,
                "message": "Token rotacionado com sucesso"}
    finally:
        session.close()


@app.get("/api/admin/users")
def list_users(
    skip: int = 0, limit: int = 100,
    admin_id: int = Depends(require_admin),
):
    """Lista usuarios com paginacao (skip/limit)."""
    session = SessionLocal()
    try:
        from sqlalchemy.orm import undefer
        total = session.query(Usuario).count()
        usuarios = (session.query(Usuario)
            .options(undefer(Usuario.bloco), undefer(Usuario.apartamento))
            .offset(skip).limit(limit).all())
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [
                {"id": u.id, "nome": u.nome, "telefone": u.telefone, "bloco": u.bloco,
                 "apartamento": u.apartamento, "is_admin": u.is_admin,
                 "is_vendedor": u.is_vendedor,
                 "condominio": u.condominio.nome if u.condominio else None}
                for u in usuarios
            ],
        }
    finally:
        session.close()


@app.put("/api/admin/profile")
def update_admin_profile(body: AdminProfileBody, admin_id: int = Depends(require_admin)):
    """Permite ao administrador editar seu proprio nome, telefone e senha."""
    session = SessionLocal()
    try:
        u = session.get(Usuario, admin_id)
        if not u:
            raise HTTPException(status_code=404, detail="Administrador nao encontrado")
        if body.telefone != u.telefone:
            existing = session.query(Usuario).filter_by(telefone=body.telefone).first()
            if existing and existing.id != admin_id:
                raise HTTPException(status_code=400, detail="Telefone ja cadastrado por outro usuario")
        if body.senha_nova:
            if not body.senha_atual:
                raise HTTPException(status_code=400, detail="Informe a senha atual para alterar a senha")
            if not verificar_senha(body.senha_atual, u.senha_hash):
                raise HTTPException(status_code=400, detail="Senha atual incorreta")
            u.senha_hash = hash_senha(body.senha_nova)
        u.nome = body.nome
        u.telefone = body.telefone
        session.commit()
        session.refresh(u)
        return {"ok": True, "message": "Perfil atualizado com sucesso",
                "user": {"id": u.id, "nome": u.nome, "telefone": u.telefone, "is_admin": u.is_admin}}
    finally:
        session.close()


@app.put("/api/admin/users/{user_id}")
def update_user(user_id: int, body: AdminUserUpdateBody, admin_id: int = Depends(require_admin)):
    """Corrige o cadastro de um usuario, inclusive redefinindo a senha.

    Redefinir sem a senha antiga e intencional: o app nao tem recuperacao de senha,
    entao quem esquece so volta por aqui.
    """
    from sqlalchemy.orm import undefer
    session = SessionLocal()
    try:
        u = (session.query(Usuario)
             .options(undefer(Usuario.bloco), undefer(Usuario.apartamento))
             .filter_by(id=user_id).first())
        if not u:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        if u.is_admin:
            raise HTTPException(
                status_code=400,
                detail="Nao e possivel editar um administrador por aqui")

        if body.telefone != u.telefone:
            em_uso = session.query(Usuario).filter_by(telefone=body.telefone).first()
            if em_uso and em_uso.id != user_id:
                raise HTTPException(status_code=400, detail="Telefone ja cadastrado por outro usuario")

        cond = session.query(Condominio).filter_by(id=body.condominio_id).first()
        if not cond:
            raise HTTPException(status_code=400, detail="Condominio nao encontrado")
        cond_nome = cond.nome

        # Deixar de ser prestador apaga o que so faz sentido para prestador,
        # senao sobra perfil e PDF orfaos apontando para um morador comum.
        if u.is_vendedor and not body.is_vendedor:
            perfil = session.query(PerfilComercial).filter_by(usuario_id=user_id).first()
            if perfil:
                session.delete(perfil)
            session.query(CardapioPdf).filter_by(usuario_id=user_id).delete()

        u.nome = body.nome
        u.telefone = body.telefone
        u.bloco = body.bloco
        u.apartamento = body.apartamento
        u.condominio_id = body.condominio_id
        u.is_vendedor = body.is_vendedor
        if body.senha_nova:
            u.senha_hash = hash_senha(body.senha_nova)
        session.commit()
        return {"ok": True, "message": "Usuario atualizado com sucesso",
                "user": {"id": user_id, "nome": body.nome, "telefone": body.telefone,
                         "bloco": body.bloco, "apartamento": body.apartamento,
                         "is_vendedor": body.is_vendedor, "condominio": cond_nome}}
    finally:
        session.close()


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, admin_id: int = Depends(require_admin)):
    session = SessionLocal()
    try:
        u = session.query(Usuario).filter_by(id=user_id).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        if u.is_admin:
            raise HTTPException(status_code=400, detail="Nao e possivel excluir um administrador")
        perfil = session.query(PerfilComercial).filter_by(usuario_id=user_id).first()
        if perfil:
            session.delete(perfil)
        session.query(CardapioPdf).filter_by(usuario_id=user_id).delete()
        session.delete(u)
        session.commit()
        return {"ok": True, "message": "Usuario removido com sucesso"}
    finally:
        session.close()


# ── SPA ───────────────────────────────────────────────────────────────────────
# O frontend inteiro e um unico index.html; qualquer rota que nao seja da API o devolve.
# NUNCA servir arquivos pelo caminho pedido: a versao anterior fazia isso e expunha
# o codigo-fonte, o .env (SECRET_KEY, ADMIN_PASSWORD) e o banco SQLite.
@app.get("/")
def serve_index():
    return FileResponse(str(INDEX_HTML))


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Rota nao encontrada")
    return FileResponse(str(INDEX_HTML))
