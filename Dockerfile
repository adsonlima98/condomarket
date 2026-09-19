# ── Build ─────────────────────────────────────────────────
FROM python:3.11-slim

# Evita .pyc no container e buffer de log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias do sistema (necessarias para bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o codigo da aplicacao
COPY . .


# Remove arquivos desnecessarios em producao
RUN rm -f marketplace.db test_marketplace.db* \
         setup.py migrar_senhas.py run.py \
         *.db-journal *.db-wal *.db-shm

# Porta que a aplicacao expoe
EXPOSE 8080

# Comando de inicializacao
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
