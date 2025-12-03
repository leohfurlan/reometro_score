from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import math, re

# Modelos
from models.massa import Massa
from models.ensaio import Ensaio

# Serviços
from connection import connect_to_database      # SQL Lab
from etl_planilha import carregar_dicionario_lotes # Excel Lotes
from services.sankhya_service import importar_catalogo_sankhya # Oracle Catálogo com códigos e nomes
from services.config_manager import aplicar_configuracoes_no_catalogo, salvar_configuracao

app = Flask(__name__)

# ==========================================
# 1. INICIALIZAÇÃO DO SISTEMA (CACHE)
# ==========================================
print("\n=== INICIANDO SISTEMA REOSCORE ===")

# A. Carrega o Catálogo Oficial do Sankhya
CATALOGO_POR_CODIGO, CATALOGO_POR_NOME = importar_catalogo_sankhya()


# B. [MODIFICADO] Carrega configurações do JSON em vez de hardcoded
print("--- Aplicando configurações de especificações (JSON) ---")
aplicar_configuracoes_no_catalogo(CATALOGO_POR_CODIGO)

# Como o Sankhya só traz o nome, precisamos "enriquecer" as massas com os alvos do Reômetro
def configurar_especificacoes():
    # Exemplo: Se o produto 26791 veio do Sankhya, adicionamos os limites nele
    if 26791 in CATALOGO_POR_CODIGO:
        m = CATALOGO_POR_CODIGO[26791]
        # Garante que é uma instância de Massa antes de adicionar parâmetros
        if isinstance(m, Massa):
            m.adicionar_parametro("Ts2", peso=8, alvo=60, minimo=40, maximo=80)
            m.adicionar_parametro("T90", peso=6, alvo=100, minimo=80, maximo=120)
            m.adicionar_parametro("Viscosidade", peso=10, alvo=63, minimo=56, maximo=70)
            print(f"   > Especificações aplicadas para: {m.descricao}")

configurar_especificacoes()

# C. Carrega o Mapa de Lotes (Excel da Rede)
MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()

CACHE_ENSAIOS = None

# ==========================================
# 2. LÓGICA DE NEGÓCIO
# ==========================================

