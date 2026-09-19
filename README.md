# CondoMarket 🏪

Marketplace interno para condomínios. Moradores encontram prestadores de serviço do
**próprio condomínio** e falam com eles pelo WhatsApp, direto do celular ou do navegador.
O acesso a cada condomínio é liberado por um token (ex.: `RECANTO2026`).

**Hospedagem:** Vercel (Python serverless) + PostgreSQL (Neon).

---

## Perfis de usuário

| Perfil | O que pode fazer |
|---|---|
| **Morador** | Navegar no marketplace, filtrar prestadores e abrir conversa no WhatsApp |
| **Prestador** | Cadastrar e editar o perfil comercial; enviar o cardápio em PDF (máx. 4 MB) |
| **Admin** | Gerenciar condomínios e usuários, rotacionar o token de acesso e ver estatísticas |

---

## Arquitetura

| Parte | Arquivo | Observação |
|---|---|---|
| API REST | `api.py` | FastAPI, 26 rotas. **É o ponto de entrada em produção.** |
| Frontend | `index.html` | SPA em HTML/CSS/JS puro, sem framework, servida pela própria API |
| Banco | `database.py` | SQLAlchemy 2. SQLite no desenvolvimento, PostgreSQL em produção |
| Frontend legado | `app.py`, `auth.py`, `*_view.py` | Versão original em Streamlit. **Não vai para produção** e o `streamlit` não está nas dependências |

Os cardápios em PDF ficam **no banco** (tabela `cardapios_pdf`), não em disco: a Vercel não
tem disco persistente.

---

## Rodando localmente

Requer Python 3.12 ou superior.

```bash
pip install -r requirements.txt
python setup.py      # só na primeira vez — cria o .env e o banco SQLite de demonstração
python run.py        # http://localhost:8000
```

> ⚠️ O `setup.py` **apaga e recria** o `marketplace.db`. Não rode de novo se o banco local
> tiver dados que você quer manter.

Sem `DATABASE_URL`, o app usa o SQLite local `marketplace.db`.

### Credenciais de demonstração

Só existem com `SEED_DATA=true` (o `setup.py` já cuida disso no ambiente local).

| Perfil | Telefone | Senha |
|---|---|---|
| Admin | `11999999999` | valor de `ADMIN_PASSWORD` |
| Morador | `11999999991` | `morador1` |
| Prestador | `11999999994` | `vendedor1` |

Token do condomínio de demonstração: **`RECANTO2026`**.

Essas senhas são públicas. **Nunca use `SEED_DATA=true` em produção.**

---

## Deploy na Vercel

A configuração já está no repositório:

- `pyproject.toml` — dependências, versão do Python e `[tool.vercel] entrypoint = "api:app"`.
  O entrypoint é explícito porque a Vercel procura um `app` em `app.py`, e aqui esse
  arquivo é o Streamlit legado.
- `.vercelignore` — impede o envio de `.env`, bancos locais, testes e do frontend Streamlit.

### Primeira vez

