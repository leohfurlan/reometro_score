import oracledb
from dotenv import load_dotenv
import os

load_dotenv()  # Carrega variáveis de ambiente do arquivo .env

# Configurações baseadas no teu script de teste
# Idealmente, mova as senhas para variáveis de ambiente (.env)
LIB_DIR = os.getenv("ORACLE_LIB_DIR")
DB_DSN = os.getenv("ORACLE_DB_DSN")
DB_USER = os.getenv("ORACLE_DB_USER")
DB_PASSWORD = os.getenv("ORACLE_DB_PASSWORD")

def connect_to_oracle():
    try:
        oracledb.init_oracle_client(lib_dir=LIB_DIR)
    except Exception:
        pass # Ignora se já tiver sido inicializado

    connection = oracledb.connect(
        user=DB_USER, 
        password=DB_PASSWORD, 
        dsn=DB_DSN
    )
    return connection