from office365.sharepoint.client_context import ClientContext
from office365.runtime.auth.user_credential import UserCredential
import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()
# --- CONFIGURAÇÕES DO SHAREPOINT (EQUIPE) ---

# 1. A URL do Site (A "Raiz" onde a biblioteca de documentos vive)
# Baseado no seu link, parece ser esta:
URL_SITE = os.getenv("URL_SITE_SHAREPOINT")  

# 2. Caminho Relativo do Arquivo (Onde ele mora dentro do servidor)
# Copiei exatamente a estrutura do seu link
CAMINHO_ARQUIVO = os.getenv("CAMINHO_REG403")

# 3. Suas Credenciais
USERNAME = os.getenv("USERNAME_ONEDRIVE")  
PASSWORD = os.getenv("PASSWORD_ONEDRIVE")

def baixar_excel_sharepoint(nome_destino="cache_reg403.xlsx"):
    print(f"--- 🏢 Conectando ao SharePoint: {URL_SITE} ---")
    
    try:
        # Autenticação
        ctx = ClientContext(URL_SITE).with_credentials(UserCredential(USERNAME, PASSWORD))
        
        # Teste de conexão: Pega o título do site para ver se deu certo
        web = ctx.web
        ctx.load(web)
        ctx.execute_query()
        print(f"   > Conectado ao site: '{web.properties['Title']}'")

        # Download do Arquivo
        # O truque aqui é usar o 'server_relative_url', que é infalível
        print(f"   > Baixando: {os.path.basename(CAMINHO_ARQUIVO)}...", end=" ")
        
        # Codifica URL para lidar com espaços e acentos (ex: 'ANÁLISES' vira 'AN%C3%81LISES')
        # O SharePoint precisa dos caracteres especiais codificados
        caminho_encoded = urllib.parse.quote(CAMINHO_ARQUIVO)
        # Corrige as barras que o quote pode ter estragado (opcional, mas seguro)
        caminho_encoded = caminho_encoded.replace("%2F", "/") 

        with open(nome_destino, "wb") as local_file:
            # Usa o caminho direto decodificado do seu link
            # Nota: Às vezes o library python aceita o caminho com espaços direto, 
            # mas se falhar, tentamos o encoded. Vamos tentar o direto primeiro.
            ctx.web.get_file_by_server_relative_url(CAMINHO_ARQUIVO).download(local_file).execute_query()
            
        print("✅ Sucesso!")
        return nome_destino

    except Exception as e:
        print(f"\n❌ Erro no Download: {e}")
        if "403" in str(e):
            print("💡 DICA: Erro 403 geralmente é permissão ou MFA (Autenticação de 2 Fatores).")
        if "404" in str(e):
            print("💡 DICA: Erro 404 significa que o caminho do arquivo está errado ou o nome mudou.")
        return None

if __name__ == "__main__":
    baixar_excel_sharepoint()