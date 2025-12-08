import math
import re
import json
import os
import pandas as pd
from datetime import datetime
from difflib import get_close_matches

# Importação dos modelos e serviços existentes
from models.ensaio import Ensaio
from connection import connect_to_database
from etl_planilha import carregar_dicionario_lotes
from services.sankhya_service import importar_catalogo_sankhya
from services.config_manager import aplicar_configuracoes_no_catalogo

# --- VARIÁVEIS DE REFERÊNCIA (CACHE DO MÓDULO) ---
# Armazenam os dados estáticos ou de atualização lenta para uso nas funções auxiliares
_CATALOGO_CODIGO = {}
_CATALOGO_NOME = {}
_MAPA_LOTES_PLANILHA = {}
_MAPA_GRUPOS = {}
_DE_PARA_CORRECOES = {}

# --- FUNÇÕES AUXILIARES (HELPERS) ---

def safe_float(val):
    """Converte para float se possível, senão retorna None (e não 0.0)."""
    if val is None: return None
    try:
        f = float(val)
        if math.isnan(f): return None
        if f == 0: return None # Trata 0 como ausência de dado também
        return f
    except: return None

def carregar_referencias_estaticas():
    """
    Carrega mapas de configuração, catálogos e dicionários.
    Deve ser chamado na inicialização do app ou quando necessário recarregar configs.
    """
    global _CATALOGO_CODIGO, _CATALOGO_NOME, _MAPA_LOTES_PLANILHA, _MAPA_GRUPOS, _DE_PARA_CORRECOES
    
    print("--- 🔄 ETL: Carregando referências estáticas... ---")
    
    # 1. Carrega Catálogo Sankhya e Aplica Configs
    _CATALOGO_CODIGO, _CATALOGO_NOME = importar_catalogo_sankhya()
    aplicar_configuracoes_no_catalogo(_CATALOGO_CODIGO)
    
    # 2. Carrega Planilha de Lotes (Excel da Rede)
    _MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()
    
    # 3. Mapa de Grupos (Excel Local)
    _MAPA_GRUPOS = {}
    if os.path.exists("mapa_tipo_equipamentos.xlsx"):
        try:
            df_g = pd.read_excel("mapa_tipo_equipamentos.xlsx")
            _MAPA_GRUPOS = dict(zip(df_g['COD_GRUPO'], df_g['TIPO SUGERIDO']))
        except Exception as e:
            print(f"⚠️ Erro ao ler mapa_tipo_equipamentos: {e}")

    # 4. De-Para de Correções (JSON Local)
    _DE_PARA_CORRECOES = {}
    if os.path.exists("de_para_massas.json"):
        try:
            with open("de_para_massas.json", 'r', encoding='utf-8') as f:
                _DE_PARA_CORRECOES = json.load(f)
        except Exception as e:
            print(f"⚠️ Erro ao ler de_para_massas.json: {e}")
            
    print("✅ Referências carregadas com sucesso.")

def extrair_lote_da_string(texto_sujo):
    """Tenta identificar um número de lote válido dentro de uma string suja."""
    if not texto_sujo: return None, None
    texto = str(texto_sujo).strip().upper()
    
    # Prioridade para Asterisco (Ex: "ABC*123")
    if '*' in texto:
        partes = texto.split('*')
        if len(partes) >= 2:
            candidato = partes[1].strip().lstrip('0')
            if not candidato: candidato = '0'
            if candidato in _MAPA_LOTES_PLANILHA: return candidato, "Asterisco"

    # Match Exato
    if texto in _MAPA_LOTES_PLANILHA: return texto, "Exato"

    # Regex (Busca qualquer sequência numérica)
    todos_numeros = re.findall(r'\d+', texto)
    for num in reversed(todos_numeros): 
        candidato = num.lstrip('0')
        if candidato in _MAPA_LOTES_PLANILHA: return candidato, "Regex"
    return None, None

def match_nome_inteligente(texto_bruto):
    """
    Usa De-Para e Fuzzy Logic para encontrar o produto no catálogo Sankhya.
    """
    if not texto_bruto: return None
    texto = str(texto_bruto).strip().upper()
    
    # 1. Correção Manual (De-Para)
    if texto in _DE_PARA_CORRECOES: 
        texto = _DE_PARA_CORRECOES[texto]

    # 2. Busca Direta
    if texto in _CATALOGO_NOME: return _CATALOGO_NOME[texto]
    if ("MASSA " + texto) in _CATALOGO_NOME: return _CATALOGO_NOME["MASSA " + texto]
    if texto.startswith("MASSA "):
        sem = texto.replace("MASSA ", "").strip()
        if sem in _CATALOGO_NOME: return _CATALOGO_NOME[sem]

    # 3. Fuzzy Match (Aproximação)
    opcoes = list(_CATALOGO_NOME.keys())
    matches = get_close_matches(texto, opcoes, n=1, cutoff=0.7)
    
    if matches:
        melhor_match = matches[0]
        # Filtro de segurança para evitar falsos positivos perigosos
        palavras_orig = set(texto.split())
        palavras_match = set(melhor_match.split())
        diferenca = palavras_match - palavras_orig
        palavras_risco = {'ORB', 'AQ', 'PRETO', 'BRANCO', 'STD', 'ESPECIAL'}
        
        if not diferenca.intersection(palavras_risco):
            return _CATALOGO_NOME[melhor_match]
            
    return None

