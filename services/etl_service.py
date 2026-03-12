import math
import re
import json
import os
import pandas as pd
from datetime import datetime
from difflib import get_close_matches
from time import perf_counter

# --- NOVAS IMPORTAÃƒâ€¡Ãƒâ€¢ES (ARQUITETURA V13) ---
from models.usuario import db
from models.consolidado import EnsaioConsolidado
from models.score_versioning import ScoreResultado
from services.scoring_engine import ScoringEngine
# -------------------------------------------

from connection import connect_to_database
from etl_planilha import carregar_dicionario_lotes
from services.sankhya_service import importar_catalogo_sankhya
from services.config_manager import (
    aplicar_configuracoes_no_catalogo,
)
from services.learning_service import carregar_aprendizado_mapa
from services.score_configuration_service import get_active_score_version

# --- VARIÃƒÂVEIS DE REFERÃƒÅ NCIA (CACHE DO MÃƒâ€œDULO) ---
_CATALOGO_CODIGO = {}
_CATALOGO_NOME = {}
_MAPA_LOTES_PLANILHA = {}
_MAPA_GRUPOS = {} 
_DE_PARA_CORRECOES = {}
_MAPA_APRENDIZADO = {}

# --- FUNÃƒâ€¡Ãƒâ€¢ES AUXILIARES ---

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


def _rotulo_reometro_por_descricao(descricao_grupo):
    txt = str(descricao_grupo or "").strip().upper()
    if not txt:
        return None
    if "PRETO" in txt:
        return "PRETO"
    if "BRANCO" in txt or "CINZA" in txt:
        return "CINZA"
    # Qualquer outro nome de grupo nao representa o tipo de reometro (Cinza/Preto).
    return None


def _faixa_reometria(v_temp, descricao_grupo):
    temp = safe_float(v_temp)
    if temp is not None:
        return "alta" if temp >= 175 else "baixa"

    desc = str(descricao_grupo or "").upper()
    if "ALTA" in desc or "HIGH" in desc:
        return "alta"
    if "BAIXA" in desc or "LOW" in desc:
        return "baixa"
    return "alta"


def _buscar_versao_ativa():
    """
    Localiza a versao ativa a partir da fonte oficial no banco.
    """
    return get_active_score_version(create_from_legacy=True)


def _criar_versao_ativa_bootstrap():
    """
    Mantido por compatibilidade com chamadas legadas do ETL.
    """
    return get_active_score_version(create_from_legacy=True)


def _obter_engine_ativa():
    """
    Retorna a engine pronta para calculo a partir da versao ativa oficial.
    """
    versao_ativa = _buscar_versao_ativa()
    if versao_ativa:
        print(f"   Engine ativada: {versao_ativa.nome}")
        return ScoringEngine(versao_ativa)

    print("   Nenhuma versao de score disponivel.")
    return None

def _obter_dados_lote_planilha(chave_lote):
    """
    Resolve lote no dicionÃƒÂ¡rio da planilha com compatibilidade para chaves antigas
    no formato numÃƒÂ©rico com '.0' (ex: '10091.0').
    """
    if not chave_lote:
        return None

    chave = str(chave_lote).strip().upper()
    if not chave:
        return None

    dados = _MAPA_LOTES_PLANILHA.get(chave)
    if dados is not None:
        return dados

    if re.fullmatch(r'\d+', chave):
        return _MAPA_LOTES_PLANILHA.get(f"{chave}.0")

    if re.fullmatch(r'\d+\.0+', chave):
        return _MAPA_LOTES_PLANILHA.get(chave.split('.', 1)[0])

    return None

