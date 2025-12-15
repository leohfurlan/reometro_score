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
from services.learning_service import carregar_aprendizado  # <--- NOVA IMPORTAÇÃO

# --- VARIÁVEIS DE REFERÊNCIA (CACHE DO MÓDULO) ---
_CATALOGO_CODIGO = {}
_CATALOGO_NOME = {}
_MAPA_LOTES_PLANILHA = {}
_MAPA_GRUPOS = {} 
_DE_PARA_CORRECOES = {}
_MAPA_APRENDIZADO = {} # <--- NOVA MEMÓRIA

# --- FUNÇÕES AUXILIARES (HELPERS) ---

def safe_float(val):
    if val is None: return None
    try:
        f = float(val)
        if math.isnan(f) or f == 0: return None
        return f
    except: return None

def carregar_referencias_estaticas():
    """
    Carrega mapas de configuração, incluindo o novo Aprendizado Manual.
    """
    global _CATALOGO_CODIGO, _CATALOGO_NOME, _MAPA_LOTES_PLANILHA, _MAPA_GRUPOS, _DE_PARA_CORRECOES, _MAPA_APRENDIZADO
    
    print("--- 🔄 ETL: Carregando referências estáticas... ---")
    
    # 1. Carrega Catálogo Sankhya
    try:
        _CATALOGO_CODIGO, _CATALOGO_NOME = importar_catalogo_sankhya()
        aplicar_configuracoes_no_catalogo(_CATALOGO_CODIGO)
    except Exception as e:
        print(f"⚠️ Erro no Sankhya: {e}")

    # 2. Carrega Planilha de Lotes (Local/SharePoint)
    _MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()
    
    # 3. Mapa de Grupos via SQL Server
    print("   > Carregando Grupos de Máquinas do SQL Server...")
    _MAPA_GRUPOS = {}
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        
        query_grupos = '''
        SELECT COD_GRUPO, NOME, MAQUINA FROM dbo.GRUPO
        '''
        
        cursor.execute(query_grupos)
        rows = cursor.fetchall()
        
        for row in rows:
            c_grupo = row[0]
            c_nome = str(row[1]).strip().upper()
            c_maquina = row[2] 
            
            tipo_normalizado = "INDEFINIDO"
            str_maquina = str(c_maquina).strip()
            
            if str_maquina == '1': tipo_normalizado = "REOMETRO"
            elif str_maquina == '3': tipo_normalizado = "VISCOSIMETRO"
            elif "VISC" in c_nome: tipo_normalizado = "VISCOSIMETRO"
            elif "REO" in c_nome or "MDR" in c_nome: tipo_normalizado = "REOMETRO"
                
            _MAPA_GRUPOS[c_grupo] = {'tipo': tipo_normalizado, 'descricao': c_nome}
            
        print(f"   ✅ {len(_MAPA_GRUPOS)} grupos carregados da tabela dbo.GRUPO.")
        
    except Exception as e:
        print(f"⚠️ Erro ao carregar Grupos do SQL: {e}")
    finally:
        if conn: conn.close()

    # 4. De-Para JSON
    _DE_PARA_CORRECOES = {}
    if os.path.exists("de_para_massas.json"):
        try:
            with open("de_para_massas.json", 'r', encoding='utf-8') as f:
                _DE_PARA_CORRECOES = json.load(f)
        except: pass

    # 5. Aprendizado Manual (Prioridade Máxima)
    _MAPA_APRENDIZADO = carregar_aprendizado()
    print(f"   🧠 Memória carregada: {len(_MAPA_APRENDIZADO)} lotes ensinados manualmente.")

def extrair_lote_da_string(texto_sujo):
    if not texto_sujo: return None, None
    texto = str(texto_sujo).strip().upper()
    if '*' in texto:
        partes = texto.split('*')
        if len(partes) >= 2:
            candidato = partes[1].strip().lstrip('0')
            if not candidato: candidato = '0'
            if candidato in _MAPA_LOTES_PLANILHA: return candidato, "Asterisco"
    if texto in _MAPA_LOTES_PLANILHA: return texto, "Exato"
    todos_numeros = re.findall(r'\d+', texto)
    for num in reversed(todos_numeros): 
        candidato = num.lstrip('0')
        if candidato in _MAPA_LOTES_PLANILHA: return candidato, "Regex"
    return None, None

