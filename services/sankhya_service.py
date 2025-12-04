import oracledb
from models.massa import Massa
from models.materia_prima import MateriaPrima
from models.produto import Produto
from dotenv import load_dotenv
import os

load_dotenv()

# Configurações Oracle (do seu script de teste)
LIB_DIR = os.getenv("ORACLE_LIB_DIR")
DB_USER = os.getenv("ORACLE_DB_USER")
DB_PASSWORD = os.getenv("ORACLE_DB_PASSWORD")
DB_DSN = os.getenv("ORACLE_DB_DSN")

def get_connection():
    try:
        oracledb.init_oracle_client(lib_dir=LIB_DIR)
    except: pass
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)

def importar_catalogo_sankhya():
    """
    Retorna dois dicionários:
    1. catalogo_cod: { 26791: ObjetoProduto }
    2. catalogo_nome: { "CAMELBACK STD": ObjetoProduto }
    """
    print("--- 📡 SANKHYA: Importando Catálogo de Produtos... ---")
    
    # Query inteligente: Traz Massas (Grupo 16-18) e Matérias Primas (Ex: Grupo 10-12 - Ajuste conforme sua realidade)
    query = """
    SELECT 
        PRO.CODPROD,
        PRO.DESCRPROD,
        PRO.CODGRUPOPROD
    FROM SANKHYA.TGFPRO PRO
    WHERE PRO.ATIVO = 'S'
      -- Ajuste os grupos conforme a realidade da Vulcaflex
      -- Ex: Massas (16000000 a 18000000) e MPs (10000000 a 15000000)
      AND (PRO.CODGRUPOPROD BETWEEN 10000000 AND 18999999)
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
            
            # Lógica simples para decidir a Classe (Ajuste os IDs de grupo reais)
            if 16000000 <= grupo <= 18999999:
                obj = Massa(cod, desc)
            elif 10000000 <= grupo <= 15999999: # Exemplo de faixa para MPs
                obj = MateriaPrima(cod, desc)
            else:
                obj = Produto(cod, desc)
            
            catalogo_cod[cod] = obj
            catalogo_nome[desc.upper()] = obj # Chave em maiúsculo para facilitar busca
            
        conn.close()
        print(f"✅ Catálogo Sincronizado: {len(catalogo_cod)} produtos carregados.")
        
    except Exception as e:
        print(f"❌ Erro ao conectar no Sankhya: {e}")
        # Retorna vazio para não quebrar o sistema, mas avisa
        return {}, {}
        
    return catalogo_cod, catalogo_nome