def classificar_tipo_ensaio(ensaio, temp_plato):
    """
    Define se é ALTA, BAIXA ou VISCOSIDADE baseado no Grupo ou Temperatura.
    """
    try:
        rotulo_grupo = _MAPA_GRUPOS.get(ensaio.cod_grupo)
        if rotulo_grupo and "VISCOSIMETRO" in str(rotulo_grupo).upper():
            return "VISCOSIDADE"
    except Exception:
        pass

    nome_perfil = getattr(ensaio, 'nome_perfil_usado', '') or ''
    nome_up = nome_perfil.upper()
    if "ALTA" in nome_up: return "ALTA"
    if "BAIXA" in nome_up: return "BAIXA"

    temp = temp_plato or 0
    if temp >= 175: return "ALTA"
    if 120 <= temp < 175: return "BAIXA"
    return "INDEFINIDO"

# --- LÓGICA PRINCIPAL (PROCESSADOR) ---

def processar_carga_dados(data_corte='2025-07-01'):
    """
    Executa o pipeline completo de ETL:
    1. Busca dados no SQL Server.
    2. Cruza com Catálogos e Lotes.
    3. Calcula Scores e Agrupamentos.
    4. Retorna os dados prontos para o Frontend.
    """
    # Garante que as referências existam (Lazy Loading)
    if not _CATALOGO_CODIGO:
        carregar_referencias_estaticas()

    print(f"--- 🚀 ETL PROCESSOR: Iniciando carga desde {data_corte} ---")
    start_time = datetime.now()
    
    resultados_brutos = []
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        
        # Query Otimizada
        query = '''
        SELECT 
            COD_ENSAIO, NUMERO_LOTE, BATCH, DATA, 
            T2TEMPO as Ts2, T90TEMPO as T90, VISCOSIDADEFINALTORQUE as Viscosidade, 
            TEMP_PLATO_INF, COD_GRUPO, MAXIMO_TEMPO,
            CODIGO as CODIGO_REO, AMOSTRA
        FROM dbo.ENSAIO 
        WHERE DATA >= ?
        ORDER BY DATA DESC
        '''
        cursor.execute(query, (data_corte,)) # Uso seguro de parâmetros
        colunas = [c[0] for c in cursor.description]
        resultados_brutos = [dict(zip(colunas, row)) for row in cursor.fetchall()]
        
    except Exception as e:
        print(f"❌ Erro Crítico no SQL: {e}")
        return None # Retorna None para indicar falha
    finally:
        if conn: conn.close()

    print(f"   > Processando {len(resultados_brutos)} registros brutos...")
    
    dados_agrupados = {} 
    
    # --- FASE 1: Agrupamento e Identificação ---
    for row in resultados_brutos:
        lote_orig = row['NUMERO_LOTE']
        amostra = row['AMOSTRA']
        grupo = row['COD_GRUPO']
        
        # Tentativa de descobrir o Lote Real
        lote_final, _ = extrair_lote_da_string(lote_orig)
        if not lote_final: 
            lote_final, _ = extrair_lote_da_string(amostra)
        
        # Chave para agrupar testes repetidos (Lote + Batch)
        chave_lote = lote_final if lote_final else str(lote_orig).strip().upper()
        try: chave_batch = int(row['BATCH'])
        except: chave_batch = 0
        chave_unica = (chave_lote, chave_batch)
        
        # Identificação do Produto (Massa)
        produto = None
        tipo_equip = _MAPA_GRUPOS.get(grupo, "INDEFINIDO")
        usar_cod = (tipo_equip != "VISCOSIMETRO") # Se for viscosímetro, o cód do aparelho costuma ser inútil
        
        if lote_final:
            nome_planilha = _MAPA_LOTES_PLANILHA.get(lote_final)
            if nome_planilha: 
                produto = match_nome_inteligente(nome_planilha)
        
        if not produto:
            produto = match_nome_inteligente(amostra) # Tenta ler o que o operador digitou
            if not produto and usar_cod: 
                produto = match_nome_inteligente(row['CODIGO_REO'])
                
        # Estrutura de Agrupamento
        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [],
                'massa': produto,
                'lote_visivel': chave_lote,
                'batch': chave_batch,
                'data': row['DATA'],
                'ts2': None,
                't90': None,
                'visc': None,
                'temps': [],
                'tempos_max': [],
                'tempo_max': None,
                'grupos': set()
            }
        
        reg = dados_agrupados[chave_unica]
        reg['ids_ensaio'].append(row['COD_ENSAIO'])
        reg['grupos'].add(grupo)
        
        # Se achamos um produto melhor agora, atualizamos o registro agrupado
        if not reg['massa'] and produto: 
            reg['massa'] = produto
            
        # Coleta de métricas (Prioriza valores válidos)
        v_ts2 = safe_float(row['Ts2'])
        v_t90 = safe_float(row['T90'])
        v_visc = safe_float(row['Viscosidade'])
        v_temp = safe_float(row['TEMP_PLATO_INF'])
        v_tempo_max = safe_float(row['MAXIMO_TEMPO'])
        
        if v_ts2: reg['ts2'] = v_ts2
        if v_t90: reg['t90'] = v_t90
        if v_visc: reg['visc'] = v_visc
        if v_temp and v_temp not in reg['temps']: reg['temps'].append(v_temp)
        if v_tempo_max:
            if not reg['tempo_max']: reg['tempo_max'] = v_tempo_max
            if v_tempo_max not in reg['tempos_max']: reg['tempos_max'].append(v_tempo_max)

    # --- FASE 2: Lógica de Negócio (Viscosidade Média) ---
    medias_visc_por_lote = {}
    acumulador_lote = {}
    
    # Coleta valores para média
    for dados in dados_agrupados.values():
        l = dados['lote_visivel']
        v = dados['visc']
        if v:
            if l not in acumulador_lote: acumulador_lote[l] = []
            acumulador_lote[l].append(v)
    
    # Calcula médias
    for l, valores in acumulador_lote.items():
        medias_visc_por_lote[l] = sum(valores) / len(valores)

    # --- FASE 3: Construção dos Objetos Finais ---
    lista_final = []
    materiais_set = set()
    
    for _, dados in dados_agrupados.items():
        if not dados['massa']: continue # Ignora se não identificou o material
        
        materiais_set.add(dados['massa'])
        
        # Aplicação da Média de Viscosidade
        valor_visc = dados['visc']
        origem_visc = "N/A"
        
        if valor_visc:
            origem_visc = "Real"
        elif dados['lote_visivel'] in medias_visc_por_lote:
            valor_visc = medias_visc_por_lote[dados['lote_visivel']]
            origem_visc = "Média (Lote)"
        else:
            valor_visc = None
        
        # Dicionário final de medidas
        medidas = {}
        if dados['ts2']: medidas['Ts2'] = dados['ts2']
        if dados['t90']: medidas['T90'] = dados['t90']
        if valor_visc: medidas['Viscosidade'] = valor_visc

        # Instanciação do Modelo
        temp_principal = dados['temps'][0] if dados['temps'] else 0
        
        novo_ensaio = Ensaio(
            id_ensaio=dados['ids_ensaio'][0],
            massa_objeto=dados['massa'],
            valores_medidos=medidas,
            lote=dados['lote_visivel'],
            batch=dados['batch'],
            data_hora=dados['data'],
            origem_viscosidade=origem_visc,
            temp_plato=temp_principal,
            temps_plato=list(dados['temps']),
            cod_grupo=list(dados['grupos'])[0],
            tempo_maximo=dados.get('tempo_max') or 0,
            tempos_max=list(dados.get('tempos_max') or []),
            ids_agrupados=list(dados['ids_ensaio'])
        )
        
        # Cálculos de Score e Classificação
        novo_ensaio.calcular_score()
        novo_ensaio.tipo_ensaio = classificar_tipo_ensaio(novo_ensaio, temp_principal)
        
        lista_final.append(novo_ensaio)
    
    # Ordenação padrão por data
    lista_final.sort(key=lambda x: x.data_hora, reverse=True)
    
    tempo_total = (datetime.now() - start_time).total_seconds()
    print(f"✅ ETL FINALIZADO: {len(lista_final)} ensaios gerados em {tempo_total:.1f}s.")
    
    return {
        'dados': lista_final,
        'materiais': sorted(list(materiais_set), key=lambda m: m.descricao),
        'ultimo_update': datetime.now(),
        'total_registros_brutos': len(resultados_brutos)
    }