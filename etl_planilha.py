import pandas as pd
import os
import shutil
import tempfile
import warnings

# Ignora avisos chatos do Excel (Data Validation)
warnings.simplefilter("ignore")

# --- CONFIGURAÇÃO ---
CAMINHO_ORIGINAL = r"C:\Users\leonardo.furlan\Vulcaflex Industria e Comercio Ltda\Laboratorio - LABORATÓRIO\RESULTADOS ANÁLISES\REG 403 - 38 ACOMPANHAMENTO ANÁLISES DE MASSAS.xlsx"

def carregar_dicionario_lotes():
    print("--- 📂 ETL: Carregando Dicionário de Lotes (Modo Clone v2) ---")
    
    if not os.path.exists(CAMINHO_ORIGINAL):
        print(f"❌ ERRO: Arquivo original não encontrado.")
        return {}

    # 1. Clone Temporário
    temp_dir = tempfile.gettempdir()
    caminho_clone = os.path.join(temp_dir, "temp_reg403_cache.xlsx")

    try:
        shutil.copy2(CAMINHO_ORIGINAL, caminho_clone)
    except Exception as e:
        print(f"❌ Falha ao clonar (Arquivo muito bloqueado?): {e}")
        return {}

    mapa_lote_massa = {}
    abas_para_ler = ['2023', '2024', '2025'] 
    
    try:
        for aba in abas_para_ler:
            try:
                # --- A CORREÇÃO ESTÁ AQUI: header=1 ---
                # Pula a linha 0 (Título) e usa a linha 1 como Cabeçalho
                df = pd.read_excel(caminho_clone, sheet_name=aba, engine='openpyxl', header=1)
                
                # Normaliza nomes das colunas (Remove espaços extras e põe em maiúsculo)
                df.columns = [str(col).strip().upper() for col in df.columns]

                # --- CORREÇÃO PARA CABEÇALHO FALTANDO (Aba 2024) ---
                # Se não achar a coluna 'MASSA', mas tiver 'LOTE', tenta adivinhar pelo índice
                # O padrão da sua planilha é: DATA(0), HORA(1), MASSA(2), BANBURY(3), LOTE(4)
                if 'MASSA' not in df.columns and len(df.columns) > 3:
                    print(f"   ℹ️ Aviso: Coluna MASSA sem nome na aba '{aba}'. Tentando índice 2...", end=" ")
                    col_index_2 = df.columns[2] # Pega o nome da 3ª coluna (geralmente "Unnamed: 2")
                    df.rename(columns={col_index_2: 'MASSA'}, inplace=True)

                # Verifica se agora temos as colunas necessárias
                if 'LOTE' not in df.columns or 'MASSA' not in df.columns:
                    print(f"⚠️ Pulei '{aba}': Colunas LOTE/MASSA não encontradas. (Colunas vistas: {list(df.columns[:5])})")
                    continue

                # Processamento normal
                df = df.dropna(subset=['LOTE', 'MASSA'])
                df['LOTE'] = df['LOTE'].astype(str).str.strip().str.upper()
                df['MASSA'] = df['MASSA'].astype(str).str.strip()
                df = df[df['LOTE'].str.len() > 2] # Remove lixo
                
                dict_aba = pd.Series(df.MASSA.values, index=df.LOTE).to_dict()
                mapa_lote_massa.update(dict_aba)
                print(f"✅ '{aba}': {len(dict_aba)} lotes.")
                
            except ValueError:
                # print(f"ℹ️ Aba '{aba}' não existe.") # Silencioso para não poluir
                pass
            except Exception as e:
                print(f"❌ Erro na aba '{aba}': {e}")
                
    finally:
        if os.path.exists(caminho_clone):
            try: os.remove(caminho_clone)
            except: pass

    print(f"--- 🏁 Sucesso: {len(mapa_lote_massa)} lotes carregados. ---")
    return mapa_lote_massa

if __name__ == "__main__":
    dicionario = carregar_dicionario_lotes()
    
    # Teste com o lote que deu erro antes
    lote = "9215"
    print(f"\n🧪 Teste Lote {lote}: {dicionario.get(lote, 'NÃO ENCONTRADO')}")