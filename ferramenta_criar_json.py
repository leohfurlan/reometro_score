import pandas as pd
import json
import os

ARQUIVO_EXCEL = "nomes_para_corrigir.xlsx"
ARQUIVO_JSON_SAIDA = "de_para_massas.json"

def gerar_dicionario_correcao():
    print("--- ⚙️ GERADOR DE DICIONÁRIO DE CORREÇÃO ---")
    
    if not os.path.exists(ARQUIVO_EXCEL):
        print(f"❌ Erro: Não encontrei o arquivo '{ARQUIVO_EXCEL}'.")
        return

    try:
        # Lê o Excel preenchido por si
        df = pd.read_excel(ARQUIVO_EXCEL)
        
        # Filtra apenas onde você preencheu a coluna 'NOME_CORRETO_SANKHYA'
        # Removemos linhas vazias ou NaN
        df_validos = df.dropna(subset=['NOME_CORRETO_SANKHYA'])
        
        dicionario = {}
        count = 0
        
        for _, row in df_validos.iterrows():
            nome_planilha = str(row['NOME_NA_PLANILHA']).strip().upper()
            nome_oficial = str(row['NOME_CORRETO_SANKHYA']).strip().upper()
            
            # Só adiciona se o nome for válido
            if nome_oficial and nome_oficial != 'NAN':
                dicionario[nome_planilha] = nome_oficial
                count += 1
        
        # Salva em JSON
        with open(ARQUIVO_JSON_SAIDA, 'w', encoding='utf-8') as f:
            json.dump(dicionario, f, indent=4, ensure_ascii=False)
            
        print(f"✅ Sucesso! {count} correções mapeadas em '{ARQUIVO_JSON_SAIDA}'.")
        print("   Agora o sistema sabe traduzir nomes da planilha para o Sankhya.")
        
    except Exception as e:
        print(f"❌ Erro ao ler Excel: {e}")

if __name__ == "__main__":
    gerar_dicionario_correcao()