def carregar_dados_do_banco(page=1, limit=20, filtros=None):
    print("--- Atualizando dados do SQL Server (Com Médias)... ---")
    filtros = filtros or {}
    page = max(1, page)
    limit = max(1, limit)

    search_term = filtros.get('search_term', '').strip().upper()
    filter_material = filtros.get('filter_material', '')
    filter_codigo = filtros.get('filter_codigo', '').strip()
    sort_by = filtros.get('sort_by', 'data')
    order = filtros.get('order', 'desc')
    date_start = filtros.get('date_start', '')
    date_end = filtros.get('date_end', '')

    where_clauses = []
    params = []

    data_inicial_padrao = datetime(2024, 1, 1)
    if date_start:
        try:
            dt_start = datetime.strptime(date_start, '%Y-%m-%d')
            where_clauses.append("DATA >= ?")
            params.append(dt_start)
        except:
            where_clauses.append("DATA >= ?")
            params.append(data_inicial_padrao)
    else:
        where_clauses.append("DATA >= ?")
        params.append(data_inicial_padrao)

    if date_end:
        try:
            dt_end = datetime.strptime(date_end, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            where_clauses.append("DATA <= ?")
            params.append(dt_end)
        except:
            pass

    if search_term:
        like_pattern = f"%{search_term}%"
        where_clauses.append("(UPPER(NUMERO_LOTE) LIKE ? OR CAST(BATCH AS NVARCHAR(50)) LIKE ? OR CAST(COD_ENSAIO AS NVARCHAR(50)) LIKE ?)")
        params.extend([like_pattern, like_pattern, like_pattern])

    if filter_codigo:
        codigo_param = filter_codigo
        try:
            codigo_param = int(filter_codigo)
        except:
            pass
        where_clauses.append("CODIGO = ?")
        params.append(codigo_param)
    elif filter_material:
        codigo_encontrado = None
        for cod, massa in CATALOGO_POR_CODIGO.items():
            if massa.descricao == filter_material:
                codigo_encontrado = cod
                break
        if codigo_encontrado:
            where_clauses.append("CODIGO = ?")
            params.append(codigo_encontrado)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sort_column_map = {
        'id': 'COD_ENSAIO',
        'data': 'DATA',
        'lote': 'NUMERO_LOTE',
        'ts2': 'T2TEMPO',
        't90': 'T90TEMPO',
        'visc': 'VISCOSIDADEFINALTORQUE'
    }
    order_column = sort_column_map.get(sort_by, 'DATA')
    order_direction = 'DESC' if order == 'desc' else 'ASC'

    total_registros = 0
    pagina_real = page

    resultados = []
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()

        count_query = f"SELECT COUNT(*) FROM dbo.ENSAIO {where_sql}"
        cursor.execute(count_query, params)
        total_registros = cursor.fetchone()[0] or 0

        total_paginas = max(1, math.ceil(total_registros / limit)) if total_registros else 1
        pagina_real = min(page, total_paginas)
        offset = (pagina_real - 1) * limit

        query = f'''
        SELECT 
            COD_ENSAIO, NUMERO_LOTE, BATCH, DATA, 
            T2TEMPO as Ts2, T90TEMPO as T90, VISCOSIDADEFINALTORQUE as Viscosidade, CODIGO,
            TEMP_PLATO_INF, COD_GRUPO, MAXIMO_TEMPO
        FROM dbo.ENSAIO 
        {where_sql}
        ORDER BY {order_column} {order_direction}
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        '''
        cursor.execute(query, params + [offset, limit])
        colunas = [c[0] for c in cursor.description]
        resultados = [dict(zip(colunas, row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"❌ Erro SQL: {e}")
        if conn: conn.close()
        return [], 0, pagina_real
    finally:
        if conn:
            conn.close()

    # --- 1. ETAPA DE AGRUPAMENTO (MERGE LOTE+BATCH) ---
    dados_agrupados = {}
    cache_limpeza_lote = {}
    
    def safe_float(val):
        if val is None: return 0.0
        try: return float(val)
        except: return 0.0

    print(f"   > Processando {len(resultados)} linhas brutas...")

    for row in resultados:
        # A. Limpeza do Lote
        lote_sujo = str(row['NUMERO_LOTE']).strip().upper()
        lote_final_match = None
        
        if lote_sujo in cache_limpeza_lote:
            lote_final_match = cache_limpeza_lote[lote_sujo]
        else:
            grupos_numericos = re.findall(r'\d+', lote_sujo)
            for grupo in reversed(grupos_numericos):
                candidato_base = grupo.lstrip('0')
                if not candidato_base: continue 
                
                temp = candidato_base
                achou = False
                while len(temp) >= 2:
                    if temp in MAPA_LOTES_PLANILHA:
                        lote_final_match = temp
                        achou = True
                        break
                    temp = temp[:-1]
                if achou: break 
            cache_limpeza_lote[lote_sujo] = lote_final_match

        chave_lote = lote_final_match if lote_final_match else lote_sujo
        try: chave_batch = int(row['BATCH']) if row['BATCH'] is not None else 0
        except: chave_batch = str(row['BATCH'])
        
        chave_unica = (chave_lote, chave_batch)

        # B. Merge de Dados
        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'row_base': row,
                'lote_real': chave_lote,
                'Ts2': safe_float(row['Ts2']),
                'T90': safe_float(row['T90']),
                'Viscosidade': safe_float(row['Viscosidade']),
                'Temp': safe_float(row['TEMP_PLATO_INF']),
                'Grupo': safe_float(row['COD_GRUPO']),
                'TempoMax': safe_float(row['MAXIMO_TEMPO']),
                'OrigemVisc': "Real" if safe_float(row['Viscosidade']) > 0 else "N/A"
            }
        else:
            registro = dados_agrupados[chave_unica]
            visc_atual = safe_float(row['Viscosidade'])
            if registro['Viscosidade'] == 0 and visc_atual > 0:
                registro['Viscosidade'] = visc_atual
                registro['OrigemVisc'] = "Real (Merge)"
            
            ts2_atual = safe_float(row['Ts2'])
            if registro['Ts2'] == 0 and ts2_atual > 0:
                registro['Ts2'] = ts2_atual
                registro['T90'] = safe_float(row['T90'])
                if registro['Temp'] == 0: registro['Temp'] = safe_float(row['TEMP_PLATO_INF'])
                if registro['Grupo'] == 0: registro['Grupo'] = safe_float(row['COD_GRUPO'])
                if registro['TempoMax'] == 0: registro['TempoMax'] = safe_float(row['MAXIMO_TEMPO'])
                registro['row_base'] = row 

    # --- 1.5 CÁLCULO DAS MÉDIAS POR LOTE ---
    # Agora que agrupamos, vamos calcular a média de cada lote
    mapa_medias_visc = {} # { 'LOTE_XYZ': 65.5 }
    acumulador_medias = {} # { 'LOTE_XYZ': [65, 66, 64] }
    
    for dados in dados_agrupados.values():
        lote = dados['lote_real']
        v = dados['Viscosidade']
        if v > 0:
            if lote not in acumulador_medias: acumulador_medias[lote] = []
            acumulador_medias[lote].append(v)
            
    for lote, valores in acumulador_medias.items():
        mapa_medias_visc[lote] = sum(valores) / len(valores)

    print(f"   > Médias calculadas para {len(mapa_medias_visc)} lotes.")

    # --- 2. CRIAÇÃO DE OBJETOS ---
    lista_ensaios = []
    cache_resolucao_nomes = {}
    
    for (lote_chave, batch_chave), dados in dados_agrupados.items():
        row = dados['row_base']
        
        # Identificação (Igual ao anterior)
        produto_identificado = None
        nome_planilha = MAPA_LOTES_PLANILHA.get(lote_chave)

        if nome_planilha:
            nome_sujo = nome_planilha.strip().upper()
            if nome_sujo in cache_resolucao_nomes:
                produto_identificado = cache_resolucao_nomes[nome_sujo]
            else:
                produto_identificado = CATALOGO_POR_NOME.get(nome_sujo)
                if not produto_identificado: produto_identificado = CATALOGO_POR_NOME.get("MASSA " + nome_sujo)
                if not produto_identificado:
                    for ns, obj in CATALOGO_POR_NOME.items():
                        if (nome_sujo in ns) or (ns in nome_sujo):
                            produto_identificado = obj; break
                cache_resolucao_nomes[nome_sujo] = produto_identificado

        if not produto_identificado:
            produto_identificado = CATALOGO_POR_CODIGO.get(row['CODIGO'])

        if produto_identificado and isinstance(produto_identificado, Massa):
            
            # --- 3. APLICAÇÃO INTELIGENTE DA VISCOSIDADE ---
            visc_final = dados['Viscosidade']
            origem_final = dados['OrigemVisc']
            
            # Se não tem valor real, tenta usar a média do lote
            if visc_final == 0 and lote_chave in mapa_medias_visc:
                visc_final = mapa_medias_visc[lote_chave]
                origem_final = "Média (Lote)"
            
            valores = {"Ts2": dados['Ts2'], "T90": dados['T90']}
            
            # Só adiciona no dicionário se tiver valor > 0
            if visc_final > 0:
                valores["Viscosidade"] = visc_final

            novo_ensaio = Ensaio(
                id_ensaio=row['COD_ENSAIO'],
                massa_objeto=produto_identificado,
                valores_medidos=valores,
                lote=lote_chave, 
                batch=batch_chave,
                data_hora=row['DATA'],
                origem_viscosidade=origem_final, # Passa a origem correta
                temp_plato=dados['Temp'],
                cod_grupo=int(dados['Grupo']),
                tempo_maximo=dados['TempoMax']
            )
            
            novo_ensaio.calcular_score()
            lista_ensaios.append(novo_ensaio)
            
    lista_ensaios.sort(key=lambda x: x.data_hora if x.data_hora else datetime.min, reverse=True)
    print(f"--- Processamento Concluído: {len(lista_ensaios)} ensaios finais. ---")
    return lista_ensaios, total_registros, pagina_real

# ==========================================
# 3. INTERFACE WEB
# ==========================================
@app.route('/')
def dashboard():
    LIMITE_POR_PAGINA = 20
    
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    
    search_term = request.args.get('search', '').strip().upper()
    filter_acao = request.args.get('acao_filter', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    filter_material = request.args.get('material_filter', '')
    filter_codigo = request.args.get('codigo_filter', '').strip()

    filtros_sql = {
        'search_term': search_term,
        'filter_material': filter_material,
        'filter_codigo': filter_codigo,
        'sort_by': sort_by,
        'order': order,
        'date_start': date_start,
        'date_end': date_end
    }

    ensaios_paginados, total_registros, pagina_real = carregar_dados_do_banco(
        page=page,
        limit=LIMITE_POR_PAGINA,
        filtros=filtros_sql
    )

    ensaios_filtrados = ensaios_paginados

    if filter_acao:
        if filter_acao == "APROVADOS":
            ensaios_filtrados = [e for e in ensaios_filtrados if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada]
        elif filter_acao == "RESSALVA":
            ensaios_filtrados = [e for e in ensaios_filtrados if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada]
        elif filter_acao == "REPROVADO":
            ensaios_filtrados = [e for e in ensaios_filtrados if "REPROVAR" in e.acao_recomendada]

    reverse_order = (order == 'desc')
    key_funcs = {
        'id': lambda x: x.id_ensaio,
        'data': lambda x: x.data_hora if x.data_hora else datetime.min,
        'lote': lambda x: x.lote,
        'score': lambda x: x.score_final,
        'ts2': lambda x: x.valores_medidos.get('Ts2', -1),
        't90': lambda x: x.valores_medidos.get('T90', -1),
        'visc': lambda x: x.valores_medidos.get('Viscosidade', -1),
        'acao': lambda x: x.acao_recomendada,
        'material': lambda x: x.massa.descricao
    }

    campos_ordenados_no_sql = {'id', 'data', 'lote', 'ts2', 't90', 'visc'}
    if sort_by not in campos_ordenados_no_sql and sort_by in key_funcs:
        ensaios_filtrados.sort(key=key_funcs[sort_by], reverse=reverse_order)

    kpi_global = {
        'total': total_registros,
        'aprovados': sum(1 for e in ensaios_filtrados if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada),
        'ressalvas': sum(1 for e in ensaios_filtrados if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada),
        'reprovados': sum(1 for e in ensaios_filtrados if "REPROVAR" in e.acao_recomendada)
    }

    total_paginas = math.ceil(total_registros / LIMITE_POR_PAGINA) if total_registros else 1

    context = {
        'ensaios': ensaios_filtrados,
        'kpi': kpi_global,
        'total_registros_filtrados': total_registros,
        'pagina_atual': pagina_real,
        'total_paginas': total_paginas,
        'sort_by': sort_by,
        'order': order,
        'search_term': search_term,
        'acao_filter': filter_acao,
        'date_start': date_start,
        'date_end': date_end,
        'material_filter': filter_material,
        'codigo_filter': filter_codigo,
        'catalogo_massas': CATALOGO_POR_CODIGO 
    }

    if request.headers.get('HX-Request'):
        return render_template('tabela_dados.html', **context)
    
    return render_template('index.html', **context)

@app.route('/config')
def pagina_config():
    query = request.args.get('q', '').upper()
    produtos_exibir = []
    
    # CORREÇÃO: Se não tiver busca, mostra os 50 primeiros itens do catálogo
    # (Antes estava filtrando e escondendo tudo)
    if not query:
        # Converte values() para lista e pega os 50 primeiros
        todos_prods = list(CATALOGO_POR_CODIGO.values())
        produtos_exibir = todos_prods[:50]
    else:
        # Busca por nome ou código
        for p in CATALOGO_POR_CODIGO.values():
            if query in str(p.cod_sankhya) or query in p.descricao.upper():
                produtos_exibir.append(p)
                if len(produtos_exibir) > 50: break # Limite de segurança
                
    return render_template('config.html', produtos=produtos_exibir, query=query)

@app.route('/salvar_config', methods=['POST'])
def salvar_config():
    cod = request.form.get('cod_sankhya')
    
    # Helpers de conversão
    def f(val): 
        try: return float(val.replace(',', '.')) if val else 0.0
        except: return 0.0
    
    def i(val):
        try: return int(val) if val else 0
        except: return 0

    # 1. Começa com a Temp Padrão
    specs = {
        "temp_padrao": f(request.form.get('temp_padrao'))
    }

    # 2. Captura os Parâmetros Fixos (Ts2, T90, Viscosidade)
    for param in ["Ts2", "T90", "Viscosidade"]:
        min_v = request.form.get(f"{param}_min")
        alvo_v = request.form.get(f"{param}_alvo")
        max_v = request.form.get(f"{param}_max")
        peso_v = request.form.get(f"{param}_peso") # <--- NOVO
        
        # Só salva se tiver preenchido algo relevante
        if min_v or alvo_v or max_v:
            specs[param] = {
                "min": f(min_v),
                "alvo": f(alvo_v),
                "max": f(max_v),
                "peso": i(peso_v) # Salva como inteiro
            }

    # 3. Captura Parâmetros Dinâmicos
    novos_nomes = request.form.getlist('din_nome[]')
    novos_pesos = request.form.getlist('din_peso[]') # <--- NOVO
    novos_mins = request.form.getlist('din_min[]')
    novos_alvos = request.form.getlist('din_alvo[]')
    novos_maxs = request.form.getlist('din_max[]')
    
    for idx, nome in enumerate(novos_nomes):
        if nome.strip():
            specs[nome] = {
                "min": f(novos_mins[idx]),
                "alvo": f(novos_alvos[idx]),
                "max": f(novos_maxs[idx]),
                "peso": i(novos_pesos[idx])
            }
    
    # 4. Salva e Aplica
    salvar_configuracao(cod, specs)
    aplicar_configuracoes_no_catalogo(CATALOGO_POR_CODIGO)
    
    global CACHE_ENSAIOS
    CACHE_ENSAIOS = None
    
    return redirect(url_for('pagina_config', q=cod))

if __name__ == '__main__':
    app.run(debug=True)