1. **Importar o projeto** — em [vercel.com/new](https://vercel.com/new), importe o repositório
   `adsonlima98/condomarket`. Nenhum ajuste de build é necessário.
2. **Criar o banco** — no projeto, em **Storage → Create Database → Neon**, conecte o banco a
   todos os ambientes. A Vercel cria a variável `DATABASE_URL` sozinha.
3. **Definir as variáveis** — em **Settings → Environment Variables**:

   | Variável | Valor |
   |---|---|
   | `SECRET_KEY` | gere com `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `ADMIN_PASSWORD` | a senha do administrador |
   | `ALLOWED_ORIGINS` | o domínio do app, ex.: `https://condomarket.vercel.app` |

4. **Fazer o redeploy** — as tabelas e o usuário admin são criados na primeira requisição.

Depois disso, cada `git push` na branch `main` publica sozinho.

### Limites da plataforma que afetam o app

| Limite | Efeito no CondoMarket |
|---|---|
| Corpo de requisição e resposta de até 4,5 MB | O PDF tem teto de **4 MB** (upload e download passam pela função) |
| Sem disco persistente | Banco PostgreSQL e PDFs no banco — o SQLite **não** funciona em produção, e o app recusa iniciar sem `DATABASE_URL` |
| Instâncias efêmeras | O rate limit (`slowapi`) é contado por instância, não globalmente. Protege contra rajadas, mas não é um limite exato |
| Cold start | A primeira requisição depois de um tempo ocioso é mais lenta |

---

## Variáveis de ambiente

| Variável | Produção | Padrão | Descrição |
|---|---|---|---|
| `DATABASE_URL` | **obrigatória** | SQLite local | URL do PostgreSQL. Criada pela integração Neon. Aceita `postgres://` e `postgresql://` |
| `SECRET_KEY` | **obrigatória** | aleatória a cada início | Assina os JWT. Sem ela, todo mundo é deslogado a cada cold start |
| `ADMIN_PASSWORD` | **obrigatória** | gerada e exibida uma vez no log | Senha do admin criado na primeira execução |
| `ALLOWED_ORIGINS` | recomendada | `*` (com aviso) | Domínios permitidos no CORS, separados por vírgula |
| `ADMIN_PHONE` | opcional | `11999999999` | Telefone (login) do admin |
| `PASSWORD_MIN_LEN` | opcional | `8` | Tamanho mínimo de senha |
| `BCRYPT_ROUNDS` | opcional | `12` | Custo do bcrypt — não altere em produção |
| `SEED_DATA` | **nunca em produção** | `false` | Cria os dados de demonstração |
| `TESTING` | só nos testes | — | Desliga o rate limit e o cookie `secure` |

---

## API REST

A autenticação usa JWT, enviado de uma de duas formas:
- cookie httpOnly `cm_auth`, definido no login — é o que o navegador usa;
- header `Authorization: Bearer <token>`, para clientes de API e testes.

### Autenticação

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/auth/login` | Login com telefone e senha (10/min por IP) |
| POST | `/api/auth/register` | Cadastro de morador ou prestador (5/min por IP) |
| POST | `/api/auth/validate-token` | Valida o token de um condomínio (20/min por IP) |
| POST | `/api/auth/logout` | Encerra a sessão (apaga o cookie) |
| GET | `/api/auth/me` | Dados do usuário logado |
| POST | `/api/auth/change-password` | Troca a própria senha |

### Marketplace

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/marketplace/vendors` | Prestadores do condomínio do usuário (todos, para o admin) |
| GET | `/api/marketplace/categories` | Categorias disponíveis |
| GET | `/api/marketplace/vendor/{id}/pdf` | Cardápio de um prestador do mesmo condomínio |

### Prestador

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/vendor/profile` | Consulta o perfil comercial |
| POST | `/api/vendor/profile` | Cria o perfil comercial |
| PUT | `/api/vendor/profile` | Atualiza (ou cria) o perfil comercial |
| POST | `/api/vendor/profile/pdf` | Envia o cardápio (PDF, máx. 4 MB; substitui o anterior) |
| GET | `/api/vendor/profile/pdf` | Baixa o próprio cardápio |
| DELETE | `/api/vendor/profile/pdf` | Remove o cardápio |

### Admin

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/admin/stats` | Totais de condomínios, usuários, moradores e prestadores |
| GET | `/api/admin/condominiums` | Lista condomínios |
| POST | `/api/admin/condominiums` | Cria condomínio |
| DELETE | `/api/admin/condominiums/{id}` | Remove condomínio (só se não tiver usuários) |
| POST | `/api/admin/condominiums/{id}/rotate-token` | Gera um novo token e invalida o anterior |
| GET | `/api/admin/users?skip=0&limit=100` | Lista usuários, paginado |
| DELETE | `/api/admin/users/{id}` | Remove usuário, com perfil e cardápio (admins não podem ser removidos) |
| PUT | `/api/admin/profile` | Admin edita o próprio nome, telefone e senha |

### Outras

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/` e demais rotas fora de `/api` | Servem o `index.html` (SPA) |

---

## Banco de dados

| Tabela | Conteúdo |
|---|---|
| `condominios` | Nome e token de acesso |
| `usuarios` | Moradores, prestadores e admins. Bloco e apartamento são colunas `deferred` |
| `perfis_comerciais` | Dados comerciais dos prestadores |
| `cardapios_pdf` | Bytes do PDF de cada prestador, em tabela separada para não pesar nas listagens |

As tabelas são criadas automaticamente na primeira execução (`init_db`). Não há ferramenta
de migração: **mudanças em colunas existentes precisam ser aplicadas à mão** no PostgreSQL.

---

## Testes

```bash
pip install pytest
pytest tests/ -v
```

São 63 testes, cobrindo autenticação, cadastro, marketplace, perfil de prestador, cardápio
em PDF, painel admin, paginação, rotação de token, a rota do SPA e a configuração do banco.

Os testes usam SQLite em memória **com chaves estrangeiras ligadas**, para se comportar
como o PostgreSQL de produção. Não tocam nem o `marketplace.db` nem o disco.

---

## Segurança

- Senhas com **bcrypt** (custo 12). O `migrar_senhas.py` converteu o formato SHA-256 antigo.
- JWT (HS256, validade de 1 dia) em cookie **httpOnly**, `SameSite=Lax` e `Secure` em produção:
  o JavaScript nunca acessa o token.
- Bloco e apartamento são colunas `deferred` e aparecem como `"CONFIDENCIAL"` na listagem
  pública de prestadores.
- Rate limit no login, no cadastro e na validação de token.
- Upload de PDF validado por tipo MIME e tamanho.
- A rota do SPA devolve **somente** o `index.html`. Versões anteriores serviam qualquer arquivo
  do projeto pelo caminho pedido — código-fonte, `.env` e banco — e isso foi corrigido.
- Sem `SECRET_KEY` ou com `ALLOWED_ORIGINS=*`, o app emite um aviso ao iniciar.
