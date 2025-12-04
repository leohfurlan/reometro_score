import pandas as pd
from connection import connect_to_database
from datetime import datetime

# --- CONFIGURAÇÃO ---
# Vamos olhar um período longo para ter certeza estatística
DATA_ANALISE = '2025-06-01' 
ARQUIVO_SAIDA = "mapa_tipo_equipamentos.xlsx"

def classificar_grupos():
    print(f"--- 🕵️ CLASSIFICADOR DE GRUPOS (Desde {DATA_ANALISE}) ---")
    
    conn = connect_to_database()
    
    # Query que traz os dados técnicos para decidirmos o tipo
    query = '''
    SELECT 
        COD_GRUPO,
        COUNT(*) as Total_Ensaios,
        
        -- Contagem de quantos tiveram dados de Reometria
        SUM(CASE WHEN T2TEMPO > 0 OR T90TEMPO > 0 THEN 1 ELSE 0 END) as Qtd_Tem_Reometria,
        
        -- Contagem de quantos tiveram dados de Viscosidade
        SUM(CASE WHEN VISCOSIDADEFINALTORQUE > 0 THEN 1 ELSE 0 END) as Qtd_Tem_Viscosidade,
        
        -- Exemplo de nome para ajudar na identificação visual
        MAX(AMOSTRA) as Exemplo_Amostra
    FROM dbo.ENSAIO
    WHERE DATA >= ?
    GROUP BY COD_GRUPO
    ORDER BY Total_Ensaios DESC
    '''
    
    print("   > Executando análise estatística no SQL...")
    df = pd.read_sql(query, conn, params=(datetime.strptime(DATA_ANALISE, '%Y-%m-%d'),))
    conn.close()
    
    lista_classificacao = []
    
    print("   > Definindo Tipo de Equipamento...")
    for index, row in df.iterrows():
        total = row['Total_Ensaios']
        qtd_reo = row['Qtd_Tem_Reometria']
        qtd_visc = row['Qtd_Tem_Viscosidade']
        
        # Lógica de Classificação
        tipo = "INDEFINIDO"
        confianca = "Baixa"
        
        # Se mais de 90% dos testes têm Ts2/T90, é Reômetro com certeza
        if (qtd_reo / total) > 0.9:
            tipo = "REOMETRO"
            confianca = "Alta"
        
        # Se tem muita Viscosidade mas quase nenhuma Reometria (Ex: Mooney)
        elif (qtd_visc / total) > 0.9 and (qtd_reo / total) < 0.1:
            tipo = "VISCOSIMETRO"
            confianca = "Alta"
            
        # Casos Híbridos (MDR que mede viscosidade também?)
        elif qtd_reo > 0 and qtd_visc > 0:
            tipo = "HÍBRIDO / REOMETRO" # Geralmente assumimos Reômetro pois é mais completo
            confianca = "Média"
            
        # Grupos vazios ou de teste
        elif qtd_reo == 0 and qtd_visc == 0:
            tipo = "SEM DADOS (LIXO)"
        
        lista_classificacao.append({
            "COD_GRUPO": row['COD_GRUPO'],
            "TIPO SUGERIDO": tipo,
            "Confiança": confianca,
            "Total Ensaios": total,
            "% Reometria": f"{(qtd_reo/total)*100:.1f}%",
            "% Viscosidade": f"{(qtd_visc/total)*100:.1f}%",
            "Exemplo Amostra": row['Exemplo_Amostra']
        })

    # Exportação
    df_final = pd.DataFrame(lista_classificacao)
    
    print(f"--- Gerando Excel: {ARQUIVO_SAIDA} ---")
    df_final.to_excel(ARQUIVO_SAIDA, index=False)
    print(f"✅ Mapa gerado! Use este arquivo para configurar o sistema.")
    print("\nResumo:")
    print(df_final[['COD_GRUPO', 'TIPO SUGERIDO', 'Total Ensaios']].head(10).to_string(index=False))

if __name__ == "__main__":
    classificar_grupos()