def match_nome_inteligente(texto_bruto):
    if not texto_bruto: return None
    texto = str(texto_bruto).strip().upper()
    if texto in _DE_PARA_CORRECOES: texto = _DE_PARA_CORRECOES[texto]
    if texto in _CATALOGO_NOME: return _CATALOGO_NOME[texto]
    if ("MASSA " + texto) in _CATALOGO_NOME: return _CATALOGO_NOME["MASSA " + texto]
    if texto.startswith("MASSA "):
        sem = texto.replace("MASSA ", "").strip()
        if sem in _CATALOGO_NOME: return _CATALOGO_NOME[sem]
    opcoes = list(_CATALOGO_NOME.keys())
    matches = get_close_matches(texto, opcoes, n=1, cutoff=0.7)
    if matches:
        melhor_match = matches[0]
        palavras_orig = set(texto.split())
        palavras_match = set(melhor_match.split())
        diferenca = palavras_match - palavras_orig
        palavras_risco = {'ORB', 'AQ', 'PRETO', 'BRANCO', 'STD', 'ESPECIAL'}
        if not diferenca.intersection(palavras_risco):
            return _CATALOGO_NOME[melhor_match]
    return None

def classificar_tipo_ensaio(ensaio, temp_plato):
    dados_grupo = _MAPA_GRUPOS.get(ensaio.cod_grupo)
    if dados_grupo:
        tipo_oficial = dados_grupo.get('tipo', 'INDEFINIDO')
        if tipo_oficial == 'VISCOSIMETRO': return 'VISCOSIDADE'
        if tipo_oficial == 'REOMETRO': pass 
    
    nome_perfil = getattr(ensaio, 'nome_perfil_usado', '') or ''
    nome_up = nome_perfil.upper()
    if "VISC" in nome_up or "MOONEY" in nome_up: return "VISCOSIDADE"
    if "ALTA" in nome_up: return "ALTA"
    if "BAIXA" in nome_up: return "BAIXA"

    temp = temp_plato or 0
    if temp >= 175: return "ALTA"
    if 120 <= temp < 175: return "BAIXA"
    if 90 <= temp < 120: return "VISCOSIDADE"
    return "ALTA"


# --- LÓGICA PRINCIPAL ---