def _gerar_candidatos_numericos(token_num):
    """
    Gera candidatos de lote a partir de um bloco numÃƒÂ©rico.
    Quando detecta sufixo de zeros longos (ruÃƒÂ­do tÃƒÂ­pico do reÃƒÂ´metro),
    tenta versÃƒÂµes progressivamente aparadas.
    """
    bruto = re.sub(r'\D', '', str(token_num or ''))
    if not bruto:
        return []

    candidatos = []

    def _add(valor):
        v = (valor or '').lstrip('0') or '0'
        if v and v not in candidatos:
            candidatos.append(v)

    _add(bruto)

    base = bruto.lstrip('0')
    if not base:
        return candidatos

    _add(base)

    # Remove sufixo longo de zeros (ruido comum no reometro).
    if re.search(r'0{3,}$', base):
        atual = base
        while atual.endswith('0') and len(atual) > 3:
            atual = atual[:-1]
            _add(atual)

    # Caso com zeros no meio + sufixo curto (ex: 001010100001 -> 10101).
    tam = len(base)
    for tam_prefixo in range(4, min(8, tam - 3) + 1):
        for tam_sufixo in (1, 2, 3):
            if tam_prefixo + tam_sufixo >= tam:
                continue
            miolo = base[tam_prefixo: tam - tam_sufixo]
            sufixo = base[tam - tam_sufixo:]
            if len(miolo) >= 3 and set(miolo) == {'0'}:
                prefixo = base[:tam_prefixo]
                _add(prefixo)
                _add(f"{prefixo}{sufixo}")

    return candidatos


def _compact_lote_key(texto):
    return re.sub(r'[^A-Z0-9]', '', str(texto or '').strip().upper())


def _parece_data(token):
    """
    Evita confundir datas (ddmmyyyy / yyyymmdd) com lote.
    """
    t = str(token or '').strip()
    if not re.fullmatch(r'\d{8}', t):
        return False

    try:
        dia = int(t[:2]); mes = int(t[2:4]); ano = int(t[4:])
        if 1 <= dia <= 31 and 1 <= mes <= 12 and 2000 <= ano <= 2100:
            return True
    except Exception:
        pass

    try:
        ano = int(t[:4]); mes = int(t[4:6]); dia = int(t[6:])
        if 2000 <= ano <= 2100 and 1 <= mes <= 12 and 1 <= dia <= 31:
            return True
    except Exception:
        pass

    return False


def _lote_generico_valido(token):
    t = str(token or '').strip()
    if not re.fullmatch(r'\d+', t):
        return False
    if len(t) < 4:
        return False
    if set(t) == {'0'}:
        return False
    if _parece_data(t):
        return False
    if len(t) > 8 and re.search(r'0{3,}$', t) and _parece_data(t[:8]):
        return False
    if len(t) > 8 and _parece_data(t[:8]) and set(t[8:]) == {'0'}:
        return False
    return True


def _ordenar_candidatos_genericos(candidatos):
    # Usa o comprimento mais comum da planilha como referencia (fallback 5 digitos).
    contagem_tam = {}
    for chave in _MAPA_LOTES_PLANILHA.keys():
        token = str(chave or "").strip().upper()
        if re.fullmatch(r"\d+\.0+", token):
            token = token.split(".", 1)[0]
        if not re.fullmatch(r"\d+", token):
            continue
        tam = len(token.lstrip("0") or "0")
        if 4 <= tam <= 7:
            contagem_tam[tam] = contagem_tam.get(tam, 0) + 1

    if contagem_tam:
        tam_ref = max(contagem_tam.items(), key=lambda item: (item[1], -abs(item[0] - 5), item[0]))[0]
    else:
        tam_ref = 5

    def _rank(c):
        tam = len(c)
        faixa = 0 if 4 <= tam <= 7 else (1 if tam <= 10 else 2)
        dist_ref = abs(tam - tam_ref)
        zeros_fim = 1 if re.search(r"0{3,}$", c) else 0
        return (faixa, dist_ref, zeros_fim, -tam)

    uniq = []
    for c in candidatos:
        if c not in uniq:
            uniq.append(c)
    return sorted(uniq, key=_rank)


def _extrair_por_numeros(texto, exigir_mapa):
    for num in reversed(re.findall(r'\d+', texto)):
        candidatos = _gerar_candidatos_numericos(num)
        if exigir_mapa:
            for candidato in candidatos:
                if _obter_dados_lote_planilha(candidato) is not None:
                    return candidato
        else:
            for candidato in _ordenar_candidatos_genericos(candidatos):
                if _lote_generico_valido(candidato):
                    return candidato
    return None


