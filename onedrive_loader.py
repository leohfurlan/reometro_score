from office365.sharepoint.client_context import ClientContext
from office365.runtime.auth.user_credential import UserCredential
import os
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURAÇÕES ---
# URL base do seu SharePoint/OneDrive Corporativo
# Exemplo: https://suaempresa-my.sharepoint.com/personal/seu_email_empresa_com
URL_SITE = os.getenv("URL_SITE_ONEDRIVE")  

# Credenciais (Ideal usar variáveis de ambiente ou .env)
USERNAME = os.getenv("USERNAME_ONEDRIVE")  
PASSWORD = os.getenv("PASSWORD_ONEDRIVE")

# Caminho do arquivo NO ONEDRIVE (Caminho relativo após o /Documents/)
CAMINHO_RELATIVO = r"Laboratorio - LABORATÓRIO/RESULTADOS ANÁLISES/REG 403 - 38 ACOMPANHAMENTO ANÁLISES DE MASSAS.xlsx"

def baixar_excel_do_onedrive(nome_destino="temp_planilha_lab.xlsx"):
    print(f"--- ☁️ Conectando ao OneDrive de {USERNAME} ---")
    
    try:
        # 1. Autenticação
        ctx = ClientContext(URL_SITE).with_credentials(UserCredential(USERNAME, PASSWORD))
        
        # 2. Localiza o arquivo
        # Em OneDrive for Business, a biblioteca padrão é "Documents"
        web = ctx.web
        ctx.load(web)
        ctx.execute_query()
        print(f"   > Conectado em: {web.properties['Title']}")
        
        # 3. Download
        print("   > Baixando arquivo...", end=" ")
        arquivo_url = f"/personal/seu_usuario_vulcaflex_com_br/Documents/{CAMINHO_RELATIVO}"
        
        # Opção mais segura: Buscar pelo caminho relativo na biblioteca
        # Ajuste 'Documents' se sua biblioteca principal tiver outro nome
        file_url = f"Documents/{CAMINHO_RELATIVO}"
        
        with open(nome_destino, "wb") as local_file:
            ctx.web.get_file_by_server_relative_url(file_url).download(local_file).execute_query()
            
        print("✅ Sucesso!")
        return nome_destino

    except Exception as e:
        print(f"\n❌ Erro ao baixar do OneDrive: {e}")
        return None

if __name__ == "__main__":
    baixar_excel_do_onedrive()