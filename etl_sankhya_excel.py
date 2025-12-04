import pandas as pd
import os
import warnings

# Ignora avisos do Excel
warnings.simplefilter("ignore")

def carregar_mapa_sankhya_xls(caminho_arquivo):
    """
    Lê a planilha do Sankhya e retorna um dicionário:
    { "NUMERO_DO_LOTE_STRING": INT_CODIGO_PRODUTO }
    Ex: { "9493": 7762, "9481": 24768 }
    """
    print(f"--- 🏭 ETL SANKHYA: Lendo arquivo de produção... ---")
    
    if not os.path.exists(caminho_arquivo):
        print(f"❌ ERRO: Arquivo Sankhya não encontrado em: {caminho_arquivo}")
        return {}

    try:
        # Tenta ler o Excel. 
        # IMPORTANTE: Verifique se a linha do cabeçalho é a 0 ou 1.
        # Ajuste 'usecols' se souber as colunas exatas para economizar memória.
        df = pd.read_excel(caminho_arquivo, engine='openpyxl')
        
        # --- PADRONIZAÇÃO DE COLUNAS ---
        # Coloque aqui os nomes EXATOS das colunas da sua planilha Sankhya
        # Vou chutar nomes comuns, mas você deve ajustar:
        col_lote = 'NÚMERO DO LOTE'  # ou 'LOTE' ou 'NRO. LOTE'
        col_cod = 'CÓD. PRODUTO'     # ou 'CODPROD' ou 'CODIGO'
        
        # Normaliza colunas do DF para maiúsculo para facilitar busca
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Tenta encontrar as colunas automaticamente
        mapa_cols = {}
        for c in df.columns:
            if 'LOTE' in c: mapa_cols['LOTE'] = c
            if 'COD' in c and 'PROD' in c: mapa_cols['CODIGO'] = c
            
        if 'LOTE' not in mapa_cols or 'CODIGO' not in mapa_cols:
            print(f"⚠️ Colunas não identificadas automaticamente. Vistas: {list(df.columns)}")
            return {}

        # Limpeza e Criação do Dicionário
        df_limpo = df.dropna(subset=[mapa_cols['LOTE'], mapa_cols['CODIGO']])
        
        # Converte lote para string limpa
        df_limpo['chave_lote'] = df_limpo[mapa_cols['LOTE']].astype(str).str.strip().str.upper()
        
        # Converte código para inteiro
        df_limpo['valor_cod'] = pd.to_numeric(df_limpo[mapa_cols['CODIGO']], errors='coerce')
        df_limpo = df_limpo.dropna(subset=['valor_cod']) # Remove se código for inválido
        
        # Cria o dicionário { LOTE: CODIGO }
        mapa_final = pd.Series(df_limpo.valor_cod.values, index=df_limpo.chave_lote).to_dict()
        
        # Converte valores para int puro (remove .0 se houver)
        mapa_final = {k: int(v) for k, v in mapa_final.items()}
        
        print(f"✅ Mapa Sankhya Carregado: {len(mapa_final)} vínculos encontrados.")
        return mapa_final

    except Exception as e:
        print(f"❌ Erro ao ler planilha Sankhya: {e}")
        return {}

# Teste rápido (só roda se executar o arquivo direto)
if __name__ == "__main__":
    # Substitua pelo caminho real para testar
    caminho = "Ordem_de_Producao (1).xlsx" 
    dic = carregar_mapa_sankhya_xls(caminho)
    print("Exemplo Lote 9493:", dic.get("9493", "Não achou"))