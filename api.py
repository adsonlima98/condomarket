"""
CondoMarket - FastAPI backend
"""
import os
import secrets
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Header, HTTPException, UploadFile, File, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import (
    SessionLocal, init_db,
    Condominio, Usuario, PerfilComercial,
    buscar_vendedores_por_condominio,
    hash_senha, verificar_senha,
)

from jose import jwt
from datetime import datetime, timedelta

# ── Seguranca ─────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "")
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
PDF_MAX_SIZE_BYTES = 5 * 1024 * 1024
PASSWORD_MIN_LEN = int(os.environ.get("PASSWORD_MIN_LEN", "8"))
COOKIE_NAME = "cm_auth"
IS_TESTING = os.environ.get("TESTING") == "true"

# Em producao (Fly.io) os PDFs ficam no volume persistente /data/pdfs
# Em desenvolvimento ficam na pasta local pdfs/
_data_dir = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
PDF_DIR = _data_dir / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)

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
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
if not _raw_origins or _raw_origins.strip() == "*":
    import warnings
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
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
        min_len = int(os.environ.get("PASSWORD_MIN_LEN", "8"))
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
        min_len = int(os.environ.get("PASSWORD_MIN_LEN", "8"))
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
        min_len = int(os.environ.get("PASSWORD_MIN_LEN", "8"))
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
            result = []
            for p in perfis:
                pdf_path = PDF_DIR / f"{p.usuario_id}.pdf"
                result.append({"id": p.usuario_id, "nome_negocio": p.nome_negocio,
                    "descricao": p.descricao or "", "categoria": p.categoria,
                    "whatsapp": p.whatsapp, "tem_pdf": pdf_path.exists()})
            return result
        if not u.condominio_id:
            return []
        perfis = buscar_vendedores_por_condominio(u.condominio_id)
        result = []
        for p in perfis:
            pdf_path = PDF_DIR / f"{p.usuario_id}.pdf"
            result.append({"id": p.usuario_id, "nome_negocio": p.nome_negocio,
                "descricao": p.descricao or "", "categoria": p.categoria,
                "whatsapp": p.whatsapp, "tem_pdf": pdf_path.exists()})
        return result
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
        pdf_path = PDF_DIR / f"{vendor_id}.pdf"
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail="PDF nao encontrado")
        profile = session.query(PerfilComercial).filter_by(usuario_id=vendor_id).first()
        filename = f"{profile.nome_negocio}.pdf" if profile else "cardapio.pdf"
        return FileResponse(str(pdf_path), media_type="application/pdf", filename=filename)
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
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        return {"nome_negocio": profile.nome_negocio, "descricao": profile.descricao or "",
                "categoria": profile.categoria, "whatsapp": profile.whatsapp,
                "tem_pdf": pdf_path.exists()}
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
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        return {"message": "Perfil criado com sucesso", "ok": True, "tem_pdf": pdf_path.exists()}
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
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        return {"message": "Perfil atualizado com sucesso", "ok": True, "tem_pdf": pdf_path.exists()}
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
            raise HTTPException(status_code=400, detail="PDF excede o limite de 5 MB")
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        pdf_path.write_bytes(contents)
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
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail="PDF nao encontrado")
        profile = session.query(PerfilComercial).filter_by(usuario_id=user_id).first()
        filename = f"{profile.nome_negocio}.pdf" if profile else "meu_cardapio.pdf"
        return FileResponse(str(pdf_path), media_type="application/pdf", filename=filename)
    finally:
        session.close()


@app.delete("/api/vendor/profile/pdf")
def delete_pdf(user_id: int = Depends(get_current_user_id)):
    session = SessionLocal()
    try:
        u = session.get(Usuario, user_id)
        if not u or not u.is_vendedor:
            raise HTTPException(status_code=403, detail="Acesso negado")
        pdf_path = PDF_DIR / f"{user_id}.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
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
        if session.query(Usuario).filter_by(condominio_id=condo_id).first():
            raise HTTPException(status_code=400, detail="Nao e possivel excluir condominio com usuarios")
        session.delete(cond)
        session.commit()
        return {"ok": True, "message": "Condominio removido com sucesso"}
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
        session.delete(u)
        session.commit()
        return {"ok": True, "message": "Usuario removido com sucesso"}
    finally:
        session.close()


# ── SPA ───────────────────────────────────────────────────────────────────────
# ── SPA ───────────────────────────────────────────────────────────────────────
@app.get("/")
def serve_index():
    return FileResponse(str(Path(__file__).parent / "index.html"))


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    static = Path(__file__).parent / full_path
    if static.exists() and static.is_file():
        return FileResponse(str(static))
    return FileResponse(str(Path(__file__).parent / "index.html"))
