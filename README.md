# CondoMarket 🏪

Marketplace interno para condomínios — moradores descobrem e contratam prestadores de serviço do próprio condomínio diretamente pelo celular ou navegador.

---

## Visão Geral

O sistema tem dois modos de execução:

| Modo | Arquivo de entrada | Porta |
|---|---|---|
| **Web (recomendado)** | `run.py` | 8000 |
| **Desktop (Streamlit)** | `app.py` | 8501 |

O modo Web expõe uma API REST (FastAPI) que serve o `index.html` como SPA. O modo Desktop usa Streamlit diretamente.

---

## Perfis de Usuário

| Perfil | O que pode fazer |
|---|---|
| **Morador** | Navegar no marketplace, buscar e filtrar prestadores, acionar WhatsApp |
| **Prestador** | Cadastrar/editar perfil comercial, fazer upload de cardápio em PDF |
| **Admin** | Gerenciar condomínios e usuários, ver estatísticas da plataforma |

---

## Instalação

```bash
# 1. Clone o repositório
git clone <seu-repositorio>
cd App\ Condominio

# 2. Instale as dependências
pip install -r requirements.txt

# 3. (Recomendado) Defina uma chave secreta estável para JWT
export SECRET_KEY="uma-chave-segura-e-longa-aqui"

# 4. Inicie o servidor
python run.py
# Acesse: http://localhost:8000
```

---

## Credenciais de Demonstração

Criadas automaticamente na primeira execução:

| Perfil | Telefone | Senha |
|---|---|---|
| Admin | `11999999999` | `admin123` |
| Morador | `11999999991` | `morador1` |
| Prestador | `11999999994` | `vendedor1` |

Token do condomínio de demonstração: **`RECANTO2026`**

---

## Estrutura do Projeto

```
App Condominio/
├── api.py              # API REST (FastAPI) — endpoints principais
├── app.py              # Entrada do modo Streamlit
├── auth.py             # Telas de login e cadastro (Streamlit)
├── admin_view.py       # Painel do administrador (Streamlit)
├── cliente_view.py     # Feed do marketplace para moradores (Streamlit)
├── vendedor_view.py    # Painel do prestador (Streamlit)
├── database.py         # Modelos SQLAlchemy e seed de dados
├── run.py              # Script de inicialização do servidor FastAPI
├── index.html          # Frontend SPA (HTML/JS)
├── marketplace.db      # Banco de dados SQLite (gerado automaticamente)
├── requirements.txt    # Dependências Python
├── uploads/            # PDFs de cardápios enviados pelos prestadores
└── tests/
    └── test_api.py     # Testes automatizados (29 testes)
```

---

## API REST — Endpoints

### Autenticação
| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/auth/login` | Login com telefone e senha |
| POST | `/api/auth/register` | Cadastro de novo usuário |
| POST | `/api/auth/validate-token` | Valida token de condomínio |

### Marketplace (requer autenticação)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/marketplace/vendors` | Lista prestadores do condomínio |
| GET | `/api/marketplace/categories` | Lista categorias disponíveis |

### Prestador (requer autenticação)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/vendor/profile` | Consulta perfil comercial |
| POST | `/api/vendor/profile` | Cria perfil comercial |
| PUT | `/api/vendor/profile` | Atualiza perfil comercial |
| POST | `/api/vendor/profile/pdf` | Faz upload do cardápio (PDF, máx. 5 MB) |

### Admin (requer perfil Admin)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/admin/stats` | Estatísticas gerais da plataforma |
| GET | `/api/admin/condominiums` | Lista todos os condomínios |
| POST | `/api/admin/condominiums` | Cria novo condomínio |
| DELETE | `/api/admin/condominiums/{id}` | Remove condomínio (só se vazio) |
| GET | `/api/admin/users` | Lista todos os usuários |
| DELETE | `/api/admin/users/{id}` | Remove usuário |

Autenticação via header: `Authorization: Bearer <token>`

---

## Banco de Dados

SQLite com 3 tabelas:

- **condominios** — Nome e token de acesso
- **usuarios** — Dados dos moradores/prestadores/admins (bloco e apartamento são `deferred` — nunca expostos publicamente)
- **perfis_comerciais** — Dados comerciais dos prestadores

---

## Testes

```bash
# Rodar todos os testes
pytest tests/ -v

# Com cobertura
pytest tests/ -v --tb=short
```

29 testes cobrindo: autenticação, registro, marketplace, perfil de prestador e painel admin.

---

## Variáveis de Ambiente

| Variável | Obrigatória em produção | Descrição |
|---|---|---|
| `SECRET_KEY` | **Sim** | Chave para assinar tokens JWT. Sem ela, tokens são invalidados a cada restart. |

---

## Segurança — Observações

- Bloco e apartamento dos usuários são campos `deferred` no SQLAlchemy e mascarados como `"CONFIDENCIAL"` na listagem pública de prestadores
- Upload de PDF validado por tipo MIME e tamanho (máx. 5 MB)
- Em produção, restrinja `allow_origins` no CORS a domínios específicos
- Considere migrar o hash de senhas de SHA-256 para `bcrypt` ou `argon2`
