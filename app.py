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
    print("--- Atualizando dados do SQL Server... ---")
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        
        # --- QUERY ATUALIZADA COM AS NOVAS COLUNAS ---
        query = '''
        SELECT TOP 2000 
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

    lista_ensaios = []
    
    # Caches para performance (evita reprocessar strings repetidas)
    cache_limpeza_lote = {}
    cache_resolucao_nomes = {}
    
    # Contadores para Diagnóstico (Opcional, mas útil)
    diag_matches_exatos = 0
    diag_matches_prefixo = 0
    diag_matches_contem = 0
    diag_sem_match = 0

    print(f"   > Processando {len(resultados)} registros com limpeza avançada e novos dados...")

    for row in resultados:
        lote_sujo = str(row['NUMERO_LOTE']).strip().upper()
        
        # ====================================================
        # ETAPA 1: LIMPEZA AVANÇADA DE LOTE (REGEX + PADDING)
        # ====================================================
        lote_final_match = None
        
        if lote_sujo in cache_limpeza_lote:
            lote_final_match = cache_limpeza_lote[lote_sujo]
        else:
            # Extrai grupos numéricos (ex: "OTR1801*9459..." -> ["1801", "94590000"])
            grupos_numericos = re.findall(r'\d+', lote_sujo)
            
            # Itera de trás para frente (Lote geralmente está no final)
            for grupo in reversed(grupos_numericos):
                # Remove zeros à esquerda
                candidato_base = grupo.lstrip('0')
                if not candidato_base: continue 
                
                # Tenta dar Match na Planilha reduzindo zeros à direita
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

        # ====================================================
        # ETAPA 2: IDENTIFICAÇÃO DO PRODUTO (SANKHYA)
        # ====================================================
        produto_identificado = None
        origem_id = "N/A"
        
        # A) Se achamos o lote na planilha, pegamos o nome "sujo" de lá
        nome_planilha = None
        if lote_final_match:
            nome_planilha = MAPA_LOTES_PLANILHA.get(lote_final_match)

        if nome_planilha:
            nome_sujo = nome_planilha.strip().upper()
            
            # Verifica cache de nomes
            if nome_sujo in cache_resolucao_nomes:
                produto_identificado = cache_resolucao_nomes[nome_sujo]
            else:
                # Match Inteligente
                # 1. Exato
                produto_identificado = CATALOGO_POR_NOME.get(nome_sujo)
                
                # 2. Prefixo "MASSA "
                if not produto_identificado:
                    produto_identificado = CATALOGO_POR_NOME.get("MASSA " + nome_sujo)
                    if produto_identificado: diag_matches_prefixo += 1

                # 3. Contém (Wildcard) - Mais lento, porém poderoso
                if not produto_identificado:
                    for nome_sankhya, obj_prod in CATALOGO_POR_NOME.items():
                        if (nome_sujo in nome_sankhya) or (nome_sankhya in nome_sujo):
                            produto_identificado = obj_prod
                            diag_matches_contem += 1
                            break
                else:
                     diag_matches_exatos += 1
                
                # Salva no cache
                cache_resolucao_nomes[nome_sujo] = produto_identificado
                if produto_identificado: origem_id = "Planilha+Match"

        # B) Fallback: Código Legado do Banco SQL
        if not produto_identificado:
            cod_antigo = row['CODIGO']
            produto_identificado = CATALOGO_POR_CODIGO.get(cod_antigo)
            if produto_identificado: origem_id = "CodigoSQL"
        
        if not produto_identificado:
            diag_sem_match += 1

        # ====================================================
        # ETAPA 3: CRIAÇÃO DO ENSAIO COM NOVOS DADOS
        # ====================================================
        if produto_identificado and isinstance(produto_identificado, Massa):
            
            valores = {"Ts2": row['Ts2'], "T90": row['T90']}
            origem_visc = "N/A"
            if row['Viscosidade'] and row['Viscosidade'] > 0:
                valores["Viscosidade"] = row['Viscosidade']
                origem_visc = "Real"

            novo_ensaio = Ensaio(
                id_ensaio=row['COD_ENSAIO'],
                massa_objeto=produto_identificado,
                valores_medidos=valores,
                # Usa lote limpo se achou, senão usa o original sujo
                lote=lote_final_match if lote_final_match else lote_sujo, 
                batch=row['BATCH'],
                data_hora=row['DATA'],
                origem_viscosidade=origem_visc,
                
                # --- NOVOS CAMPOS DO SQL ---
                temp_plato=row['TEMP_PLATO_INF'] if row['TEMP_PLATO_INF'] else 0,
                cod_grupo=row['COD_GRUPO'] if row['COD_GRUPO'] else 0,
                tempo_maximo=row['MAXIMO_TEMPO'] if row['MAXIMO_TEMPO'] else 0
            )
            
            if row['BATCH']:
                try: novo_ensaio.batch = int(row['BATCH'])
                except: pass

            novo_ensaio.calcular_score()
            lista_ensaios.append(novo_ensaio)
            
    print(f"--- Processamento Concluído: {len(lista_ensaios)} ensaios. ---")
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