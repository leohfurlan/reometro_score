# ReoScore v13 - Monitoramento de Qualidade de Massas

O **ReoScore** é um sistema web (Dashboard) desenvolvido para monitorar a qualidade de compostos de borracha em laboratório. Ele integra dados de equipamentos de análise (Reômetros/Viscosímetros) com dados de produção (ERP Sankhya) para calcular automaticamente notas de conformidade.

## 🚀 Funcionalidades

- **Monitoramento em Tempo Real:** Leitura de ensaios direto do banco de dados do laboratório (SQL Server).
- **Cálculo de Score:** Algoritmo que pontua cada ensaio (0 a 100) baseado em especificações técnicas (Ts2, T90, Viscosidade).
- **Integração ERP:** Conexão com Oracle (Sankhya) para validar códigos e descrições de produtos.
- **Smart Matching:** Identificação inteligente de produtos via fuzzy logic e dicionários de correção, resolvendo problemas de digitação manual nos equipamentos.
- **Gestão de Lotes:** Cruzamento automático com planilhas de apontamento de produção (Excel/Rede).
- **Ferramentas de Auditoria:** Scripts para validação de dados e classificação automática de equipamentos.

## 🛠️ Tecnologias Utilizadas

- **Backend:** Python 3.10+, Flask
- **Banco de Dados:** SQL Server (Leitura Lab), Oracle (Leitura ERP), SQLite (Usuários Local)
- **Manipulação de Dados:** Pandas, SQLAlchemy
- **Frontend:** HTML5, Bootstrap 5, HTMX
- **Outros:** OpenPyXL (Excel), PyODBC, OracleDB

## ⚙️ Pré-requisitos

1.  **Python 3.x** instalado.
2.  **ODBC Driver 18 for SQL Server** instalado (necessário para conexão com o banco do laboratório).
3.  **Oracle Instant Client** (caso necessário para a biblioteca `oracledb` no ambiente Windows).

## 📦 Instalação

1.  Clone o repositório:
    ```bash
    git clone https://seu-repositorio/reometro_score.git
    cd reometro_score
    ```

2.  Crie um ambiente virtual:
    ```bash
    python -m venv venv
    # Windows:
    venv\Scripts\activate
    # Linux/Mac:
    source venv/bin/activate
    ```

3.  Instale as dependências:
    ```bash
    pip install -r requirements.txt
    ```

4.  Configure as variáveis de ambiente. Crie um arquivo `.env` na raiz com o seguinte conteúdo (ajuste conforme seu ambiente):

    ```env
    # Flask
    FLASK_SECRET_KEY=sua_chave_secreta_aqui
    DATA_MINIMA_ENSAIOS=2025-07-01

    # Caminho Planilha de Lotes (Rede)
    CAMINHO_REG403=C:\Caminho\Para\Arquivo\REG 403.xlsx

    # Banco de Dados Lab (SQL Server)
    SERVER=ip_do_servidor
    DATABASE=nome_do_banco
    USERNAME_DB=usuario
    PASSWORD_DB=senha
    DSN=ODBC Driver 18 for SQL Server

    # Banco de Dados ERP (Oracle)
    ORACLE_LIB_DIR=C:\oracle\instantclient_19_8
    ORACLE_DB_USER=usuario_oracle
    ORACLE_DB_PASSWORD=senha_oracle
    ORACLE_DB_DSN=ip_oracle:1521/servico
    ```

## 🚀 Execução

1. Para iniciar o servidor web:
    ```bash
    python app.py
    ```
O sistema estará acessível em http://127.0.0.1:5000.


2. Ferramentas de Manutenção (Pasta /tools)
Auditoria de Match: Verifica falhas na identificação de produtos.
    ```bash
    python tools/etl_match_test.py
    ```

3. Gerar De-Para: Cria planilha para correção de nomes errados.
    ```bash
    python tools/ferramenta_gerar_depara.py
    ```

4. Classificar Grupos: Atualiza o mapa de equipamentos (Reômetro vs Viscosímetro).

    ```bash
    python tools/ferramenta_classificar_grupos.py
    ```

## 📂 Estrutura do Projeto
/models: Classes de negócio (Ensaio, Massa, Usuário).

/services: Integrações externas (Sankhya, Config Manager).

/templates: Arquivos HTML (Jinja2).

/static: CSS e JavaScript.

/tools: Scripts auxiliares de manutenção e ETL.

app.py: Ponto de entrada da aplicação Flask.

etl_planilha.py: Módulo de leitura da planilha de produção.

Desenvolvido para uso interno no Laboratório de Qualidade.