def _score_lote_limpo(candidato, origem):
    c = str(candidato or '').strip()
    if not c:
        return (99, 99, 99, 99)
    origem_rank = 0 if origem in {"Asterisco", "Exato", "Regex"} else 1

    contagem_tam = {}
    for chave in _MAPA_LOTES_PLANILHA.keys():
        token = str(chave or "").strip().upper()
        if re.fullmatch(r"\d+\.0+", token):
            token = token.split(".", 1)[0]
        if not re.fullmatch(r"\d+", token):
            continue
        tam = len(token.lstrip("0") or "0")
        if 4 <= tam <= 7:
            contagem_tam[tam] = contagem_tam.get(tam, 0) + 1
    tam_ref = max(contagem_tam.items(), key=lambda item: (item[1], -abs(item[0] - 5), item[0]))[0] if contagem_tam else 5

    tam_c = len(c)
    faixa = 0 if 4 <= tam_c <= 7 else (1 if tam_c <= 10 else 2)
    dist_ref = abs(tam_c - tam_ref)
    zeros_fim = 1 if re.search(r"0{3,}$", c) else 0
    return (origem_rank, faixa, dist_ref, zeros_fim, -tam_c)

def carregar_referencias_estaticas():
    """
    Carrega mapas de configuraÃƒÂ§ÃƒÂ£o, incluindo o Aprendizado Manual.
    """
    global _CATALOGO_CODIGO, _CATALOGO_NOME, _MAPA_LOTES_PLANILHA, _MAPA_GRUPOS, _DE_PARA_CORRECOES, _MAPA_APRENDIZADO
    
    print("---  ETL: Carregando referÃƒÂªncias estÃƒÂ¡ticas... ---")
    
    try:
        _CATALOGO_CODIGO, _CATALOGO_NOME = importar_catalogo_sankhya()
        aplicar_configuracoes_no_catalogo(_CATALOGO_CODIGO)
    except Exception as e:
        print(f"Erro no Sankhya: {e}")

    _MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()
    
    print("  > Carregando Grupos de MÃƒÂ¡quinas do SQL Server...")
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
        print(f"   {len(_MAPA_GRUPOS)} grupos carregados.")
    except Exception as e:
        print(f"Erro ao carregar Grupos do SQL: {e}")
    finally:
        if conn: conn.close()

    if os.path.exists("de_para_massas.json"):
        try:
            with open("de_para_massas.json", 'r', encoding='utf-8') as f:
                _DE_PARA_CORRECOES = json.load(f)
        except: pass

    _MAPA_APRENDIZADO = carregar_aprendizado_mapa()
    print(f"MemÃƒÂ³ria carregada (SQLAlchemy): {len(_MAPA_APRENDIZADO)} correÃƒÂ§ÃƒÂµes manuais.")


def recarregar_aprendizado_memoria():
    """
    Recarrega somente o mapa de aprendizado manual na memoria do ETL.
    """
    global _MAPA_APRENDIZADO
    _MAPA_APRENDIZADO = carregar_aprendizado_mapa()
    return len(_MAPA_APRENDIZADO)


def extrair_lote_da_string(texto_sujo):
    if not texto_sujo: return None, None
    texto = str(texto_sujo).strip().upper()
    if '*' in texto:
        partes = texto.split('*')
        if len(partes) >= 2:
            for parte in partes[1:]:
                parte_limpa = parte.strip()
                if _obter_dados_lote_planilha(parte_limpa) is not None:
                    if re.fullmatch(r'\d+\.0+', parte_limpa):
                        return parte_limpa.split('.', 1)[0], "Asterisco"
                    return parte_limpa, "Asterisco"
                cand = _extrair_por_numeros(parte, exigir_mapa=True)
                if cand:
                    return cand, "Asterisco"
    if _obter_dados_lote_planilha(texto) is not None:
        if re.fullmatch(r'\d+\.0+', texto):
            return texto.split('.', 1)[0], "Exato"
        return texto, "Exato"

    # 1) Prioriza match no mapa da planilha.
    cand = _extrair_por_numeros(texto, exigir_mapa=True)
    if cand:
        return cand, "Regex"

    # 2) Fallback: limpeza generica segura (sem depender da planilha).
    cand = _extrair_por_numeros(texto, exigir_mapa=False)
    if cand:
        return cand, "Generico"

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

# --- LOGICA PRINCIPAL (ETL V2) ---

_ETL_STATE_FILE = os.path.abspath(os.getenv("ETL_STATE_FILE", os.path.join("instance", "etl_state.json")))


