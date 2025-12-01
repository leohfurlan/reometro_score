from flask import Flask, render_template
import pandas as pd
from models.massa import Massa
from models.ensaio import Ensaio

app = Flask(__name__)

# --- 1. CONFIGURAÇÃO (Mantida) ---
catalogo_massas = {}
massa_std = Massa(cod_sankhya=26791, descricao="MASSA CAMELBACK AGR STD")
massa_std.adicionar_parametro("Ts2", peso=8, alvo=60, minimo=40, maximo=80)
massa_std.adicionar_parametro("T90", peso=6, alvo=100, minimo=80, maximo=120)
massa_std.adicionar_parametro("Viscosidade", peso=10, alvo=63, minimo=56, maximo=70)
catalogo_massas[26791] = massa_std


def processar_dados():
    """Função que encapsula toda a lógica de leitura e cálculo"""
    try:
        df_geral = pd.read_excel('exemplo.xlsx', engine='openpyxl')
    except:
        return [] # Retorna lista vazia se der erro

    # Limpeza
    df_geral['NUMERO_LOTE'] = df_geral['NUMERO_LOTE'].astype(str).str.upper().str.replace(" ", "")
    df_geral['CODIGO'] = pd.to_numeric(df_geral['CODIGO'], errors='coerce')

    # Separação
    df_reometria = df_geral.dropna(subset=['T2TEMPO', 'T90TEMPO']).copy()
    df_reometria = df_reometria.dropna(subset=['CODIGO'])
    df_reometria['CODIGO'] = df_reometria['CODIGO'].astype(int)

    df_viscosidade = df_geral.dropna(subset=['VISCOSIDADEFINALTORQUE']).copy()
    
    # Mapa de Médias
    medias_por_lote = df_viscosidade.groupby('NUMERO_LOTE')['VISCOSIDADEFINALTORQUE'].mean().to_dict()

    lista_ensaios = []

    for index, linha_reo in df_reometria.iterrows():
        cod_material = linha_reo['CODIGO']
        lote_atual = linha_reo['NUMERO_LOTE']
        batch_atual = linha_reo['BATCH']
        
        if cod_material in catalogo_massas:
            massa_selecionada = catalogo_massas[cod_material]
            
            valor_visc = None
            origem = "N/A"

            # Busca Exata
            match_exato = df_viscosidade[
                (df_viscosidade['NUMERO_LOTE'] == lote_atual) & 
                (df_viscosidade['BATCH'] == batch_atual)
            ]
            
            if not match_exato.empty:
                valor_visc = match_exato.iloc[0]['VISCOSIDADEFINALTORQUE']
                origem = "Real" # Encurtei para caber melhor na tela
            elif lote_atual in medias_por_lote:
                valor_visc = medias_por_lote[lote_atual]
                origem = "Média"
            
            valores_medidos = {
                "Ts2": linha_reo['T2TEMPO'],
                "T90": linha_reo['T90TEMPO']
            }
            if valor_visc is not None:
                valores_medidos["Viscosidade"] = round(valor_visc,1)


            novo_ensaio = Ensaio(
                id_ensaio=linha_reo['COD_ENSAIO'],
                massa_objeto=massa_selecionada,
                valores_medidos=valores_medidos,
                lote=lote_atual,
                batch=batch_atual,
                origem_viscosidade=origem
            )
            # --- CORREÇÃO DO BATCH (INT vs FLOAT vs NAN) ---
            # Verifica se é um número válido (não é NaN/Vazio)
            if pd.notna(batch_atual):
                try:
                    # Tenta converter para inteiro
                    novo_ensaio.batch = int(batch_atual)
                except ValueError:
                    # Se for um texto (ex: "315A"), mantém como texto
                    novo_ensaio.batch = str(batch_atual)
            else:
                # Se for vazio (NaN), define como None ou "N/A" para o HTML tratar
                novo_ensaio.batch = None
            novo_ensaio.calcular_score()
            lista_ensaios.append(novo_ensaio)
            
    return lista_ensaios

# --- ROTA DO SITE ---
@app.route('/')
def dashboard():
    # 1. Processa os dados
    ensaios = processar_dados()
    
    # 2. Calcula KPIs para os Cards do topo
    total = len(ensaios)
    aprovados = sum(1 for e in ensaios if "PRIME" in e.acao_recomendada or e.acao_recomendada == "LIBERAR")
    ressalvas = sum(1 for e in ensaios if "RESSALVA" in e.acao_recomendada)
    reprovados = sum(1 for e in ensaios if "REPROVAR" in e.acao_recomendada or "CORTAR" in e.acao_recomendada)

    # 3. Envia tudo para o HTML
    return render_template('index.html', 
                           ensaios=ensaios,
                           kpi={'total': total, 'aprovados': aprovados, 'ressalvas': ressalvas, 'reprovados': reprovados})

if __name__ == '__main__':
    app.run(debug=True)