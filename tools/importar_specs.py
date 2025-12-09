import pandas as pd
import json
import os

ARQUIVO_REOMETRIA = "planilhas/dados_reometria_convertidos.xlsx"
ARQUIVO_VISCOSIDADE = "planilhas/dados_viscosidade_convertidos.xlsx"
ARQUIVO_JSON_DESTINO = "config_massas.json"

PESO_PADRAO = 10 

def carregar_json():
    if os.path.exists(ARQUIVO_JSON_DESTINO):
        with open(ARQUIVO_JSON_DESTINO, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def salvar_json(dados):
    with open(ARQUIVO_JSON_DESTINO, 'w', encoding='utf-8') as f:
        json.dump(dados, f, indent=4)
    print(f"💾 JSON salvo/atualizado com sucesso!")

def processar():
    print("--- 📥 IMPORTANDO DADOS (USANDO COLUNAS DE ALVO) ---")
    configs = carregar_json()
    
    # 1. REOMETRIA
    if os.path.exists(ARQUIVO_REOMETRIA):
        df = pd.read_excel(ARQUIVO_REOMETRIA)
        df = df.dropna(subset=['COD_SANKHYA'])
        
        for _, row in df.iterrows():
            cod = str(int(row['COD_SANKHYA']))
            perfil = str(row['TIPO_PERFIL']).lower()
            
            if cod not in configs: configs[cod] = {}
            
            if pd.notna(row['TEMP_PADRAO']):
                configs[cod][f"{perfil}_temp_padrao"] = float(row['TEMP_PADRAO'])
            if pd.notna(row['TEMPO_TOTAL_S']):
                configs[cod][f"{perfil}_tempo_total"] = float(row['TEMPO_TOTAL_S'])
                
            for param in ['TS2', 'T90']:
                min_v = row.get(f'{param}_MIN')
                max_v = row.get(f'{param}_MAX')
                alvo_v = row.get(f'{param}_ALVO') # Lê a coluna nova
                
                # Se não tiver Alvo no Excel (raro), calcula fallback
                if pd.isna(alvo_v) and pd.notna(min_v) and pd.notna(max_v):
                    alvo_v = (min_v + max_v) / 2
                
                if pd.notna(min_v) and pd.notna(max_v):
                    configs[cod][f"{perfil}_{param.title()}"] = {
                        "min": float(min_v), 
                        "max": float(max_v), 
                        "alvo": float(alvo_v), 
                        "peso": PESO_PADRAO
                    }
        print(f"✅ Reometria sincronizada.")

    # 2. VISCOSIDADE
    if os.path.exists(ARQUIVO_VISCOSIDADE):
        df = pd.read_excel(ARQUIVO_VISCOSIDADE)
        df = df.dropna(subset=['COD_SANKHYA'])
        
        for _, row in df.iterrows():
            cod = str(int(row['COD_SANKHYA']))
            if cod not in configs: configs[cod] = {}
            
            min_v = row.get('VISC_MIN')
            max_v = row.get('VISC_MAX')
            alvo_v = row.get('VISC_ALVO')
            
            if pd.isna(alvo_v) and pd.notna(min_v) and pd.notna(max_v):
                alvo_v = (min_v + max_v) / 2
            
            if pd.notna(min_v) and pd.notna(max_v):
                spec = {"min": float(min_v), "max": float(max_v), "alvo": float(alvo_v), "peso": PESO_PADRAO}
                configs[cod]["alta_Viscosidade"] = spec
                configs[cod]["baixa_Viscosidade"] = spec
        
        print(f"✅ Viscosidade sincronizada.")

    salvar_json(configs)



if __name__ == "__main__":
    processar()