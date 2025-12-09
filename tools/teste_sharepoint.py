import pandas as pd
import os
import tempfile
import warnings
import urllib.parse
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
    caminho_relativo_raw = os.getenv("CAMINHO_REG403_SHAREPOINT") # Ajustado para bater com seu .env
    client_id = os.getenv("SHAREPOINT_CLIENT_ID")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET")
    
    # Validação básica
    if not all([url_site, caminho_relativo_raw, client_id, client_secret]):
        print("❌ ERRO: Faltam credenciais (URL, Caminho, ID ou Secret) no .env")
        return {}

    # 2. Conexão com SharePoint
    ctx = None
    try:
        print(f"   > Tentando autenticar com Client ID: ...{client_id[-4:]}")
        creds = ClientCredential(client_id, client_secret)
        ctx = ClientContext(url_site).with_credentials(creds)
        
        # Teste de conexão (Gera erro 403 se faltar permissão Sites.Read.All no Azure)
        web = ctx.web
        ctx.load(web)
        ctx.execute_query()
        print(f"   ✅ Conectado ao site: '{web.properties['Title']}'")
        
    except Exception as e:
        print(f"❌ Falha na Autenticação (Erro 403 = Falta permissão no Azure): {e}")
        print("   -> Dica: Verifique se a permissão 'Sites.Read.All' (Application) foi concedida no Azure AD.")
        return {}

    # 3. Tratamento do Caminho do Arquivo (URL Encoding)
    # SharePoint precisa de %20 para espaços, mas a lib office365 às vezes prefere o caminho decodificado.
    # Vamos tentar acessar o arquivo. Se o caminho tiver "Documentos Compartilhados",
    # internamente no SharePoint ele pode ser "Shared Documents".
    
    temp_dir = tempfile.gettempdir()
    caminho_temp = os.path.join(temp_dir, "temp_nuvem_reg403.xlsx")
    
    try:
        # Tenta primeiro com o caminho exato do .env
        caminho_final = caminho_relativo_raw
        
        # Se for biblioteca padrão, tenta ajustar para o nome interno em inglês se falhar
        # (Descomente abaixo se tiver erro de arquivo não encontrado)
        # if "Documentos Compartilhados" in caminho_final:
        #     caminho_final = caminho_final.replace("Documentos Compartilhados", "Shared Documents")

        print(f"   ⬇️ Baixando: {os.path.basename(caminho_final)}...")
        
        with open(caminho_temp, "wb") as local_file:
            # A lib office365 geralmente lida bem com espaços se a string for crua,
            # mas se falhar, use urllib.parse.quote(caminho_final)
            ctx.web.get_file_by_server_relative_url(caminho_final).download(local_file).execute_query()
            
        print("   ✅ Download concluído com sucesso.")
        
    except Exception as e:
        print(f"❌ Erro ao baixar arquivo: {e}")
        print(f"   -> Caminho tentado: {caminho_final}")
        if "404" in str(e):
             print("   -> DICA: Tente trocar 'Documentos Compartilhados' por 'Shared Documents' no caminho.")
        return {}

    # 4. Processamento do Excel (Pandas) - Mantido igual ao original
    mapa_lote_massa = {}
    abas_para_ler = ['2023', '2024', '2025', '2026'] 
    
    try:
        # Lê todas as abas de uma vez para otimizar
        xls = pd.ExcelFile(caminho_temp, engine='openpyxl')
        
        for aba in abas_para_ler:
            if aba not in xls.sheet_names:
                continue
                
            try:
                df = pd.read_excel(xls, sheet_name=aba, header=1)
                
                # Normalização de colunas
                df.columns = [str(col).strip().upper() for col in df.columns]

                # Correção de cabeçalhos
                if 'MASSA' not in df.columns and len(df.columns) > 3:
                    col_index_2 = df.columns[2] 
                    df.rename(columns={col_index_2: 'MASSA'}, inplace=True)

                if 'LOTE' not in df.columns or 'MASSA' not in df.columns:
                    continue

                # Limpeza e Dicionário
                df = df.dropna(subset=['LOTE', 'MASSA'])
                df['LOTE'] = df['LOTE'].astype(str).str.strip().str.upper()
                df['MASSA'] = df['MASSA'].astype(str).str.strip()
                df = df[df['LOTE'].str.len() > 2] 
                
                dict_aba = pd.Series(df.MASSA.values, index=df.LOTE).to_dict()
                mapa_lote_massa.update(dict_aba)
                
            except Exception as e:
                print(f"⚠️ Erro ao processar aba '{aba}': {e}")
                
    finally:
        xls.close()
        if os.path.exists(caminho_temp):
            try: os.remove(caminho_temp)
            except: pass

    print(f"✅ SUCESSO: {len(mapa_lote_massa)} lotes carregados via Nuvem.")
    return mapa_lote_massa

if __name__ == "__main__":
    carregar_dicionario_lotes()