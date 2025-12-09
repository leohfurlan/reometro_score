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
_CATALOGO_CODIGO = {}
_CATALOGO_NOME = {}
_MAPA_LOTES_PLANILHA = {}
_MAPA_GRUPOS = {} 
_DE_PARA_CORRECOES = {}

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
    Carrega mapas de configuração.
    AGORA COM SQL SERVER PARA GRUPOS (Tabela dbo.GRUPO)!
    """
    global _CATALOGO_CODIGO, _CATALOGO_NOME, _MAPA_LOTES_PLANILHA, _MAPA_GRUPOS, _DE_PARA_CORRECOES
    
    print("--- 🔄 ETL: Carregando referências estáticas... ---")
    
    # 1. Carrega Catálogo Sankhya
    try:
        _CATALOGO_CODIGO, _CATALOGO_NOME = importar_catalogo_sankhya()
        aplicar_configuracoes_no_catalogo(_CATALOGO_CODIGO)
    except Exception as e:
        print(f"⚠️ Erro no Sankhya: {e}")

    # 2. Carrega Planilha de Lotes (Local/SharePoint)
    _MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()
    
    # 3. [ATUALIZADO] Mapa de Grupos via SQL Server (Tabela dbo.GRUPO)
    print("   > Carregando Grupos de Máquinas do SQL Server...")
    _MAPA_GRUPOS = {}
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        
        # --- QUERY AJUSTADA PARA SUA TABELA REAL ---
        query_grupos = '''
        SELECT 
            COD_GRUPO, 
            NOME,    
            MAQUINA  
        FROM dbo.GRUPO
        '''
        
        cursor.execute(query_grupos)
        rows = cursor.fetchall()
        
        for row in rows:
            c_grupo = row[0]
            c_nome = str(row[1]).strip().upper()
            c_maquina = row[2] # Pode vir como int (1, 3) ou string
            
            tipo_normalizado = "INDEFINIDO"
            str_maquina = str(c_maquina).strip()
            
            # Lógica baseada na sua informação:
            # Máquina 1 = Reômetro
            # Máquina 3 = Viscosímetro
            
            if str_maquina == '1': 
                tipo_normalizado = "REOMETRO"
            elif str_maquina == '3': 
                tipo_normalizado = "VISCOSIMETRO"
            # Fallback pelo nome se o código da máquina estiver vazio ou diferente
            elif "VISC" in c_nome: 
                tipo_normalizado = "VISCOSIMETRO"
            elif "REO" in c_nome or "MDR" in c_nome:
                tipo_normalizado = "REOMETRO"
                
            _MAPA_GRUPOS[c_grupo] = {
                'tipo': tipo_normalizado,
                'descricao': c_nome
            }
            
        print(f"   ✅ {len(_MAPA_GRUPOS)} grupos carregados da tabela dbo.GRUPO.")
        
    except Exception as e:
        print(f"⚠️ Erro ao carregar Grupos do SQL: {e}")
        # Fallback (opcional): Tenta ler o Excel antigo se o banco falhar
        if os.path.exists("mapa_tipo_equipamentos.xlsx"):
            print("   -> Usando mapa_tipo_equipamentos.xlsx como backup.")
            try:
                df_g = pd.read_excel("mapa_tipo_equipamentos.xlsx")
                for _, row in df_g.iterrows():
                    _MAPA_GRUPOS[row['COD_GRUPO']] = {
                        'tipo': row['TIPO SUGERIDO'], 
                        'descricao': 'VIA EXCEL (BACKUP)'
                    }
            except: pass
    finally:
        if conn: conn.close()

    # 4. De-Para JSON
    _DE_PARA_CORRECOES = {}
    if os.path.exists("de_para_massas.json"):
        try:
            with open("de_para_massas.json", 'r', encoding='utf-8') as f:
                _DE_PARA_CORRECOES = json.load(f)
        except: pass

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
    """
    Define se é ALTA, BAIXA ou VISCOSIDADE.
    """
    # 1. Tenta pegar a definição oficial do Grupo (Se o banco tiver a info correta)
    dados_grupo = _MAPA_GRUPOS.get(ensaio.cod_grupo)
    if dados_grupo:
        tipo_oficial = dados_grupo.get('tipo', 'INDEFINIDO')
        if tipo_oficial == 'VISCOSIMETRO':
            return 'VISCOSIDADE'
        if tipo_oficial == 'REOMETRO':
            # Se for Reômetro, ainda precisamos saber se é Alta ou Baixa
            pass 
    
    # 2. Definição por Nome do Perfil (Ex: "Viscosidade Mooney")
    nome_perfil = getattr(ensaio, 'nome_perfil_usado', '') or ''
    nome_up = nome_perfil.upper()
    if "VISC" in nome_up or "MOONEY" in nome_up: return "VISCOSIDADE"
    if "ALTA" in nome_up: return "ALTA"
    if "BAIXA" in nome_up: return "BAIXA"

    # 3. Definição por Temperatura (Heurística)
    temp = temp_plato or 0
    
    if temp >= 175: return "ALTA"          # Reometria (Cura Rápida)
    if 120 <= temp < 175: return "BAIXA"   # Reometria (Scorch)
    if 90 <= temp < 120: return "VISCOSIDADE" # <--- CORREÇÃO: Faixa típica Mooney (100°C)
    
    # Fallback: Se não caiu em nada, assume Reometria (Alta) para não quebrar
    return "ALTA"


# --- LÓGICA PRINCIPAL ---

def processar_carga_dados(data_corte='2025-07-01'):
    # Garante que as referências existam (Lazy Loading)
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
        
        lote_final, _ = extrair_lote_da_string(lote_orig)
        if not lote_final: lote_final, _ = extrair_lote_da_string(amostra)
        
        chave_lote = lote_final if lote_final else str(lote_orig).strip().upper()
        try: chave_batch = int(row['BATCH'])
        except: chave_batch = 0
        chave_unica = (chave_lote, chave_batch)
        
        # --- LÓGICA DE IDENTIFICAÇÃO ---
        produto = None
        
        # Busca tipo da máquina no mapa carregado do DB
        dados_grupo = _MAPA_GRUPOS.get(grupo, {})
        tipo_equip = dados_grupo.get('tipo', 'INDEFINIDO')
        
        # Se for viscosímetro (Máquina 3), ignorar código do reômetro
        usar_cod = (tipo_equip != "VISCOSIMETRO") 
        
        if lote_final:
            nome_planilha = _MAPA_LOTES_PLANILHA.get(lote_final)
            if nome_planilha: produto = match_nome_inteligente(nome_planilha)
        
        if not produto:
            produto = match_nome_inteligente(amostra)
            if not produto and usar_cod: 
                produto = match_nome_inteligente(row['CODIGO_REO'])
        # -------------------------------

        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [], 'massa': produto, 'lote_visivel': chave_lote,
                'batch': chave_batch, 'data': row['DATA'],
                'ts2': None, 't90': None, 'visc': None,
                'temps': [], 'tempos_max': [], 'tempo_max': None,
                'grupos': set()
            }
        
        reg = dados_agrupados[chave_unica]
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

    # Médias de Viscosidade
    medias_visc_por_lote = {}
    acumulador_lote = {}
    for dados in dados_agrupados.values():
        l = dados['lote_visivel']
        v = dados['visc']
        if v:
            if l not in acumulador_lote: acumulador_lote[l] = []
            acumulador_lote[l].append(v)
    for l, vals in acumulador_lote.items():
        medias_visc_por_lote[l] = sum(vals) / len(vals)

    lista_final = []
    materiais_set = set()
    
    for _, dados in dados_agrupados.items():
        if not dados['massa']: continue
        materiais_set.add(dados['massa'])
        
        valor_visc = dados['visc']
        origem_visc = "Real" if valor_visc else "N/A"
        
        if not valor_visc and dados['lote_visivel'] in medias_visc_por_lote:
            valor_visc = medias_visc_por_lote[dados['lote_visivel']]
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
            lote=dados['lote_visivel'],
            batch=dados['batch'],
            data_hora=dados['data'],
            origem_viscosidade=origem_visc,
            temp_plato=temp_princ,
            temps_plato=list(dados['temps']),
            cod_grupo=grupo_id,
            tempo_maximo=dados.get('tempo_max') or 0,
            tempos_max=list(dados.get('tempos_max') or []),
            ids_agrupados=list(dados['ids_ensaio'])
        )
        
        # Se quiser guardar o nome do grupo no objeto Ensaio para mostrar na tela:
        # novo_ensaio.nome_grupo = _MAPA_GRUPOS.get(grupo_id, {}).get('descricao', '---')
        
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
    """Retorna a referência atualizada do catálogo de códigos."""
    return _CATALOGO_CODIGO