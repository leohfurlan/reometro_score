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

def carregar_dados_do_banco():
    print("--- Atualizando dados do SQL Server (Com Médias)... ---")
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        query = '''
        SELECT TOP 3000 
            COD_ENSAIO, NUMERO_LOTE, BATCH, DATA, 
            T2TEMPO as Ts2, T90TEMPO as T90, VISCOSIDADEFINALTORQUE as Viscosidade, CODIGO,
            TEMP_PLATO_INF, COD_GRUPO, MAXIMO_TEMPO
        FROM dbo.ENSAIO 
        WHERE DATA >= '2024-01-01' ORDER BY DATA DESC
        '''
        cursor.execute(query)
        colunas = [c[0] for c in cursor.description]
        resultados = [dict(zip(colunas, row)) for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        print(f"❌ Erro SQL: {e}")
        return []

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
    return lista_ensaios

def obter_dados():
    global CACHE_ENSAIOS
    if CACHE_ENSAIOS is None:
        CACHE_ENSAIOS = carregar_dados_do_banco()
    return CACHE_ENSAIOS

# ==========================================
# 3. INTERFACE WEB
# ==========================================
@app.route('/')
def dashboard():
    # 1. Carrega dados (Cacheado e Processado)
    todos_ensaios = obter_dados()
    
    # --- KPIs GLOBAIS (Calculados antes de filtrar) ---
    kpi_global = {
        'total': len(todos_ensaios),
        'aprovados': sum(1 for e in todos_ensaios if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada),
        'ressalvas': sum(1 for e in todos_ensaios if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada),
        'reprovados': sum(1 for e in todos_ensaios if "REPROVAR" in e.acao_recomendada)
    }

    # --- FILTRAGEM ---
    ensaios_filtrados = todos_ensaios 
    
    # Parâmetros da URL
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    
    search_term = request.args.get('search', '').strip().upper()
    filter_acao = request.args.get('acao_filter', '')
    date_start = request.args.get('date_start', '')
    date_end = request.args.get('date_end', '')
    filter_material = request.args.get('material_filter', '')
    filter_codigo = request.args.get('codigo_filter', '').strip()

    # Aplica Filtros
    if search_term:
        ensaios_filtrados = [
            e for e in ensaios_filtrados 
            if search_term in str(e.lote).upper() 
            or (e.batch and search_term in str(e.batch).upper())
            or search_term in str(e.id_ensaio)
        ]

    if filter_acao:
        if filter_acao == "APROVADOS":
            ensaios_filtrados = [e for e in ensaios_filtrados if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada]
        elif filter_acao == "RESSALVA":
            ensaios_filtrados = [e for e in ensaios_filtrados if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada]
        elif filter_acao == "REPROVADO":
            ensaios_filtrados = [e for e in ensaios_filtrados if "REPROVAR" in e.acao_recomendada]

    if date_start:
        try:
            dt_start = datetime.strptime(date_start, '%Y-%m-%d')
            ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora and e.data_hora >= dt_start]
        except: pass
        
    if date_end:
        try:
            dt_end = datetime.strptime(date_end, '%Y-%m-%d')
            dt_end = dt_end.replace(hour=23, minute=59, second=59)
            ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora and e.data_hora <= dt_end]
        except: pass

    if filter_material:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.massa.descricao == filter_material]
    
    if filter_codigo:
        ensaios_filtrados = [e for e in ensaios_filtrados if str(e.massa.cod_sankhya) == filter_codigo]

    # --- ORDENAÇÃO ---
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

    if sort_by in key_funcs:
        ensaios_filtrados.sort(key=key_funcs[sort_by], reverse=reverse_order)
    
    # --- PAGINAÇÃO ---
    LIMITE_POR_PAGINA = 20
    total_itens_filtrados = len(ensaios_filtrados)
    total_pages = math.ceil(total_itens_filtrados / LIMITE_POR_PAGINA)
    
    if page > total_pages: page = 1
    if page < 1: page = 1

    start = (page - 1) * LIMITE_POR_PAGINA
    end = start + LIMITE_POR_PAGINA
    ensaios_paginados = ensaios_filtrados[start:end]

    # Contexto para o Template
    context = {
        'ensaios': ensaios_paginados,
        'kpi': kpi_global,
        'total_registros_filtrados': total_itens_filtrados,
        'pagina_atual': page,
        'total_paginas': total_pages,
        'sort_by': sort_by,
        'order': order,
        # Filtros para manter estado na URL
        'search_term': search_term,
        'acao_filter': filter_acao,
        'date_start': date_start,
        'date_end': date_end,
        'material_filter': filter_material,
        'codigo_filter': filter_codigo,
        # Catálogo para o Dropdown (Usamos o catálogo por código ou nome, o que preferir exibir)
        'catalogo_massas': CATALOGO_POR_CODIGO 
    }

    # [CORREÇÃO DO LAYOUT] 
    # Se a requisição vier do HTMX (clique na tabela/filtro), retorna SÓ a tabela.
    # Se for acesso direto no navegador, retorna a página inteira (Index).
    if request.headers.get('HX-Request'):
        return render_template('tabela_dados.html', **context)
    
    return render_template('index.html', **context)

@app.route('/config')
def pagina_config():
    query = request.args.get('q', '').upper()
    
    # Filtra produtos para exibir na lista
    produtos_exibir = []
    
    # Se não tiver busca, mostra só os que já tem configuração (para não travar a tela com 9000 itens)
    if not query:
        produtos_exibir = [p for p in CATALOGO_POR_CODIGO.values() if hasattr(p, 'parametros') and p.parametros]
    else:
        # Busca por nome ou código
        for p in CATALOGO_POR_CODIGO.values():
            if query in str(p.cod_sankhya) or query in p.descricao.upper():
                produtos_exibir.append(p)
                if len(produtos_exibir) > 50: break # Limite para não pesar
                
    return render_template('config.html', produtos=produtos_exibir, query=query)

@app.route('/salvar_config', methods=['POST'])
def salvar_config():
    cod = request.form.get('cod_sankhya')
    
    # Helper para converter vazio em 0.0
    def f(val): 
        try:
            return float(val.replace(',', '.')) if val else 0.0
        except ValueError:
            return 0.0

    specs = {
        # --- CAMPO NOVO: Temperatura Padrão ---
        "temp_padrao": f(request.form.get('temp_padrao')),
        
        "Ts2": {
            "min": f(request.form.get('ts2_min')),
            "alvo": f(request.form.get('ts2_alvo')),
            "max": f(request.form.get('ts2_max')),
            "peso": 8
        },
        "T90": {
            "min": f(request.form.get('t90_min')),
            "alvo": f(request.form.get('t90_alvo')),
            "max": f(request.form.get('t90_max')),
            "peso": 6
        },
        "Viscosidade": {
            "min": f(request.form.get('visc_min')),
            "alvo": f(request.form.get('visc_alvo')),
            "max": f(request.form.get('visc_max')),
            "peso": 10
        }
    }
    
    # 1. Salva no JSON (Persistência)
    salvar_configuracao(cod, specs)
    
    # 2. Atualiza em Memória (Imediato) para não precisar reiniciar o app
    aplicar_configuracoes_no_catalogo(CATALOGO_POR_CODIGO)
    
    # 3. Limpa o cache de ensaios para recalcular com as novas specs na próxima carga
    global CACHE_ENSAIOS
    CACHE_ENSAIOS = None
    
    return redirect(url_for('pagina_config', q=cod))

if __name__ == '__main__':
    app.run(debug=True)