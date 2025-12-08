import pandas as pd
import os
import shutil
import tempfile
import warnings
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

# Ignora avisos do Excel
warnings.simplefilter("ignore")

def carregar_dicionario_lotes():
    print("--- 📂 ETL: Carregando Dicionário de Lotes (Via OneDrive Local) ---")
    
    # 1. Pega o caminho configurado no .env
    caminho_arquivo = os.getenv("CAMINHO_REG403")
    
    if not caminho_arquivo:
        print("❌ ERRO: Variável 'CAMINHO_REG403' não encontrada no .env")
        return {}

    # Remove aspas se houver (comum em copy/paste de caminhos)
    caminho_arquivo = caminho_arquivo.replace('"', '')

    if not os.path.exists(caminho_arquivo):
        print(f"❌ ERRO: Arquivo não encontrado no disco.")
        print(f"   -> Caminho buscado: {caminho_arquivo}")
        print("   -> DICA: Verifique se o OneDrive está rodando e sincronizado.")
        return {}

    print(f"   > Arquivo localizado: ...{caminho_arquivo[-40:]}")

    # 2. Clone Temporário (Para não travar o arquivo se alguém estiver com ele aberto)
    temp_dir = tempfile.gettempdir()
    caminho_clone = os.path.join(temp_dir, "temp_reg403_cache.xlsx")

    try:
        shutil.copy2(caminho_arquivo, caminho_clone)
    except Exception as e:
        print(f"❌ Falha ao clonar arquivo (Arquivo travado?): {e}")
        return {}

    mapa_lote_massa = {}
    # Abas para ler (Pode adicionar '2026' no futuro)
    abas_para_ler = ['2023', '2024', '2025'] 
    
    try:
        for aba in abas_para_ler:
            try:
                # header=1: Pula a linha de título e pega o cabeçalho real
                df = pd.read_excel(caminho_clone, sheet_name=aba, engine='openpyxl', header=1)
                
                # Normaliza nomes das colunas (Maiúsculo e sem espaços nas pontas)
                df.columns = [str(col).strip().upper() for col in df.columns]

                # Tenta corrigir cabeçalhos quebrados (comum na aba 2024)
                if 'MASSA' not in df.columns and len(df.columns) > 3:
                    col_index_2 = df.columns[2] 
                    df.rename(columns={col_index_2: 'MASSA'}, inplace=True)

                if 'LOTE' not in df.columns or 'MASSA' not in df.columns:
                    continue

                # Limpeza dos dados
                df = df.dropna(subset=['LOTE', 'MASSA'])
                df['LOTE'] = df['LOTE'].astype(str).str.strip().str.upper()
                df['MASSA'] = df['MASSA'].astype(str).str.strip()
                
                # Filtra lotes inválidos (muito curtos)
                df = df[df['LOTE'].str.len() > 2] 
                
                # Transforma em dicionário { LOTE: MASSA }
                dict_aba = pd.Series(df.MASSA.values, index=df.LOTE).to_dict()
                mapa_lote_massa.update(dict_aba)
                # print(f"   -> Aba '{aba}': {len(dict_aba)} registros.")
                
            except ValueError:
                # Aba não existe no arquivo, ignora
                pass
            except Exception as e:
                print(f"⚠️ Aviso na aba '{aba}': {e}")
                
    finally:
        # Remove o arquivo temporário
        if os.path.exists(caminho_clone):
            try: os.remove(caminho_clone)
            except: pass

    print(f"✅ SUCESSO: {len(mapa_lote_massa)} lotes carregados da planilha.")
    return mapa_lote_massa

if __name__ == "__main__":
    # Teste direto
    dicionario = carregar_dicionario_lotes()
    if dicionario:
        print(f"Exemplo de carga: {list(dicionario.items())[:3]}")