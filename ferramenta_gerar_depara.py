import pandas as pd
from etl_planilha import carregar_dicionario_lotes
from services.sankhya_service import importar_catalogo_sankhya
from difflib import get_close_matches

def gerar_lista_para_correcao():
    print("--- 🛠️ GERADOR DE LISTA DE-PARA ---")
    
    # 1. Carrega tudo
    mapa_lotes_planilha = carregar_dicionario_lotes()
    _, catalogo_sankhya = importar_catalogo_sankhya()
    nomes_sankhya = list(catalogo_sankhya.keys())
    
    # 2. Identifica nomes únicos na planilha que NÃO estão no Sankhya
    nomes_planilha_unicos = set(mapa_lotes_planilha.values())
    nomes_desconhecidos = []
    
    print(f"   > Analisando {len(nomes_planilha_unicos)} nomes únicos da planilha...")
    
    for nome in nomes_planilha_unicos:
        nome_upper = nome.strip().upper()
        
        # Testes básicos que o sistema já faz
        if nome_upper in catalogo_sankhya: continue
        if ("MASSA " + nome_upper) in catalogo_sankhya: continue
        
        # Se chegou aqui, é um "Fantasma"
        # Tenta achar uma sugestão parecida para te ajudar
        sugestao = ""
        matches = get_close_matches(nome_upper, nomes_sankhya, n=1, cutoff=0.5)
        if matches:
            sugestao = matches[0]
            
        nomes_desconhecidos.append({
            "NOME_NA_PLANILHA": nome,
            "SUGESTAO_SANKHYA": sugestao,
            "NOME_CORRETO_SANKHYA": "" # Coluna para você preencher no Excel
        })
    
    # 3. Exporta para você trabalhar
    df = pd.DataFrame(nomes_desconhecidos)
    arquivo = "nomes_para_corrigir.xlsx"
    df.to_excel(arquivo, index=False)
    
    print(f"✅ Arquivo '{arquivo}' gerado com {len(df)} nomes para mapear.")
    print("   > Abra o Excel, preencha a coluna 'NOME_CORRETO_SANKHYA' e me avise.")

if __name__ == "__main__":
    gerar_lista_para_correcao()