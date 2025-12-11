import os
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

# Configuracoes lidas do .env
URL_SITE = os.getenv("URL_SITE_SHAREPOINT")
CAMINHO_ARQUIVO = os.getenv("CAMINHO_REG403_SHAREPOINT")
TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID") or os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")


def _obter_token_graph() -> str:
    missing = [
        nome
        for nome, valor in [
            ("SHAREPOINT_TENANT_ID/TENANT_ID", TENANT_ID),
            ("SHAREPOINT_CLIENT_ID", CLIENT_ID),
            ("SHAREPOINT_CLIENT_SECRET", CLIENT_SECRET),
        ]
        if not valor
    ]
    if missing:
        raise ValueError(f"Variaveis faltando no .env: {', '.join(missing)}")

    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default",
    }

    resp = requests.post(token_url, data=data, timeout=30)
    resp.raise_for_status()
    token_info = resp.json()
    token = token_info.get("access_token", "")
    print("DEBUG token_len:", len(token))
    return token


def _resolver_site_e_drive(headers):
    if not URL_SITE:
        raise ValueError("URL_SITE_SHAREPOINT nao encontrada no .env")

    parsed = urllib.parse.urlparse(URL_SITE)
    site_hostname = parsed.netloc
    site_path = parsed.path.rstrip("/")
    if not site_hostname or not site_path:
        raise ValueError("URL_SITE_SHAREPOINT invalida")

    site_url = f"https://graph.microsoft.com/v1.0/sites/{site_hostname}:{site_path}"
    site_resp = requests.get(site_url, headers=headers, timeout=30)
    site_resp.raise_for_status()
    site_id = site_resp.json().get("id")

    drive_resp = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive",
        headers=headers,
        timeout=30,
    )
    drive_resp.raise_for_status()
    drive_id = drive_resp.json().get("id")
    return site_path, drive_id


def _montar_caminho_drive(site_path: str) -> str:
    if not CAMINHO_ARQUIVO:
        raise ValueError("CAMINHO_REG403_SHAREPOINT nao configurado no .env")

    caminho = CAMINHO_ARQUIVO.replace("\\", "/").strip()
    
    # Remove o site_path se existir
    if site_path and caminho.startswith(site_path):
        caminho = caminho[len(site_path):]
    
    caminho = caminho.lstrip("/")

    # --- CORREÇÃO AQUI ---
    # Se o caminho começar com "Shared Documents/", removemos isso
    # pois o endpoint /drive já aponta para a raiz dessa biblioteca.
    prefixo_padrao = "Shared Documents/"
    if caminho.startswith(prefixo_padrao):
        caminho = caminho[len(prefixo_padrao):]
    # ---------------------

    # Codifica espacos e acentos, preservando as barras
    return urllib.parse.quote(caminho, safe="/")


def baixar_excel_sharepoint(nome_destino="cache_reg403.xlsx"):
    print(f"--- Conectando ao SharePoint via Graph: {URL_SITE} ---")
    try:
        token = _obter_token_graph()
        headers = {"Authorization": f"Bearer {token}"}

        site_path, drive_id = _resolver_site_e_drive(headers)
        caminho_drive = _montar_caminho_drive(site_path)

        print("DEBUG site_path:", site_path)
        print("DEBUG CAMINHO_ARQUIVO (.env):", CAMINHO_ARQUIVO)
        print("DEBUG caminho_drive (para o Graph):", caminho_drive)


        download_url = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{caminho_drive}:/content"
        )
        print(f"   > Baixando: {os.path.basename(CAMINHO_ARQUIVO)} ...", end=" ")
        resp = requests.get(download_url, headers=headers, timeout=60)
        resp.raise_for_status()

        with open(nome_destino, "wb") as destino:
            destino.write(resp.content)

        print("ok.")
        return nome_destino
    except requests.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response else "n/d"
        print(f"\nErro HTTP {status}: {http_err}")
    except Exception as e:
        print(f"\nErro no download: {e}")
    return None


if __name__ == "__main__":
    baixar_excel_sharepoint()
