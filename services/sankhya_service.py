import oracledb
from models.dissolucao import Dissolucao
from models.massa import Massa
from models.materia_prima import MateriaPrima
from dotenv import load_dotenv
import os

load_dotenv()

# Configuracoes Oracle
LIB_DIR = os.getenv("ORACLE_LIB_DIR")
DB_USER = os.getenv("ORACLE_DB_USER")
DB_PASSWORD = os.getenv("ORACLE_DB_PASSWORD")
DB_DSN = os.getenv("ORACLE_DB_DSN")

def get_connection():
    try:
        oracledb.init_oracle_client(lib_dir=LIB_DIR)
    except:
        pass
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)

def importar_catalogo_sankhya():
    """
    Retorna dois dicionarios:
    1. catalogo_cod: { 26791: ObjetoProduto }
    2. catalogo_nome: { "CAMELBACK STD": ObjetoProduto }
    """
    print("--- SANKHYA: Importando Catalogo de Produtos... ---")
    
    query = """
    SELECT 
        PRO.CODPROD,
        PRO.DESCRPROD,
        PRO.CODGRUPOPROD
    FROM SANKHYA.TGFPRO PRO
    WHERE PRO.ATIVO = 'S'
      AND (PRO.CODGRUPOPROD BETWEEN 10000000 AND 18999999)
      AND (
            PRO.CODGRUPOPROD BETWEEN 16010100 AND 16011200
         OR PRO.CODGRUPOPROD BETWEEN 18010100 AND 18010700
         OR PRO.CODGRUPOPROD = 18010900
         OR PRO.CODGRUPOPROD = 18010800
      )
    """
    
    catalogo_cod = {}
    catalogo_nome = {}
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        
        for row in cursor:
            cod = row[0]
            desc = str(row[1]).strip()
            grupo = row[2]
            
            obj = None

            # Classificacao por grupo Sankhya; apenas MateriaPrima, Massa e Dissolucao sao importados
            if grupo == 18010800:
                obj = Dissolucao(cod, desc)
            elif 18010100 <= grupo <= 18010700 or grupo == 18010900:
                obj = Massa(cod, desc)
            elif 16010100 <= grupo <= 16011200:
                obj = MateriaPrima(cod, desc)
            
            if obj:
                catalogo_cod[cod] = obj
                catalogo_nome[desc.upper()] = obj
        
        conn.close()
        print(f"Catalogo sincronizado: {len(catalogo_cod)} produtos carregados.")
        
    except Exception as e:
        print(f"Erro ao conectar no Sankhya: {e}")
        return {}, {}
        
    return catalogo_cod, catalogo_nome
