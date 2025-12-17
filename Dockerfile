FROM python:3.10-slim

# Evita que o Python gere arquivos .pyc e garante logs em tempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Define o diretório de trabalho dentro do container
WORKDIR /app

# --- INSTALAÇÃO DE DRIVERS DE BANCO DE DADOS (CORRIGIDO PARA DEBIAN 12) ---
# Instala dependências do sistema e prepara para o driver ODBC
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    ca-certificates \
    apt-transport-https \
    unixodbc \
    unixodbc-dev \
    libgssapi-krb5-2 \
    && mkdir -p /etc/apt/keyrings \
    # Baixa a chave da Microsoft e salva no formato correto
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    # Adiciona o repositório da Microsoft referenciando a chave
    && echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    # Instala o driver aceitando a licença
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    # Limpeza para reduzir tamanho da imagem
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copia apenas o requirements primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instala as dependências do Python
# ATENÇÃO: Se o requirements.txt tiver versões inexistentes (ex: numpy==2.3.5), o build falhará aqui.
# Remova as versões fixas do requirements.txt se necessário antes de rodar.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia todo o código do projeto para dentro da imagem
COPY . .

# Expõe a porta 5000
EXPOSE 5000

# Comando para iniciar o servidor acessível externamente (0.0.0.0)
CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]