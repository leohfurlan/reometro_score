from connection import connect_to_database
import pandas as pd

query = '''SELECT
    COD_ENSAIO,
    NOME,
    COD_GRUPO,
    NUMERO_LOTE,
    CODIGO,
    DATA,
    AMOSTRA,
    BATCH,
    MAXIMO_TEMPO,
    TEMP_PLATO_INF,
    T2TEMPO,
    T90TEMPO,
    VISCOSIDADEFINALTORQUE
FROM
    dbo.ENSAIO
WHERE
    DATA >= '2025-09-01'
'''

cursor = connect_to_database().cursor()
cursor.execute(query)
rows = cursor.fetchall()
columns = [column[0] for column in cursor.description]
df_ensaios = pd.DataFrame.from_records(rows, columns=columns)
#print(df_ensaios.info())
cursor.close()

def valida_reometria(cod_ensaio):
    df_filtro = df_ensaios[df_ensaios['COD_ENSAIO'] == cod_ensaio]
    if df_filtro.empty:
        return False
    ts2tempo = df_filtro['T2TEMPO'].item()
    t90tempo = df_filtro['T90TEMPO'].item()
    print(ts2tempo, t90tempo)
    if ts2tempo <= 0 or t90tempo <= 0:
        return False
    if t90tempo <= ts2tempo:
        return False
    return True
    

print(valida_reometria(437441))  # Exemplo de uso