def processar_carga_dados(data_corte='2025-07-01'):
    if not _CATALOGO_CODIGO:
        carregar_referencias_estaticas()

    print(f"--- 🚀 ETL PROCESSOR: Iniciando carga SQL... ---")
    start_time = datetime.now()
    
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
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
        cursor.execute(query, (data_corte,))
        colunas = [c[0] for c in cursor.description]
        resultados_brutos = [dict(zip(colunas, row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"❌ Erro Crítico no SQL: {e}")
        return None
    finally:
        if conn: conn.close()

    dados_agrupados = {} 
    
    for row in resultados_brutos:
        lote_orig = row['NUMERO_LOTE']
        amostra = row['AMOSTRA']
        grupo = row['COD_GRUPO']
        
        # Chaves para busca
        key_lote_orig = str(lote_orig).strip().upper()
        
        produto = None
        equip_planilha = None
        metodo_id = "FANTASMA"
        
        # Variáveis finais (padrão)
        lote_final = None
        chave_lote = key_lote_orig # Se não achar nada, usa o original (sujo)

        dados_grupo = _MAPA_GRUPOS.get(grupo, {})
        tipo_equip = dados_grupo.get('tipo', 'INDEFINIDO')
        usar_cod = (tipo_equip != "VISCOSIMETRO") 

        # --- [PRIORIDADE 0] MEMÓRIA DE APRENDIZADO (CORREÇÃO TOTAL) ---
        match_aprendido = _MAPA_APRENDIZADO.get(key_lote_orig)
        
        if match_aprendido:
            # Sobrescreve com o que o usuário ensinou
            lote_final = match_aprendido.get('lote_real')
            nome_massa_aprendida = match_aprendido.get('massa')
            
            # Atualiza a chave de agrupamento para o lote limpo/correto
            chave_lote = lote_final
            
            produto = match_nome_inteligente(nome_massa_aprendida)
            if produto:
                metodo_id = "MANUAL" # 🔵 Ensinado pelo usuário
        
        else:
            # --- [PRIORIDADE 1] BUSCA AUTOMÁTICA (Planilha / Regex) ---
            lote_final, _ = extrair_lote_da_string(lote_orig)
            if not lote_final: lote_final, _ = extrair_lote_da_string(amostra)
            
            if lote_final:
                chave_lote = lote_final # Usa o lote limpo
                
                dados_planilha = _MAPA_LOTES_PLANILHA.get(lote_final)
                
                if dados_planilha:
                    # Lógica de Ano (Correção de Colisão)
                    if isinstance(dados_planilha, dict) and 'massa' not in dados_planilha:
                        try:
                            data_ensaio = row['DATA']
                            ano_ensaio = str(data_ensaio.year) if hasattr(data_ensaio, 'year') else str(pd.to_datetime(data_ensaio).year)
                        except:
                            ano_ensaio = str(datetime.now().year)
                        
                        item_ano = dados_planilha.get(ano_ensaio)
                        if not item_ano:
                            try:
                                anos_ordenados = sorted(dados_planilha.keys())
                                if anos_ordenados: item_ano = dados_planilha[anos_ordenados[-1]]
                            except: pass
                        
                        if item_ano: dados_planilha = item_ano

                    # Extração dos dados
                    if isinstance(dados_planilha, dict):
                        nome_massa = dados_planilha.get('massa')
                        equip_planilha = dados_planilha.get('equipamento')
                    else:
                        nome_massa = dados_planilha
                    
                    if nome_massa: 
                        produto = match_nome_inteligente(nome_massa)
                        if produto:
                            metodo_id = "LOTE" # ✅ Identificado via Planilha
            
            # --- [PRIORIDADE 2] BUSCA POR TEXTO (FALLBACK) ---
            if not produto:
                produto = match_nome_inteligente(amostra)
                if not produto and usar_cod: 
                    produto = match_nome_inteligente(row['CODIGO_REO'])
                
                if produto:
                    metodo_id = "TEXTO" # ⚠️ Identificado via Fuzzy/Texto

        # --- AGRUPAMENTO ---
        try: chave_batch = int(row['BATCH'])
        except: chave_batch = 0
        chave_unica = (chave_lote, chave_batch)

        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [], 'massa': produto, 
                'lote_visivel': chave_lote, 'batch': chave_batch, 
                'data': row['DATA'],
                'ts2': None, 't90': None, 'visc': None,
                'temps': [], 'tempos_max': [], 'tempo_max': None,
                'grupos': set(),
                'equip_planilha': equip_planilha,
                'metodo_id': metodo_id
            }
        
        # Atualiza se encontrou melhor info (ex: equipamento)
        reg = dados_agrupados[chave_unica]
        if equip_planilha and not reg['equip_planilha']: reg['equip_planilha'] = equip_planilha
        
        # Atualiza método se melhorou (FANTASMA -> MANUAL/LOTE)
        if reg['metodo_id'] == "FANTASMA" and metodo_id != "FANTASMA":
            reg['metodo_id'] = metodo_id
            
        reg['ids_ensaio'].append(row['COD_ENSAIO'])
        reg['grupos'].add(grupo)
        if not reg['massa'] and produto: reg['massa'] = produto
            
        v_ts2 = safe_float(row['Ts2'])
        v_t90 = safe_float(row['T90'])
        v_visc = safe_float(row['Viscosidade'])
        v_temp = safe_float(row['TEMP_PLATO_INF'])
        v_max = safe_float(row['MAXIMO_TEMPO'])
        
        if v_ts2: reg['ts2'] = v_ts2
        if v_t90: reg['t90'] = v_t90
        if v_visc: reg['visc'] = v_visc
        if v_temp and v_temp not in reg['temps']: reg['temps'].append(v_temp)
        if v_max:
            if not reg['tempo_max']: reg['tempo_max'] = v_max
            if v_max not in reg['tempos_max']: reg['tempos_max'].append(v_max)

    # --- 📊 NOVO BLOCO: CÁLCULO DE MÉDIAS ESTATÍSTICAS POR LOTE ---
    acumuladores = {
        'visc': {},
        'ts2': {},
        't90': {}
    }

    # 1. Coleta os valores de todos os ensaios do mesmo lote (independente do batch)
    for dados in dados_agrupados.values():
        l = dados['lote_visivel']
        
        if dados['visc']:
            if l not in acumuladores['visc']: acumuladores['visc'][l] = []
            acumuladores['visc'][l].append(dados['visc'])
            
        if dados['ts2']:
            if l not in acumuladores['ts2']: acumuladores['ts2'][l] = []
            acumuladores['ts2'][l].append(dados['ts2'])

        if dados['t90']:
            if l not in acumuladores['t90']: acumuladores['t90'][l] = []
            acumuladores['t90'][l].append(dados['t90'])

    # 2. Calcula as médias
    medias_por_lote = {'visc': {}, 'ts2': {}, 't90': {}}
    for tipo in ['visc', 'ts2', 't90']:
        for lote, valores in acumuladores[tipo].items():
            if valores:
                medias_por_lote[tipo][lote] = sum(valores) / len(valores)

    # -------------------------------------------------------------

    lista_final = []
    materiais_set = set()
    
    for _, dados in dados_agrupados.items():
        if not dados['massa']: continue
        materiais_set.add(dados['massa'])
        
        lote_atual = dados['lote_visivel']
        
        # Lógica da Viscosidade (Preenchimento de Falta)
        valor_visc = dados['visc']
        origem_visc = "Real" if valor_visc else "N/A"
        
        if not valor_visc and lote_atual in medias_por_lote['visc']:
            valor_visc = medias_por_lote['visc'][lote_atual]
            origem_visc = "Média (Lote)"
        
        medidas = {}
        if dados['ts2']: medidas['Ts2'] = dados['ts2']
        if dados['t90']: medidas['T90'] = dados['t90']
        if valor_visc: medidas['Viscosidade'] = valor_visc

        temp_princ = dados['temps'][0] if dados['temps'] else 0
        grupo_id = list(dados['grupos'])[0]

        novo_ensaio = Ensaio(
            id_ensaio=dados['ids_ensaio'][0],
            massa_objeto=dados['massa'],
            valores_medidos=medidas,
            lote=lote_atual,
            batch=dados['batch'],
            data_hora=dados['data'],
            origem_viscosidade=origem_visc,
            temp_plato=temp_princ,
            temps_plato=list(dados['temps']),
            cod_grupo=grupo_id,
            tempo_maximo=dados.get('tempo_max') or 0,
            tempos_max=list(dados.get('tempos_max') or []),
            ids_agrupados=list(dados['ids_ensaio']),
            equipamento_planilha=dados.get('equip_planilha') 
        )
        
        # --- ATRIBUIÇÃO DE NOVOS DADOS ---
        novo_ensaio.metodo_identificacao = dados.get('metodo_id', 'FANTASMA')
        
        # Injeção das médias para relatórios (mesmo se o ensaio tiver valor real)
        novo_ensaio.medias_lote = {
            'Ts2': medias_por_lote['ts2'].get(lote_atual),
            'T90': medias_por_lote['t90'].get(lote_atual),
            'Visc': medias_por_lote['visc'].get(lote_atual)
        }
        
        novo_ensaio.calcular_score()
        novo_ensaio.tipo_ensaio = classificar_tipo_ensaio(novo_ensaio, temp_princ)
        
        lista_final.append(novo_ensaio)
    
    lista_final.sort(key=lambda x: x.data_hora, reverse=True)
    
    tempo_total = (datetime.now() - start_time).total_seconds()
    print(f"✅ ETL FINALIZADO: {len(lista_final)} registros em {tempo_total:.1f}s.")
    
    return {
        'dados': lista_final,
        'materiais': sorted(list(materiais_set), key=lambda m: m.descricao),
        'ultimo_update': datetime.now(),
        'total_registros_brutos': len(resultados_brutos)
    }

def get_catalogo_codigo():
    return _CATALOGO_CODIGO