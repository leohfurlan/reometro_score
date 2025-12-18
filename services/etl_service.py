import math
import re
import json
import os
import pandas as pd
from datetime import datetime
from difflib import get_close_matches

# --- NOVAS IMPORTAÇÕES (ARQUITETURA V13) ---
from models.usuario import db
from models.consolidado import EnsaioConsolidado
from models.score_versioning import ScoreVersao, ScoreResultado
from services.scoring_engine import ScoringEngine
# -------------------------------------------

from connection import connect_to_database
from etl_planilha import carregar_dicionario_lotes
from services.sankhya_service import importar_catalogo_sankhya
from services.config_manager import aplicar_configuracoes_no_catalogo
from services.learning_service import carregar_aprendizado

# --- VARIÁVEIS DE REFERÊNCIA (CACHE DO MÓDULO) ---
_CATALOGO_CODIGO = {}
_CATALOGO_NOME = {}
_MAPA_LOTES_PLANILHA = {}
_MAPA_GRUPOS = {} 
_DE_PARA_CORRECOES = {}
_MAPA_APRENDIZADO = {}

# --- FUNÇÕES AUXILIARES ---

def safe_float(val):
    if val is None: return None
    try:
        f = float(val)
        if math.isnan(f) or f == 0: return None
        return f
    except: return None

def chunk_list(lista, tamanho):
    """Gera fatias da lista para evitar erro de 'too many SQL variables'"""
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]

def carregar_referencias_estaticas():
    """
    Carrega mapas de configuração, incluindo o Aprendizado Manual.
    """
    global _CATALOGO_CODIGO, _CATALOGO_NOME, _MAPA_LOTES_PLANILHA, _MAPA_GRUPOS, _DE_PARA_CORRECOES, _MAPA_APRENDIZADO
    
    print("--- 🔄 ETL: Carregando referências estáticas... ---")
    
    try:
        _CATALOGO_CODIGO, _CATALOGO_NOME = importar_catalogo_sankhya()
        aplicar_configuracoes_no_catalogo(_CATALOGO_CODIGO)
    except Exception as e:
        print(f"⚠️ Erro no Sankhya: {e}")

    _MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()
    
    print("   > Carregando Grupos de Máquinas do SQL Server...")
    _MAPA_GRUPOS = {}
    conn = None
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        cursor.execute("SELECT COD_GRUPO, NOME, MAQUINA FROM dbo.GRUPO")
        rows = cursor.fetchall()
        for row in rows:
            c_grupo = row[0]
            c_nome = str(row[1]).strip().upper()
            str_maquina = str(row[2]).strip()
            tipo_normalizado = "INDEFINIDO"
            if str_maquina == '1': tipo_normalizado = "REOMETRO"
            elif str_maquina == '3': tipo_normalizado = "VISCOSIMETRO"
            elif "VISC" in c_nome: tipo_normalizado = "VISCOSIMETRO"
            elif "REO" in c_nome or "MDR" in c_nome: tipo_normalizado = "REOMETRO"
            _MAPA_GRUPOS[c_grupo] = {'tipo': tipo_normalizado, 'descricao': c_nome}
        print(f"   ✅ {len(_MAPA_GRUPOS)} grupos carregados.")
    except Exception as e:
        print(f"⚠️ Erro ao carregar Grupos do SQL: {e}")
    finally:
        if conn: conn.close()

    if os.path.exists("de_para_massas.json"):
        try:
            with open("de_para_massas.json", 'r', encoding='utf-8') as f:
                _DE_PARA_CORRECOES = json.load(f)
        except: pass

    _MAPA_APRENDIZADO = carregar_aprendizado()
    print(f"   🧠 Memória carregada: {len(_MAPA_APRENDIZADO)} correções manuais.")

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
    opcoes = list(_CATALOGO_NOME.keys())
    matches = get_close_matches(texto, opcoes, n=1, cutoff=0.7)
    if matches: return _CATALOGO_NOME[matches[0]]
    return None

# --- LÓGICA PRINCIPAL (ETL V2) ---

