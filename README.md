ReoScore v13 - Monitoramento de Qualidade de Massas

O ReoScore é um sistema web (Dashboard) desenvolvido para monitorar a qualidade de compostos de borracha em laboratório. Ele integra dados de equipamentos de análise (Reômetros/Viscosímetros) com dados de produção (ERP Sankhya) para calcular automaticamente notas de conformidade.

🚀 Funcionalidades

Monitoramento em Tempo Real: Leitura de ensaios direto do banco de dados do laboratório (SQL Server).

Cálculo de Score: Algoritmo que pontua cada ensaio (0 a 100) baseado em especificações técnicas (Ts2, T90, Viscosidade).

Integração ERP: Conexão com Oracle (Sankhya) para validar códigos e descrições de produtos.

Painel de Configurações Unificado:

Materiais (Admin): Gestão de especificações técnicas (Limites e Alvos).

Regras de Ação (Admin): Definição global de critérios de aprovação e cores de etiquetas.

Ensinar Sistema (Todos): Ferramenta de auditoria onde operadores corrigem a identificação de lotes.

Smart Matching & Aprendizado: Identificação inteligente de produtos via fuzzy logic e correções manuais ("Ensinar Sistema") persistidas em banco local.

Auditoria de Alterações: Logs automáticos de quem realizou correções manuais e quando elas ocorreram.

Gestão de Lotes: Cruzamento automático com planilhas de apontamento de produção (Excel/Rede).

🛠️ Tecnologias Utilizadas

Backend: Python 3.10+, Flask

Banco de Dados:

SQL Server: Leitura de dados brutos do laboratório.

Oracle: Leitura de cadastro de produtos do ERP.

SQLite: Banco local (instance/users_reoscore.db) para gestão de usuários, senhas e regras de aprendizado com logs.

Manipulação de Dados: Pandas, SQLAlchemy

Frontend: HTML5, Bootstrap 5, HTMX

Outros: OpenPyXL (Excel), PyODBC, OracleDB

⚙️ Controle de Acesso e Logs (RBAC)

O sistema possui controle de acesso baseado em funções:

Administradores:

Acesso total ao Painel de Configurações.

Podem editar Specs de Materiais e Regras de Ação.

Podem visualizar e editar a aba "Ensinar Sistema".

Operadores/Usuários:

Acesso restrito no Painel de Configurações.

Visualizam apenas a aba "Ensinar Sistema".

Permite que o operador corrija falhas de identificação de lote no dia a dia.

Auditoria:

Todas as correções manuais feitas na aba "Ensinar Sistema" são gravadas no banco local com o Nome do Usuário e Data/Hora da alteração.

⚙️ Pré-requisitos

Python 3.x instalado.

ODBC Driver 18 for SQL Server instalado (necessário para conexão com o banco do laboratório).

Oracle Instant Client (caso necessário para a biblioteca oracledb no ambiente Windows).

📦 Instalação

Clone o repositório:

git clone https://seu-repositorio/reometro_score.git
cd reometro_score


Crie um ambiente virtual:

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate


Instale as dependências:

pip install -r requirements.txt


Configure as variáveis de ambiente. Crie um arquivo .env na raiz com o seguinte conteúdo (ajuste conforme seu ambiente):

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


🚀 Execução

Para iniciar o servidor web:

python app.py


Nota: O banco de dados SQLite local (users_reoscore.db) será atualizado automaticamente com as novas tabelas de log na primeira execução.

Acesso: http://127.0.0.1:5000

📂 Estrutura do Projeto

app.py: Ponto de entrada da aplicação Flask e orquestrador de rotas.

etl_planilha.py: Módulo de leitura da planilha de produção.

instance/users_reoscore.db: Banco SQLite local. Armazena usuários e a tabela aprendizado_local (regras de correção + logs de auditoria).

models/: Classes de negócio (Ensaio, Massa, Usuário).

services/: Integrações externas (Sankhya, Config Manager, Learning Service).

templates/: Arquivos HTML (Jinja2).

static/: CSS e JavaScript.

tools/: Scripts auxiliares de manutenção e ETL.

Desenvolvido para uso interno no Laboratório de Qualidade.