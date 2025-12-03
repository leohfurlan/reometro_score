import pandas as pd
from connection import connect_to_database as connect_sql_server
from connection_oracle import connect_to_oracle

def extrair_dados_lab():
    print("--- 1. Lendo dados do Laboratório (SQL Server) ---")
    conn = connect_sql_server()
    
    # Pegamos os dados e o LOTE
    query = '''
    SELECT 
        COD_ENSAIO, NUMERO_LOTE, BATCH, DATA, 
        T2TEMPO as Ts2, T90TEMPO as T90, VISCOSIDADEFINALTORQUE as Viscosidade
    FROM dbo.ENSAIO
    WHERE DATA >= '2025-09-01' -- Filtrar para não trazer o banco todo
    '''
    df_lab = pd.read_sql(query, conn)
    conn.close()
    
    # Limpeza básica do Lote para garantir o match (remover espaços, maiúsculas)
    df_lab['NUMERO_LOTE'] = df_lab['NUMERO_LOTE'].astype(str).str.strip().str.upper()
    print(f"   > Encontrados {len(df_lab)} ensaios.")
    return df_lab

def buscar_detalhes_erp(lista_lotes):
    print("--- 2. Buscando dados oficiais no Sankhya (Oracle) ---")
    
    if not lista_lotes:
        return pd.DataFrame()

    # Formata a lista para usar no SQL IN ('Lote1', 'Lote2', ...)
    # O Oracle tem limite de 1000 itens no IN, em produção faríamos em chunks,
    # mas para teste isso serve.
    lotes_formatados = "', '".join(lista_lotes[:999]) 
    
    # --- QUERY DO SANKHYA ---
    # PRECISAS AJUSTAR ESTA QUERY PARA AS TABELAS REAIS DO SEU SANKHYA.
    # Geralmente a tabela de Lote é a TPRLOTE ou similar, e o Produto é TGFPRO.
    # Estou assumindo uma estrutura genérica aqui.
    query_oracle = f'''
    SELECT 
        LOTE as NUMERO_LOTE, -- Nome tem que bater para o merge
        CODPROD as COD_SANKHYA,
        DESCRPROD as DESCRICAO_OFICIAL
    FROM 
        -- [ATENÇÃO] Substitua pelas tabelas e joins reais do Sankhya
        VIEW_ESTOQUE_LOTE_PRODUTO 
    WHERE 
        LOTE IN ('{lotes_formatados}')
    '''
    
    conn = connect_to_oracle()
    try:
        df_erp = pd.read_sql(query_oracle, conn)
    except Exception as e:
        print(f"Erro no Oracle: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

    # Garantir formatação para o match
    if not df_erp.empty:
        df_erp['NUMERO_LOTE'] = df_erp['NUMERO_LOTE'].astype(str).str.strip().str.upper()
    
    print(f"   > Detalhes encontrados para {len(df_erp)} lotes.")
    return df_erp

def executar_consolidacao():
    # 1. Pega o Lab
    df_lab = extrair_dados_lab()
    
    # 2. Extrai os lotes únicos para pesquisar no Oracle
    lotes_unicos = df_lab['NUMERO_LOTE'].unique().tolist()
    
    # 3. Pega o Sankhya
    df_erp = buscar_detalhes_erp(lotes_unicos)
    
    if df_erp.empty:
        print("⚠️ Não foi possível recuperar dados do Oracle. Abortando merge.")
        return

    # 4. O MATCH (Left Join)
    # Mantemos todos os dados do Lab (left), e trazemos info do ERP onde houver match
    print("--- 3. Cruzando informações (Match) ---")
    df_consolidado = pd.merge(df_lab, df_erp, on='NUMERO_LOTE', how='left')
    
    # 5. Análise do Resultado
    total = len(df_consolidado)
    matches = df_consolidado['COD_SANKHYA'].notna().sum()
    print(f"--- RELATÓRIO FINAL ---")
    print(f"Total de Ensaios: {total}")
    print(f"Ensaios com Match no ERP: {matches} ({(matches/total)*100:.1f}%)")
    print(f"Ensaios sem Match (Lote não achado): {total - matches}")
    
    # Exemplo dos dados consolidados
    print("\nExemplo de dados consolidados:")
    print(df_consolidado[['COD_ENSAIO', 'NUMERO_LOTE', 'COD_SANKHYA', 'DESCRICAO_OFICIAL']].head())

    # --- PRÓXIMO PASSO: SALVAR ---
    # df_consolidado.to_sql('ENSAIO_CONSOLIDADO', con=engine_sql_server) 
    # Ou salvar num .csv temporário para o dashboard ler
    df_consolidado.to_csv('cache_dados_consolidados.csv', index=False)
    print("\nArquivo 'cache_dados_consolidados.csv' gerado com sucesso.")

if __name__ == "__main__":
    executar_consolidacao()