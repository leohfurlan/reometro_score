# Usa uma imagem Python leve e oficial baseada em Debian
FROM python:3.10-slim

# Evita que o Python gere arquivos .pyc e garante logs em tempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Define o diretório de trabalho dentro do container
WORKDIR /app

# --- INSTALAÇÃO DE DRIVERS DE BANCO DE DADOS (CRÍTICO) ---
# Instala dependências do sistema e o driver ODBC 18 para SQL Server
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    apt-transport-https \
    unixodbc \
    unixodbc-dev \
    libgssapi-krb5-2 \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copia apenas o requirements primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instala as dependências do Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia todo o código do projeto para dentro da imagem
COPY . .

# Expõe a porta 5000
EXPOSE 5000

# Comando para iniciar o servidor acessível externamente (0.0.0.0)
CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]