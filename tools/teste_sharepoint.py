import pandas as pd
import os
import tempfile
import warnings
from dotenv import load_dotenv

# Bibliotecas para conexão Nuvem Real
from office365.sharepoint.client_context import ClientContext
from office365.runtime.auth.client_credential import ClientCredential

load_dotenv()
warnings.simplefilter("ignore")

def carregar_dicionario_lotes():
    print("--- ☁️ ETL: Carregando Dicionário (Modo NUVEM SERVER-SIDE) ---")
    
    # 1. Carrega configurações
    url_site = os.getenv("URL_SITE_SHAREPOINT")
    caminho_relativo = os.getenv("CAMINHO_REG403")
    client_id = os.getenv("SHAREPOINT_CLIENT_ID")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")
    
    # Validação básica
    if not all([url_site, caminho_relativo, client_id, client_secret]):
        print("❌ ERRO: Faltam credenciais de Azure (Client ID/Secret) no .env")
        print("   -> Sem isso, o sistema não consegue acessar a nuvem sozinho.")
        return {}

    # 2. Conexão Direta com SharePoint (Sem usuário Leonardo)
    ctx = None
    try:
        # Autenticação via Aplicativo (Service Principal)
        creds = ClientCredential(client_id, client_secret)
        ctx = ClientContext(url_site).with_credentials(creds)
        
        # Teste rápido de conexão
        web = ctx.web
        ctx.load(web)
        ctx.execute_query()
        print(f"   ✅ Conectado ao SharePoint via Azure App: '{web.properties['Title']}'")
        
    except Exception as e:
        print(f"❌ Falha na Autenticação Nuvem: {e}")
        return {}

    # 3. Download do Arquivo para Temp (Sem salvar na pasta do usuário)
    temp_dir = tempfile.gettempdir()
    caminho_temp = os.path.join(temp_dir, "temp_nuvem_reg403.xlsx")
    
    try:
        print(f"   ⬇️ Baixando arquivo da nuvem...")
        with open(caminho_temp, "wb") as local_file:
            ctx.web.get_file_by_server_relative_url(caminho_relativo).download(local_file).execute_query()
        print("   ✅ Download concluído.")
        
    except Exception as e:
        print(f"❌ Erro ao baixar arquivo (Caminho errado?): {e}")
        return {}

    # 4. Processamento do Excel (Pandas)
    mapa_lote_massa = {}
    abas_para_ler = ['2023', '2024', '2025'] 
    
    try:
        for aba in abas_para_ler:
            try:
                # header=1: Pula título
                df = pd.read_excel(caminho_temp, sheet_name=aba, engine='openpyxl', header=1)
                
                # Normalização de colunas
                df.columns = [str(col).strip().upper() for col in df.columns]

                # Correção de cabeçalhos
                if 'MASSA' not in df.columns and len(df.columns) > 3:
                    col_index_2 = df.columns[2] 
                    df.rename(columns={col_index_2: 'MASSA'}, inplace=True)

                if 'LOTE' not in df.columns or 'MASSA' not in df.columns:
                    continue

                # Limpeza
                df = df.dropna(subset=['LOTE', 'MASSA'])
                df['LOTE'] = df['LOTE'].astype(str).str.strip().str.upper()
                df['MASSA'] = df['MASSA'].astype(str).str.strip()
                df = df[df['LOTE'].str.len() > 2] 
                
                # Update no dicionário principal
                dict_aba = pd.Series(df.MASSA.values, index=df.LOTE).to_dict()
                mapa_lote_massa.update(dict_aba)
                
            except ValueError:
                pass # Aba não existe
            except Exception as e:
                print(f"⚠️ Erro na aba '{aba}': {e}")
                
    finally:
        # Limpa o rastro do arquivo temporário
        if os.path.exists(caminho_temp):
            try: os.remove(caminho_temp)
            except: pass

    print(f"✅ SUCESSO: {len(mapa_lote_massa)} lotes carregados via Nuvem.")
    return mapa_lote_massa

if __name__ == "__main__":
    carregar_dicionario_lotes()