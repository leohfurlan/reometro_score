from collections import defaultdict
import statistics
from datetime import datetime

def gerar_estrutura_relatorio(lista_ensaios, busca='', ordenar_por='nome', ordem='asc'):
    arvore = {}
    
    for ensaio in lista_ensaios:
        nome_massa = ensaio.massa.descricao
        cod_sankhya = ensaio.massa.cod_sankhya
        
        if busca:
            termo = busca.upper()
            if termo not in nome_massa.upper() and termo not in str(cod_sankhya):
                continue

        if nome_massa not in arvore:
            arvore[nome_massa] = {
                'cod': cod_sankhya,
                'nome': nome_massa,
                'lotes': {},
                'kpi': {
                    'total_batches': 0, 'aprovados': 0, 'score_soma': 0, 'score_medio': 0, 'taxa_aprovacao': 0
                },
                'stats_raw': {'visc': [], 'alta': {'ts2': [], 't90': []}, 'baixa': {'ts2': [], 't90': []}},
                'medias_gerais': {}
            }
            
        massa_node = arvore[nome_massa]
        lote_key = ensaio.lote
        
        if lote_key not in massa_node['lotes']:
            massa_node['lotes'][lote_key] = {
                'numero': lote_key,
                'batches': [],
                'medias': ensaio.medias_lote,
                'data_recente': ensaio.data_hora,
                # NOVOS CAMPOS PARA O LOTE
                'kpi_lote': {'total': 0, 'aprovados': 0, 'score_soma': 0, 'reprovados': 0, 'ressalvas': 0}
            }
            
        lote_node = massa_node['lotes'][lote_key]
        lote_node['batches'].append(ensaio)
        
        # KPI DO LOTE
        lote_node['kpi_lote']['total'] += 1
        lote_node['kpi_lote']['score_soma'] += ensaio.score_final
        
        if "PRIME" in ensaio.acao_recomendada or "LIBERAR" == ensaio.acao_recomendada:
            lote_node['kpi_lote']['aprovados'] += 1
        elif "REPROVAR" in ensaio.acao_recomendada:
            lote_node['kpi_lote']['reprovados'] += 1
        else:
            lote_node['kpi_lote']['ressalvas'] += 1

        if ensaio.data_hora > lote_node['data_recente']:
            lote_node['data_recente'] = ensaio.data_hora
        
        # KPI DA MASSA
        massa_node['kpi']['total_batches'] += 1
        massa_node['kpi']['score_soma'] += ensaio.score_final
        if "LIBERAR" in ensaio.acao_recomendada:
            massa_node['kpi']['aprovados'] += 1

        # Coleta de Stats (igual anterior)
        vals = ensaio.valores_medidos
        temp = ensaio.temp_plato or 0
        contexto = 'alta' if temp >= 175 else 'baixa'
        if vals.get('Ts2'): massa_node['stats_raw'][contexto]['ts2'].append(vals['Ts2'])
        if vals.get('T90'): massa_node['stats_raw'][contexto]['t90'].append(vals['T90'])
        if vals.get('Viscosidade'): massa_node['stats_raw']['visc'].append(vals['Viscosidade'])

    # Finalização
    lista_final = []
    
    for massa_node in arvore.values():
        # KPIs Massa
        total = massa_node['kpi']['total_batches']
        if total > 0:
            massa_node['kpi']['score_medio'] = massa_node['kpi']['score_soma'] / total
            massa_node['kpi']['taxa_aprovacao'] = (massa_node['kpi']['aprovados'] / total) * 100
        
        # FINALIZAÇÃO KPIs DOS LOTES (Cálculo de médias individuais)
        for lote in massa_node['lotes'].values():
            qtd_lote = lote['kpi_lote']['total']
            if qtd_lote > 0:
                lote['kpi_lote']['score_medio'] = lote['kpi_lote']['score_soma'] / qtd_lote
                lote['kpi_lote']['taxa_aprovacao'] = (lote['kpi_lote']['aprovados'] / qtd_lote) * 100
            else:
                lote['kpi_lote']['score_medio'] = 0
                lote['kpi_lote']['taxa_aprovacao'] = 0

        # Ordenação padrão dos lotes: mais recente primeiro (data_recente DESC)
        massa_node['lotes'] = dict(
            sorted(
                massa_node['lotes'].items(),
                key=lambda kv: kv[1].get('data_recente') or datetime.min,
                reverse=True,
            )
        )

        # Médias Gerais Massa (igual anterior)
        def safe_mean(lista): return statistics.mean(lista) if lista else None
        raw = massa_node['stats_raw']
        massa_node['medias_gerais'] = {
            'visc': safe_mean(raw['visc']),
            'alta_ts2': safe_mean(raw['alta']['ts2']), 'alta_t90': safe_mean(raw['alta']['t90']),
            'baixa_ts2': safe_mean(raw['baixa']['ts2']), 'baixa_t90': safe_mean(raw['baixa']['t90']),
            'qtd_alta': len(raw['alta']['ts2']) + len(raw['alta']['t90']),
            'qtd_baixa': len(raw['baixa']['ts2']) + len(raw['baixa']['t90'])
        }
        massa_node['qtd_lotes_unicos'] = len(massa_node['lotes'])
        lista_final.append(massa_node)

    # Ordenação (igual anterior)
    reverse = (ordem == 'desc')
    if ordenar_por == 'aprovacao': lista_final.sort(key=lambda x: x['kpi']['taxa_aprovacao'], reverse=reverse)
    elif ordenar_por == 'score': lista_final.sort(key=lambda x: x['kpi']['score_medio'], reverse=reverse)
    elif ordenar_por == 'cod': lista_final.sort(key=lambda x: x['cod'], reverse=reverse)
    else: lista_final.sort(key=lambda x: x['nome'], reverse=reverse)
            
    return lista_final