def processar_carga_dados(data_corte='2025-07-01'):
    if not _CATALOGO_CODIGO:
        carregar_referencias_estaticas()

    print(f"--- 🚀 ETL V2: Iniciando carga e cálculo de score... ---")
    start_time = datetime.now()
    
    versao_ativa = ScoreVersao.query.filter_by(status='ACTIVE').first()
    engine = None
    if versao_ativa:
        print(f"   ⚙️ Engine ativada: {versao_ativa.nome}")
        engine = ScoringEngine(versao_ativa)
    else:
        print("   ⚠️ Nenhuma versão de score ATIVA encontrada. Scores serão 0.")

    conn = None
    resultados_brutos = []
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
        key_lote_orig = str(lote_orig).strip().upper()
        
        lote_final = key_lote_orig
        produto = None
        metodo_id = "FANTASMA"
        equip_planilha = None
        
        match_aprendido = _MAPA_APRENDIZADO.get(key_lote_orig)
        if match_aprendido:
            lote_final = match_aprendido.get('lote_real')
            produto = match_nome_inteligente(match_aprendido.get('massa'))
            if produto: metodo_id = "MANUAL"
        
        if metodo_id == "FANTASMA":
            lote_clean, _ = extrair_lote_da_string(lote_orig)
            if not lote_clean: lote_clean, _ = extrair_lote_da_string(amostra)
            if lote_clean:
                lote_final = lote_clean
                dados_planilha = _MAPA_LOTES_PLANILHA.get(lote_final)
                if isinstance(dados_planilha, dict) and 'massa' not in dados_planilha:
                    ano = str(row['DATA'].year)
                    dados_planilha = dados_planilha.get(ano) or list(dados_planilha.values())[-1]
                if isinstance(dados_planilha, dict):
                    produto = match_nome_inteligente(dados_planilha.get('massa'))
                    equip_planilha = dados_planilha.get('equipamento')
                    if produto: metodo_id = "LOTE"

        if metodo_id == "FANTASMA":
            produto = match_nome_inteligente(amostra) or match_nome_inteligente(row['CODIGO_REO'])
            if produto: metodo_id = "TEXTO"

        try: chave_batch = str(int(row['BATCH']))
        except: chave_batch = "0"
        
        chave_unica = (lote_final, chave_batch)
        
        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [], 'massa': produto,
                'lote_visivel': lote_final, 'batch': chave_batch,
                'lote_original': key_lote_orig,
                'material_original': str(amostra).strip(),
                'data': row['DATA'],
                'ts2': None, 't90': None, 'visc': None, 'temps': [],
                'ids_reo': [], 'ids_visc': [],
                'temps_reo': [], 'temps_visc': [],
                'metodo_id': metodo_id,
                'equip_planilha': equip_planilha
            }
        
        reg = dados_agrupados[chave_unica]
        if equip_planilha and not reg['equip_planilha']: reg['equip_planilha'] = equip_planilha
        if reg['metodo_id'] == "FANTASMA" and metodo_id != "FANTASMA": reg['metodo_id'] = metodo_id
        if not reg['massa'] and produto: reg['massa'] = produto
        
        cod_ensaio = row['COD_ENSAIO']
        reg['ids_ensaio'].append(cod_ensaio)
        
        v_ts2 = safe_float(row['Ts2'])
        v_t90 = safe_float(row['T90'])
        v_visc = safe_float(row['Viscosidade'])
        v_temp = safe_float(row['TEMP_PLATO_INF'])

        # Classifica o tipo do ensaio (Reometria vs Viscosidade) para persistir temps/ids corretamente.
        tipo_maquina = _MAPA_GRUPOS.get(grupo, {}).get('tipo', 'INDEFINIDO')
        is_visc = False
        if tipo_maquina == 'VISCOSIMETRO':
            is_visc = True
        elif tipo_maquina == 'REOMETRO':
            is_visc = False
        else:
            # Fallbacks
            if v_temp and 90 <= v_temp <= 115:
                is_visc = True
            elif v_temp and v_temp >= 120:
                is_visc = False
            else:
                is_visc = bool(v_visc and not (v_ts2 or v_t90))
        
        if v_ts2: reg['ts2'] = v_ts2
        if v_t90: reg['t90'] = v_t90
        if v_visc: reg['visc'] = v_visc
        if v_temp:
            reg['temps'].append(v_temp)
            if is_visc:
                reg['temps_visc'].append(v_temp)
            else:
                reg['temps_reo'].append(v_temp)

        if is_visc:
            reg['ids_visc'].append(cod_ensaio)
        else:
            reg['ids_reo'].append(cod_ensaio)

    acumuladores_visc = {}
    for dados in dados_agrupados.values():
        if dados['visc']:
            l = dados['lote_visivel']
            if l not in acumuladores_visc: acumuladores_visc[l] = []
            acumuladores_visc[l].append(dados['visc'])
    
    medias_visc = {k: sum(v)/len(v) for k, v in acumuladores_visc.items()}

    lista_consolidada = []
    lista_historico = []
    ids_ensaios_processados = []

    for _, dados in dados_agrupados.items():
        if not dados['massa']: continue
        
        valor_visc = dados['visc']
        origem_visc = "Real" if valor_visc else "N/A"
        if not valor_visc and dados['lote_visivel'] in medias_visc:
            valor_visc = medias_visc[dados['lote_visivel']]
            origem_visc = "Média"

        ids_merge = sorted({int(i) for i in (dados['ids_ensaio'] or []) if i is not None})
        temps_merge = sorted({float(t) for t in (dados['temps'] or []) if t}, reverse=True)

        ids_reo = sorted({int(i) for i in (dados.get('ids_reo') or []) if i is not None})
        ids_visc = sorted({int(i) for i in (dados.get('ids_visc') or []) if i is not None})

        temps_reo = sorted({float(t) for t in (dados.get('temps_reo') or []) if t}, reverse=True)
        temps_visc = sorted({float(t) for t in (dados.get('temps_visc') or []) if t}, reverse=True)

        temp_plato = (temps_reo[0] if temps_reo else (temps_visc[0] if temps_visc else (temps_merge[0] if temps_merge else 0)))

        novo_ensaio = EnsaioConsolidado(
            id_ensaio=dados['ids_ensaio'][0],
            data_hora=dados['data'],
            lote=dados['lote_visivel'],
            batch=dados['batch'],
            cod_sankhya=dados['massa'].cod_sankhya,
            massa_descricao=dados['massa'].descricao,
            temp_plato=temp_plato,
            ts2=dados['ts2'],
            t90=dados['t90'],
            viscosidade=valor_visc,
            origem_viscosidade=origem_visc,
            ids_agrupados=json.dumps(ids_merge),
            temps_plato=json.dumps(temps_merge),
            temp_reo=(temps_reo[0] if temps_reo else None),
            temp_visc=(temps_visc[0] if temps_visc else None),
            ids_reo=json.dumps(ids_reo),
            ids_visc=json.dumps(ids_visc),
            metodo_identificacao=dados['metodo_id'],
            lote_original=dados['lote_original'],
            material_original=dados['material_original'],
            updated_at=datetime.now()
        )
        
        if engine:
            resultado = engine.calcular(novo_ensaio)
            novo_ensaio.score_final = resultado.score
            novo_ensaio.acao_recomendada = resultado.acao
            lista_historico.append(resultado)
            ids_ensaios_processados.append(novo_ensaio.id_ensaio)
        else:
            novo_ensaio.score_final = 0
            novo_ensaio.acao_recomendada = "SEM ENGINE"

        lista_consolidada.append(novo_ensaio)

    # 6. Persistência com Chunking (Correção do Erro)
    try:
        print(f"   💾 Salvando {len(lista_consolidada)} registros consolidados...")
        
        # A. Upsert do Dataset Mestre
        count = 0
        for e in lista_consolidada:
            db.session.merge(e)
            count += 1
            # Commit parcial para aliviar memória
            if count % 1000 == 0:
                db.session.commit()
        db.session.commit() # Commit final do merge
        
        # B. Histórico de Score (Com Chunking no Delete)
        if engine and ids_ensaios_processados:
            print(f"   🧹 Limpando histórico anterior...")
            
            # Limite seguro para SQLite (999 é o padrão antigo, 900 é seguro)
            BATCH_SIZE = 900 
            
            # Deleta em lotes
            for lote_ids in chunk_list(ids_ensaios_processados, BATCH_SIZE):
                db.session.query(ScoreResultado).filter(
                    ScoreResultado.id_versao == engine.versao.id,
                    ScoreResultado.id_ensaio.in_(lote_ids)
                ).delete(synchronize_session=False)
            
            db.session.commit() # Confirma deleções
            
            print(f"   📝 Inserindo novos resultados...")
            # Insere em lotes
            for lote_res in chunk_list(lista_historico, BATCH_SIZE):
                db.session.add_all(lote_res)
                db.session.commit()

        print("✅ Dados persistidos com sucesso!")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Erro ao salvar no banco: {e}")
        return None

    total_time = (datetime.now() - start_time).total_seconds()
    return {'total': len(lista_consolidada), 'tempo': total_time}

def get_catalogo_codigo():
    return _CATALOGO_CODIGO
