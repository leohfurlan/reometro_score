import pandas as pd
import json
import os

# --- CONFIGURAÇÃO ---
# Coloque aqui o caminho exato da sua planilha de exemplo
CAMINHO_PLANILHA = r"C:\Users\leonardo.furlan\Documents\producao_out_hoje.xlsx" # AJUSTE AQUI
ARQUIVO_SAIDA = "mapa_lotes_cache.json"

def gerar_cache():
    print(f"--- 🔄 Lendo planilha: {CAMINHO_PLANILHA} ---")
    
    if not os.path.exists(CAMINHO_PLANILHA):
        print("❌ Arquivo não encontrado!")
        return

    try:
        # Lê o Excel
        df = pd.read_excel(CAMINHO_PLANILHA, engine='openpyxl')
        
        # AJUSTE OS NOMES DAS COLUNAS AQUI CONFORME SEU EXCEL REAL
        # Vou tentar adivinhar nomes comuns, mas verifique no seu arquivo
        col_lote = 'NÚMERO DO LOTE' # Ex: 'Lote', 'Nr Lote'
        col_cod = 'CÓD. PRODUTO'    # Ex: 'Cód.', 'Código'

        # Normaliza nomes das colunas para maiúsculo para facilitar
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Tenta achar as colunas automaticamente
        match_lote = next((c for c in df.columns if 'LOTE' in c), None)
        match_cod = next((c for c in df.columns if 'COD' in c and 'PROD' in c), None)

        if not match_lote or not match_cod:
            print(f"⚠️ Colunas não encontradas. Colunas disponíveis: {list(df.columns)}")
            print(">> Edite o script e coloque o nome exato das colunas.")
            return

        print(f"   > Coluna Lote: {match_lote}")
        print(f"   > Coluna Código: {match_cod}")

        # Limpeza
        df = df.dropna(subset=[match_lote, match_cod])
        
        mapa = {}
        for _, row in df.iterrows():
            lote_sujo = str(row[match_lote]).strip().upper()
            
            # Limpeza fina do lote (remove .0 se for número que virou texto)
            if lote_sujo.endswith('.0'): lote_sujo = lote_sujo[:-2]
            
            try:
                cod = int(float(row[match_cod])) # Garante que vira inteiro
                mapa[lote_sujo] = cod
            except:
                continue

        # Salva em JSON
        with open(ARQUIVO_SAIDA, 'w') as f:
            json.dump(mapa, f)
            
        print(f"✅ Sucesso! Arquivo '{ARQUIVO_SAIDA}' gerado com {len(mapa)} vínculos.")
        print("Agora reinicie o Flask (app.py) para ele ler este arquivo.")

    except Exception as e:
        print(f"❌ Erro: {e}")

if __name__ == "__main__":
    gerar_cache()