import pandas as pd
import sys
import os

# Adiciona diretório raiz para importar conexão
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from connection import connect_to_database

def auditar_lote(lote_alvo, batch_alvo=None):
    print(f"\n--- 🕵️ AUDITORIA DE LOTE: {lote_alvo} (Batch: {batch_alvo}) ---")
    
    conn = connect_to_database()
    cursor = conn.cursor()
    
    # Busca tudo que existe no banco para esse lote
    query = '''
    SELECT 
        COD_ENSAIO, 
        DATA, 
        NUMERO_LOTE, 
        BATCH, 
        COD_GRUPO,
        T2TEMPO as Ts2,
        TEMP_PLATO_INF as Temp,
        CODIGO as Cod_Reometro
    FROM dbo.ENSAIO
    WHERE NUMERO_LOTE LIKE ?
    ORDER BY DATA DESC
    '''
    
    # O LIKE permite buscar mesmo se tiver espaços extras
    params = (f"%{lote_alvo}%",)
    
    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        colunas = [c[0] for c in cursor.description]
    finally:
        conn.close()

    if not rows:
        print("❌ Nenhum registro encontrado no banco de dados.")
        return

    df = pd.DataFrame([list(r) for r in rows], columns=colunas)
    
    # Filtra pelo Batch se fornecido
    if batch_alvo:
        try:
            df = df[df['BATCH'].astype(str) == str(batch_alvo)]
        except:
            pass

    print(f"\n✅ Encontrados {len(df)} registros brutos no SQL:")
    print("-" * 80)
    print(df[['COD_ENSAIO', 'DATA', 'BATCH', 'COD_GRUPO', 'Temp', 'Ts2']].to_string(index=False))
    print("-" * 80)

    # Análise de Duplicidade
    ids_unicos = df['COD_ENSAIO'].nunique()
    if ids_unicos == len(df):
        print("\nCONCLUSÃO: Não há duplicidade de ID. São testes distintos feitos para o mesmo lote.")
        print("O sistema está agrupando corretamente testes físicos diferentes.")
    else:
        print("\nCONCLUSÃO: ALERTA! Existem IDs duplicados ou linhas retornando múltiplas vezes.")

if __name__ == "__main__":
    # --- DIGITE AQUI O LOTE QUE QUER INVESTIGAR ---
    LOTE_TESTE = input("Digite o Número do Lote para auditar: ")
    BATCH_TESTE = input("Digite o Batch (opcional, ENTER para pular): ")
    
    auditar_lote(LOTE_TESTE, BATCH_TESTE)