def _normalizar_batch(valor):
    try:
        return str(int(valor))
    except Exception:
        txt = str(valor or "").strip()
        return txt if txt else "0"


def _ranking_metodo_identificacao(metodo):
    ranking = {"MANUAL": 0, "LOTE": 1, "TEXTO": 2, "FANTASMA": 3}
    return ranking.get(str(metodo or "FANTASMA").strip().upper(), 99)


def _melhor_metodo_identificacao(atual, novo):
    return atual if _ranking_metodo_identificacao(atual) <= _ranking_metodo_identificacao(novo) else novo


def _carregar_estado_etl():
    if not os.path.exists(_ETL_STATE_FILE):
        return {}

    try:
        with open(_ETL_STATE_FILE, "r", encoding="utf-8") as fp:
            dados = json.load(fp)
        return dados if isinstance(dados, dict) else {}
    except Exception as exc:
        print(f"[WARN] Falha ao ler estado incremental do ETL: {exc}")
        return {}


def _salvar_estado_etl(max_cod_ensaio):
    try:
        pasta = os.path.dirname(_ETL_STATE_FILE)
        if pasta:
            os.makedirs(pasta, exist_ok=True)

        payload = {
            "max_cod_ensaio": int(max_cod_ensaio or 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(_ETL_STATE_FILE, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[WARN] Falha ao salvar estado incremental do ETL: {exc}")


def _obter_ultimo_cod_ensaio_processado():
    estado = _carregar_estado_etl()
    try:
        valor = int(estado.get("max_cod_ensaio") or 0)
        if valor > 0:
            return valor
    except Exception:
        pass

    # Bootstrap: quando nao houver estado salvo, usa o maior id local como ponto de partida.
    try:
        local_max = db.session.query(db.func.max(EnsaioConsolidado.id_ensaio)).scalar() or 0
        return int(local_max or 0)
    except Exception:
        return 0


def processar_carga_dados(data_corte='2025-07-01', apenas_novos=True):
    if not _CATALOGO_CODIGO:
        carregar_referencias_estaticas()
    else:
        qtd = recarregar_aprendizado_memoria()
        print(f"Memoria de aprendizado atualizada para ETL: {qtd} correcoes.")

    print("--- ETL V2: Iniciando carga e calculo de score... ---")
    start_time = datetime.now()
    t_total = perf_counter()
    stage_timings = {
        'sql': 0.0,
        'agrupamento': 0.0,
        'consolidacao': 0.0,
        'persistencia': 0.0,
        'total': 0.0,
    }

    engine = _obter_engine_ativa()
    if engine is None:
        print("   Nenhuma versao de score disponivel. Scores serao 0.")

    ultimo_cod = _obter_ultimo_cod_ensaio_processado() if apenas_novos else 0
    if apenas_novos and ultimo_cod > 0:
        print(f"   Modo incremental ativo (COD_ENSAIO > {ultimo_cod}).")
    elif apenas_novos:
        print("   Modo incremental ativo (sem estado salvo, ponto de corte local = 0).")
    else:
        print("   Modo completo forcado.")

    conn = None
    resultados_brutos = []
    t_sql = perf_counter()
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
        '''
        params = [data_corte]

        if apenas_novos and ultimo_cod > 0:
            query += " AND COD_ENSAIO > ?"
            params.append(ultimo_cod)

        query += " ORDER BY COD_ENSAIO ASC"

        cursor.execute(query, params)
        colunas = [c[0] for c in cursor.description]
        resultados_brutos = [dict(zip(colunas, row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Erro Critico no SQL: {e}")
        return None
    finally:
        if conn:
            conn.close()
    stage_timings['sql'] = perf_counter() - t_sql

    if not resultados_brutos:
        total_time = (datetime.now() - start_time).total_seconds()
        stage_timings['total'] = perf_counter() - t_total
        print("   Nenhum novo ensaio para processar.")
        print(
            "[PERF][ETL] "
            f"sql={stage_timings['sql']:.2f}s "
            f"agrupamento={stage_timings['agrupamento']:.2f}s "
            f"consolidacao={stage_timings['consolidacao']:.2f}s "
            f"persistencia={stage_timings['persistencia']:.2f}s "
            f"total={stage_timings['total']:.2f}s "
            "brutos=0 consolidados=0"
        )
        return {
            'total': 0,
            'total_bruto': 0,
            'tempo': total_time,
            'incremental': bool(apenas_novos),
            'ids_processados': [],
            'timings': stage_timings,
        }

    dados_agrupados = {}
    novo_max_cod = int(ultimo_cod or 0)
    t_agrupamento = perf_counter()

    for row in resultados_brutos:
        try:
            cod_ensaio = int(row['COD_ENSAIO'])
        except Exception:
            continue

        if cod_ensaio > novo_max_cod:
            novo_max_cod = cod_ensaio

        lote_orig = row['NUMERO_LOTE']
        amostra = row['AMOSTRA']
        grupo = row['COD_GRUPO']
        key_lote_orig = str(lote_orig).strip().upper()
        key_lote_compacto = _compact_lote_key(key_lote_orig)

        lote_final = key_lote_orig
        produto = None
        metodo_id = "FANTASMA"
        equip_planilha = None

        match_aprendido = _MAPA_APRENDIZADO.get(key_lote_orig) or _MAPA_APRENDIZADO.get(key_lote_compacto)
        if match_aprendido:
            lote_final = match_aprendido.get('lote_real')
            produto = match_nome_inteligente(match_aprendido.get('massa'))
            if produto:
                metodo_id = "MANUAL"

        if metodo_id == "FANTASMA":
            lote_cand_orig, origem_orig = extrair_lote_da_string(lote_orig)
            lote_cand_amostra, origem_amostra = extrair_lote_da_string(amostra)

            lote_clean = None
            if lote_cand_orig and lote_cand_amostra:
                score_orig = _score_lote_limpo(lote_cand_orig, origem_orig)
                score_amostra = _score_lote_limpo(lote_cand_amostra, origem_amostra)
                lote_clean = lote_cand_orig if score_orig <= score_amostra else lote_cand_amostra
            else:
                lote_clean = lote_cand_orig or lote_cand_amostra

            if lote_clean:
                lote_final = lote_clean
                dados_planilha = _obter_dados_lote_planilha(lote_final)
                if isinstance(dados_planilha, dict) and 'massa' not in dados_planilha:
                    ano = str(row['DATA'].year)
                    dados_planilha = dados_planilha.get(ano) or list(dados_planilha.values())[-1]
                if isinstance(dados_planilha, dict):
                    produto = match_nome_inteligente(dados_planilha.get('massa'))
                    equip_planilha = dados_planilha.get('equipamento')
                    if produto:
                        metodo_id = "LOTE"

        if metodo_id == "FANTASMA":
            produto = match_nome_inteligente(amostra) or match_nome_inteligente(row['CODIGO_REO'])
            if produto:
                metodo_id = "TEXTO"

        chave_batch = _normalizar_batch(row.get('BATCH'))
        chave_unica = (str(lote_final or '').strip().upper(), chave_batch)

        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [], 'massa': produto,
                'lote_visivel': chave_unica[0], 'batch': chave_batch,
                'lote_original': key_lote_orig,
                'material_original': str(amostra).strip(),
                'data': row['DATA'],
                'ts2': None, 't90': None, 'visc': None, 'temps': [],
                'ts2_alta': None, 't90_alta': None, 'ts2_baixa': None, 't90_baixa': None,
                'reometro_alta': None, 'reometro_baixa': None,
                'ids_reo': [], 'ids_visc': [],
                'ids_reo_alta': [], 'ids_reo_baixa': [],
                'temps_reo': [], 'temps_visc': [],
                'temps_reo_alta': [], 'temps_reo_baixa': [],
                'metodo_id': metodo_id,
                'equip_planilha': equip_planilha,
            }

        reg = dados_agrupados[chave_unica]

        if row['DATA'] and (not reg['data'] or row['DATA'] > reg['data']):
            reg['data'] = row['DATA']
        if equip_planilha and not reg['equip_planilha']:
            reg['equip_planilha'] = equip_planilha
        reg['metodo_id'] = _melhor_metodo_identificacao(reg.get('metodo_id'), metodo_id)
        if not reg['massa'] and produto:
            reg['massa'] = produto

        reg['ids_ensaio'].append(cod_ensaio)

        v_ts2 = safe_float(row['Ts2'])
        v_t90 = safe_float(row['T90'])
        v_visc = safe_float(row['Viscosidade'])
        v_temp = safe_float(row['TEMP_PLATO_INF'])

        dados_grupo = _MAPA_GRUPOS.get(grupo, {})
        tipo_maquina = dados_grupo.get('tipo', 'INDEFINIDO')
        desc_grupo = dados_grupo.get('descricao')

        is_visc = False
        if tipo_maquina == 'VISCOSIMETRO':
            is_visc = True
        elif tipo_maquina == 'REOMETRO':
            is_visc = False
        else:
            if v_temp and 90 <= v_temp <= 115:
                is_visc = True
            elif v_temp and v_temp >= 120:
                is_visc = False
            else:
                is_visc = bool(v_visc and not (v_ts2 or v_t90))

        faixa_reo = None
        if not is_visc:
            faixa_reo = _faixa_reometria(v_temp, desc_grupo)
            rotulo_reometro = _rotulo_reometro_por_descricao(desc_grupo)

            if v_ts2 and reg['ts2'] is None:
                reg['ts2'] = v_ts2
            if v_t90 and reg['t90'] is None:
                reg['t90'] = v_t90

            if faixa_reo == 'alta':
                if v_ts2 and reg['ts2_alta'] is None:
                    reg['ts2_alta'] = v_ts2
                if v_t90 and reg['t90_alta'] is None:
                    reg['t90_alta'] = v_t90
                if rotulo_reometro and not reg['reometro_alta']:
                    reg['reometro_alta'] = rotulo_reometro
            else:
                if v_ts2 and reg['ts2_baixa'] is None:
                    reg['ts2_baixa'] = v_ts2
                if v_t90 and reg['t90_baixa'] is None:
                    reg['t90_baixa'] = v_t90
                if rotulo_reometro and not reg['reometro_baixa']:
                    reg['reometro_baixa'] = rotulo_reometro
        else:
            if v_visc and reg['visc'] is None:
                reg['visc'] = v_visc

        if v_temp:
            reg['temps'].append(v_temp)
            if is_visc:
                reg['temps_visc'].append(v_temp)
            else:
                reg['temps_reo'].append(v_temp)
                if faixa_reo == 'alta':
                    reg['temps_reo_alta'].append(v_temp)
                else:
                    reg['temps_reo_baixa'].append(v_temp)

        if is_visc:
            reg['ids_visc'].append(cod_ensaio)
        else:
            reg['ids_reo'].append(cod_ensaio)
            if faixa_reo == 'alta':
                reg['ids_reo_alta'].append(cod_ensaio)
            else:
                reg['ids_reo_baixa'].append(cod_ensaio)
    stage_timings['agrupamento'] = perf_counter() - t_agrupamento

    t_consolidacao = perf_counter()
    acumuladores_visc = {}
    for dados in dados_agrupados.values():
        if dados['visc']:
            lote_visivel = dados['lote_visivel']
            if lote_visivel not in acumuladores_visc:
                acumuladores_visc[lote_visivel] = []
            acumuladores_visc[lote_visivel].append(dados['visc'])

    medias_visc = {k: sum(v) / len(v) for k, v in acumuladores_visc.items()}

    chaves_processadas = {
        (str(d.get('lote_visivel') or '').strip().upper(), _normalizar_batch(d.get('batch')))
        for d in dados_agrupados.values()
        if str(d.get('lote_visivel') or '').strip()
    }

    mapa_existentes = {}
    if chaves_processadas:
        lotes_alvo = sorted({chave[0] for chave in chaves_processadas})
        candidatos = EnsaioConsolidado.query.filter(EnsaioConsolidado.lote.in_(lotes_alvo)).all()

        for existente in candidatos:
            chave = (str(existente.lote or '').strip().upper(), _normalizar_batch(existente.batch))
            if chave not in chaves_processadas:
                continue

            atual = mapa_existentes.get(chave)
            if atual is None:
                mapa_existentes[chave] = existente
                continue

            ref_atual = atual.updated_at or atual.data_hora or datetime.min
            ref_existente = existente.updated_at or existente.data_hora or datetime.min
            if ref_existente > ref_atual:
                mapa_existentes[chave] = existente

    lista_consolidada = []
    lista_historico = []
    ids_ensaios_processados = []

    for dados in dados_agrupados.values():
        chave = (str(dados['lote_visivel'] or '').strip().upper(), _normalizar_batch(dados.get('batch')))
        existente = mapa_existentes.get(chave)

        massa = dados.get('massa')
        if not massa and existente and existente.cod_sankhya:
            massa = type(
                'MassaTmp',
                (),
                {'cod_sankhya': existente.cod_sankhya, 'descricao': existente.massa_descricao},
            )()

        if not massa:
            continue

        ids_novos = {int(i) for i in (dados.get('ids_ensaio') or []) if i is not None}
        ids_antigos = set(existente.ids_agrupados_list) if existente else set()
        ids_merge = sorted(ids_novos | ids_antigos)

        if existente and existente.id_ensaio:
            id_destino = int(existente.id_ensaio)
            ids_merge = sorted(set(ids_merge) | {id_destino})
        elif ids_merge:
            id_destino = max(ids_merge)
        else:
            continue

        temps_antigos = existente.temps_plato_list if existente else []
        temps_merge = sorted({float(t) for t in (dados.get('temps') or []) + temps_antigos if t}, reverse=True)

        ids_reo_antigos = set(existente.ids_reo_list) if existente else set()
        ids_visc_antigos = set(existente.ids_visc_list) if existente else set()

        ids_reo = sorted({int(i) for i in (dados.get('ids_reo') or []) if i is not None} | ids_reo_antigos)
        ids_visc = sorted({int(i) for i in (dados.get('ids_visc') or []) if i is not None} | ids_visc_antigos)

        temps_reo_base = [float(t) for t in (dados.get('temps_reo') or []) if t]
        temps_visc_base = [float(t) for t in (dados.get('temps_visc') or []) if t]

        if existente and existente.temp_reo:
            temps_reo_base.append(float(existente.temp_reo))
        if existente and existente.temp_visc:
            temps_visc_base.append(float(existente.temp_visc))

        temps_reo = sorted(set(temps_reo_base), reverse=True)
        temps_visc = sorted(set(temps_visc_base), reverse=True)

        ts2_alta = dados['ts2_alta'] if dados['ts2_alta'] is not None else (existente.ts2_alta if existente else None)
        t90_alta = dados['t90_alta'] if dados['t90_alta'] is not None else (existente.t90_alta if existente else None)
        ts2_baixa = dados['ts2_baixa'] if dados['ts2_baixa'] is not None else (existente.ts2_baixa if existente else None)
        t90_baixa = dados['t90_baixa'] if dados['t90_baixa'] is not None else (existente.t90_baixa if existente else None)
        ts2_base = dados['ts2'] if dados['ts2'] is not None else (existente.ts2 if existente else None)
        t90_base = dados['t90'] if dados['t90'] is not None else (existente.t90 if existente else None)

        temp_plato = (
            temps_reo[0]
            if temps_reo
            else (temps_visc[0] if temps_visc else (temps_merge[0] if temps_merge else (existente.temp_plato if existente else 0)))
        )

        usar_alta = bool(temp_plato and temp_plato >= 175)
        ts2_final = (ts2_alta if usar_alta else ts2_baixa) or (ts2_baixa if usar_alta else ts2_alta) or ts2_base
        t90_final = (t90_alta if usar_alta else t90_baixa) or (t90_baixa if usar_alta else t90_alta) or t90_base

        valor_visc = dados['visc']
        origem_visc = 'Real' if valor_visc is not None else 'N/A'
        if valor_visc is None and existente and existente.viscosidade is not None:
            valor_visc = existente.viscosidade
            origem_visc = existente.origem_viscosidade or 'Real'
        if valor_visc is None and dados['lote_visivel'] in medias_visc:
            valor_visc = medias_visc[dados['lote_visivel']]
            origem_visc = 'Media'

        data_hora = dados.get('data')
        if existente and existente.data_hora and (not data_hora or existente.data_hora > data_hora):
            data_hora = existente.data_hora

        metodo_id = _melhor_metodo_identificacao(
            existente.metodo_identificacao if existente else dados.get('metodo_id'),
            dados.get('metodo_id'),
        )

        novo_ensaio = EnsaioConsolidado(
            id_ensaio=id_destino,
            data_hora=data_hora,
            lote=dados['lote_visivel'],
            batch=dados['batch'],
            cod_sankhya=massa.cod_sankhya,
            massa_descricao=massa.descricao,
            temp_plato=temp_plato,
            ts2=ts2_final,
            t90=t90_final,
            ts2_alta=ts2_alta,
            t90_alta=t90_alta,
            ts2_baixa=ts2_baixa,
            t90_baixa=t90_baixa,
            reometro_alta=dados.get('reometro_alta') or (existente.reometro_alta if existente else None),
            reometro_baixa=dados.get('reometro_baixa') or (existente.reometro_baixa if existente else None),
            viscosidade=valor_visc,
            origem_viscosidade=origem_visc,
            ids_agrupados=json.dumps(ids_merge),
            temps_plato=json.dumps(temps_merge),
            temp_reo=(temps_reo[0] if temps_reo else (existente.temp_reo if existente else None)),
            temp_visc=(temps_visc[0] if temps_visc else (existente.temp_visc if existente else None)),
            ids_reo=json.dumps(ids_reo),
            ids_visc=json.dumps(ids_visc),
            metodo_identificacao=metodo_id,
            lote_original=dados.get('lote_original') or (existente.lote_original if existente else None),
            material_original=dados.get('material_original') or (existente.material_original if existente else None),
            updated_at=datetime.now(),
        )

        if engine:
            resultado = engine.calcular(novo_ensaio)
            novo_ensaio.score_final = resultado.score
            novo_ensaio.acao_recomendada = resultado.acao
            lista_historico.append(resultado)
            ids_ensaios_processados.append(novo_ensaio.id_ensaio)
        else:
            novo_ensaio.score_final = 0
            novo_ensaio.acao_recomendada = 'SEM ENGINE'

        lista_consolidada.append(novo_ensaio)

    stage_timings['consolidacao'] = perf_counter() - t_consolidacao
    t_persistencia = perf_counter()
    try:
        print(f"   Salvando {len(lista_consolidada)} registros consolidados...")

        count = 0
        for ensaio in lista_consolidada:
            db.session.merge(ensaio)
            count += 1
            if count % 1000 == 0:
                db.session.commit()
        db.session.commit()

        ids_ensaios_processados = sorted({int(i) for i in ids_ensaios_processados if i is not None})

        if engine and ids_ensaios_processados:
            print("   Limpando historico anterior...")
            batch_size = 900

            for lote_ids in chunk_list(ids_ensaios_processados, batch_size):
                db.session.query(ScoreResultado).filter(
                    ScoreResultado.id_versao == engine.versao.id,
                    ScoreResultado.id_ensaio.in_(lote_ids),
                ).delete(synchronize_session=False)

            db.session.commit()

            print("   Inserindo novos resultados...")
            for lote_res in chunk_list(lista_historico, batch_size):
                db.session.add_all(lote_res)
                db.session.commit()

        _salvar_estado_etl(novo_max_cod)
        stage_timings['persistencia'] = perf_counter() - t_persistencia
        print("Dados persistidos com sucesso!")

    except Exception as e:
        stage_timings['persistencia'] = perf_counter() - t_persistencia
        db.session.rollback()
        print(f"Erro ao salvar no banco: {e}")
        return None

    total_time = (datetime.now() - start_time).total_seconds()
    stage_timings['total'] = perf_counter() - t_total
    print(
        "[PERF][ETL] "
        f"sql={stage_timings['sql']:.2f}s "
        f"agrupamento={stage_timings['agrupamento']:.2f}s "
        f"consolidacao={stage_timings['consolidacao']:.2f}s "
        f"persistencia={stage_timings['persistencia']:.2f}s "
        f"total={stage_timings['total']:.2f}s "
        f"brutos={len(resultados_brutos)} consolidados={len(lista_consolidada)}"
    )
    return {
        'total': len(lista_consolidada),
        'total_bruto': len(resultados_brutos),
        'tempo': total_time,
        'incremental': bool(apenas_novos),
        'ids_processados': ids_ensaios_processados,
        'timings': stage_timings,
    }


def get_catalogo_codigo():
    return _CATALOGO_CODIGO
