from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from models.usuario import db
from models.consolidado import EnsaioConsolidado # Novo Modelo

from collections import Counter
from datetime import datetime, timedelta
import math
import os
import statistics 
import sqlite3 # Adicionado para conexão local
import json
import re
import unicodedata
from urllib.parse import urlparse, urljoin
from sqlalchemy import or_, func, case, desc, and_ # Adicionado para conexÃƒÂ£o local
from sqlalchemy.orm import load_only

# ConfiguraÃƒÂ§ÃƒÂµes e Modelos
from services.config_manager import carregar_configuracoes
from services.learning_service import carregar_aprendizado_mapa
from services.report_service import gerar_estrutura_relatorio
from models.score_versioning import ScoreResultado
from models.formula import Formula
from models.formulation_v2 import (
    Formulation as FormulationV2,
    FormulationIngredient as FormulationIngredientV2,
    ProcessParameters as ProcessParametersV2,
    MeasuredProperties as MeasuredPropertiesV2,
    OptimizationHistory as OptimizationHistoryV2,
)
try:
    from services.simulador_ia_service import SimuladorIAService
except Exception as e:
    SimuladorIAService = None
    print(f"Aviso: Servico de simulacao IA indisponivel: {e}")

try:
    from services.theory_engine import hardness_prior_details
except Exception as e:
    hardness_prior_details = None
    print(f"Aviso: Motor teorico de dureza indisponivel: {e}")

# --- R&D Services ---
try:
    from services.multi_target_simulator import MultiTargetSimulator
    from services.knowledge_service import KnowledgeService
    from services.search_service import SearchService
    from models.knowledge import KnowledgeRule
except Exception as e:
    print(f"Aviso: Servicos R&D indisponiveis: {e}")
    MultiTargetSimulator = None
    KnowledgeService = None
    SearchService = None


# --- IMPORTAÃƒâ€¡ÃƒÆ’O: SERVIÃƒâ€¡O DE ETL ---
from services.etl_service import (
    processar_carga_dados, 
    carregar_referencias_estaticas,
    recarregar_aprendizado_memoria,
    extrair_lote_da_string,
    get_catalogo_codigo,
    _MAPA_GRUPOS
)

# --- NOVA IMPORTAÃƒâ€¡ÃƒÆ’O: SHAREPOINT LOADER ---
try:
    from sharepoint_loader import baixar_excel_sharepoint
except ImportError:
    baixar_excel_sharepoint = None
    print("[WARN] Aviso: 'sharepoint_loader.py' nao encontrado. O download automatico sera desativado.")

from reoscore.use_cases.learning_corrections import (
    apply_learning_overlay,
    apply_lot_cleanup_to_consolidated,
    apply_persisted_corrections_to_consolidated,
)
from reoscore.webapp import create_app
from reoscore.webapp.extensions import cache_service, formulation_engine_service
from reoscore.webapp.runtime import (
    bootstrap_operacional,
    preparar_planilha_sharepoint,
    recarregar_cache_memoria as _recarregar_cache_memoria,
)

# ÃƒÂ£o para o cache baixado do SharePoint

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def _parse_float_locale(value, default=None):
    if value is None:
        return default

    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return default

    s = str(value).strip()
    if not s:
        return default

    # Aceita formato BR e internacional:
    # 0,5 | 0.5 | 1.234,56 | 1,234.56
    s = s.replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return default


def _parse_int_locale(value, default=None, min_value=None):
    parsed = _parse_float_locale(value, default=None)
    if parsed is None:
        return default

    try:
        parsed_int = int(parsed)
    except Exception:
        return default

    if min_value is not None and parsed_int < min_value:
        return min_value
    return parsed_int

def recarregar_cache_memoria():
    return _recarregar_cache_memoria(overlay_fn=globals().get('aplicar_sobreposicao_local'))

from connection import connect_to_database

app = create_app()

# ==========================================
# 0. CONFIGURACAO DE APRENDIZADO (SQLALCHEMY)
# ==========================================

def aplicar_sobreposicao_local(dados_brutos):
    return apply_learning_overlay(dados_brutos)

    """
    LÃƒÂª as regras de aprendizado no SQLAlchemy e aplica em memÃƒÂ³ria sobre os ensaios.
    """
    try:
        mapa_correcoes = carregar_aprendizado_mapa()
        if not mapa_correcoes:
            return dados_brutos

        count = 0
        for ensaio in dados_brutos:
            lote_orig = str(getattr(ensaio, 'lote_original', '')).strip().upper()
            mat_orig = str(getattr(ensaio, 'material_original', '')).strip().upper()
            lote_compacto = _compact_lote_key(lote_orig)
            mat_compacto = _compact_lote_key(mat_orig)

            regra = (
                mapa_correcoes.get(lote_orig)
                or mapa_correcoes.get(lote_compacto)
                or mapa_correcoes.get(mat_orig)
                or mapa_correcoes.get(mat_compacto)
            )
            if not regra:
                continue

            ensaio.lote = regra.get('lote_real') or ensaio.lote
            massa_corrigida = regra.get('massa')
            if massa_corrigida:
                ensaio.massa_descricao = massa_corrigida

            ensaio.metodo_identificacao = "MANUAL"
            count += 1

        print(f"Sobrescrita de aprendizado: {count} registros corrigidos em memÃƒÂ³ria via SQLAlchemy.")
        return dados_brutos

    except Exception as e:
        print(f"[WARN] Erro ao aplicar correcoes de aprendizado: {e}")
        return dados_brutos


def _norm_upper(valor):
    return str(valor or "").strip().upper()


def _compact_lote_key(valor):
    return "".join(ch for ch in _norm_upper(valor) if ch.isalnum())


def aplicar_correcoes_persistidas_no_consolidado(chaves_alvo=None):
    """
    Aplica correcoes manuais persistidas diretamente no ensaio_consolidado para
    refletir as mudancas sem necessidade de ETL completo.
    """
    mapa_correcoes = carregar_aprendizado_mapa()
    if not mapa_correcoes:
        return 0

    if chaves_alvo:
        chaves = {_norm_upper(c) for c in chaves_alvo if _norm_upper(c)}
    else:
        chaves = set(mapa_correcoes.keys())

    if not chaves:
        return 0

    chaves_compactas = {_compact_lote_key(c) for c in chaves if _compact_lote_key(c)}
    lote_original_compacto = func.upper(
        func.replace(
            func.replace(
                func.replace(
                    func.replace(EnsaioConsolidado.lote_original, " ", ""),
                    "-", "",
                ),
                "/",
                "",
            ),
            ".",
            "",
        )
    )

    rows = (
        EnsaioConsolidado.query
        .filter(
            or_(
                func.upper(EnsaioConsolidado.lote_original).in_(list(chaves)),
                lote_original_compacto.in_(list(chaves_compactas)),
            )
        )
        .all()
    )

    alterados = 0
    for ensaio in rows:
        chave = _norm_upper(ensaio.lote_original)
        regra = mapa_correcoes.get(chave) or mapa_correcoes.get(_compact_lote_key(chave))
        if not regra:
            continue

        novo_lote = _norm_upper(regra.get('lote_real')) or ensaio.lote
        nova_massa = _norm_upper(regra.get('massa')) or ensaio.massa_descricao

        mudou = False
        if novo_lote and ensaio.lote != novo_lote:
            ensaio.lote = novo_lote
            mudou = True

        if nova_massa and _norm_upper(ensaio.massa_descricao) != nova_massa:
            ensaio.massa_descricao = nova_massa
            mudou = True

        if ensaio.metodo_identificacao != "MANUAL":
            ensaio.metodo_identificacao = "MANUAL"
            mudou = True

        if mudou:
            ensaio.updated_at = datetime.now()
            alterados += 1

    if alterados:
        db.session.commit()

    return alterados


def _score_lote_extraido(candidato, origem):
    c = str(candidato or '').strip()
    if not c:
        return (99, 99, 99, 99)
    origem_rank = 0 if origem in {"Asterisco", "Exato", "Regex"} else 1
    faixa = 0 if 4 <= len(c) <= 7 else (1 if len(c) <= 10 else 2)
    zeros_fim = 1 if re.search(r'0{3,}$', c) else 0
    return (origem_rank, faixa, zeros_fim, len(c))


def aplicar_limpeza_lotes_no_consolidado():
    """
    Reaplica a engine de limpeza de lote diretamente nos dados consolidados
    para refletir melhorias de parser sem precisar de ETL completo.
    """
    rows = (
        EnsaioConsolidado.query
        .filter(
            EnsaioConsolidado.lote_original.isnot(None),
            EnsaioConsolidado.metodo_identificacao.in_(["TEXTO", "FANTASMA"]),
        )
        .all()
    )

    alterados = 0
    for ensaio in rows:
        cand_orig, origem_orig = extrair_lote_da_string(ensaio.lote_original or ensaio.lote)
        cand_mat, origem_mat = extrair_lote_da_string(ensaio.material_original)

        lote_limpo = None
        if cand_orig and cand_mat:
            lote_limpo = cand_orig if _score_lote_extraido(cand_orig, origem_orig) <= _score_lote_extraido(cand_mat, origem_mat) else cand_mat
        else:
            lote_limpo = cand_orig or cand_mat

        if not lote_limpo:
            continue

        lote_limpo = _norm_upper(lote_limpo)
        if lote_limpo and _norm_upper(ensaio.lote) != lote_limpo:
            ensaio.lote = lote_limpo
            ensaio.updated_at = datetime.now()
            alterados += 1

    if alterados:
        db.session.commit()

    return alterados


# ==========================================
# 1. INICIALIZACAO E CACHE
# ==========================================
bootstrap_operacional(
    app,
    baixar_excel_sharepoint,
    carregar_referencias_estaticas,
    recarregar_cache_fn=recarregar_cache_memoria,
)

# ==========================================
# 3. ROTAS PRINCIPAIS (DASHBOARD)
# ==========================================

@app.route('/atualizar_dados')
@login_required
def rota_atualizar():
    try:
        # --- PASSO 1: DOWNLOAD SHAREPOINT ---
        if baixar_excel_sharepoint:
            caminho_baixado = preparar_planilha_sharepoint(baixar_excel_sharepoint, forcar_download=True)
            if caminho_baixado:
                flash("Ã¢Å“â€¦ Planilha baixada do SharePoint com sucesso!", "success")
            else:
                flash("Ã¢Å¡Â Ã¯Â¸Â Falha no download do SharePoint. Usando cache.", "warning")

        # --- PASSO 2: EXECUÃƒâ€¡ÃƒÆ’O DO ETL ---
        stats = processar_carga_dados()
        
        if stats:
            # --- PASSO 3 (FIX): RECARREGAR O CACHE DA APLICAÃƒâ€¡ÃƒÆ’O ---
            qtd_cache = recarregar_cache_memoria() 
            
            total = stats.get('total', 0)
            tempo = stats.get('tempo', 0)
            flash(f"Base atualizada! {total} processados no ETL e {qtd_cache} carregados no Cache ({tempo:.1f}s).", "info")
        else:
            flash("Erro ao processar carga de dados (ETL retornou vazio).", "danger")
            
    except Exception as e:
        print(f"[ERRO] Erro critico na rota Atualizar: {e}")
        flash(f"Erro crÃƒÂ­tico: {str(e)}", "danger")

    next_url = request.args.get('next') or request.referrer
    if next_url and _is_safe_redirect_url(next_url):
        return redirect(next_url)

    return redirect(url_for('dashboard_home'))


@app.route('/aplicar_correcoes')
@login_required
def rota_aplicar_correcoes():
    try:
        recarregar_aprendizado_memoria()
        total_ajustados = apply_persisted_corrections_to_consolidated()
        total_lotes_limpos = apply_lot_cleanup_to_consolidated()
        qtd_cache = recarregar_cache_memoria()

        flash(
            f"Correcoes aplicadas: {total_ajustados} registros manuais, "
            f"{total_lotes_limpos} lotes limpos e cache com {qtd_cache} registros.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] Falha ao aplicar correcoes: {e}")
        flash(f"Erro ao aplicar correcoes: {e}", "danger")

    next_url = request.args.get('next') or request.referrer
    if next_url and _is_safe_redirect_url(next_url):
        return redirect(next_url)

    return redirect(url_for('admin.pagina_config', _anchor='ensinar'))

@app.route('/')
@login_required
def dashboard_home():
    """
    ROTA ESTRATÃƒâ€°GICA: VisÃƒÂ£o geral de KPIs e GrÃƒÂ¡ficos Gerenciais.
    NÃƒÂ£o carrega a lista de 50k registros, focando em agregaÃƒÂ§ÃƒÂµes rÃƒÂ¡pidas.
    """
    # Filtros de PerÃƒÂ­odo (ÃƒÅ¡nico filtro relevante para o Dashboard Global)
    d_start = request.args.get('date_start', '')
    d_end = request.args.get('date_end', '')
    
    query = EnsaioConsolidado.query
    
    if d_start:
        try: query = query.filter(EnsaioConsolidado.data_hora >= datetime.strptime(d_start, '%Y-%m-%d'))
        except: pass
    if d_end:
        try: query = query.filter(EnsaioConsolidado.data_hora <= datetime.strptime(d_end, '%Y-%m-%d').replace(hour=23, minute=59))
        except: pass

    # --- CÃƒÂLCULO DE KPIS (Agregado) ---
    stats = query.with_entities(
        func.count(EnsaioConsolidado.id_ensaio).label('total'),
        func.avg(EnsaioConsolidado.score_final).label('score_medio'),
        func.sum(case((or_(EnsaioConsolidado.acao_recomendada.like('%PRIME%'), EnsaioConsolidado.acao_recomendada == 'LIBERAR'), 1), else_=0)).label('aprovados'),
        func.sum(case((or_(EnsaioConsolidado.acao_recomendada.like('%RESSALVA%'), EnsaioConsolidado.acao_recomendada.like('%CORTAR%')), 1), else_=0)).label('ressalvas'),
        func.sum(case((EnsaioConsolidado.acao_recomendada.like('%REPROVAR%'), 1), else_=0)).label('reprovados')
    ).first()

    total = stats.total or 0
    kpi = {
        'total': total,
        'icg': ((stats.aprovados or 0) + (stats.ressalvas or 0)) / total * 100 if total > 0 else 0,
        'qsm': stats.score_medio or 0,
        'aprovados': stats.aprovados or 0,
        'ressalvas': stats.ressalvas or 0,
        'reprovados': stats.reprovados or 0
    }

    # --- GRÃƒÂFICO DE TENDÃƒÅ NCIA (ÃƒÅ¡ltimos 30 dias com dados) ---
    trend_data = query.with_entities(
        func.strftime('%Y-%m-%d', EnsaioConsolidado.data_hora).label('dia'),
        func.avg(EnsaioConsolidado.score_final).label('media')
    ).group_by('dia').order_by(desc('dia')).limit(30).all()
    
    # Reverte para cronolÃƒÂ³gico
    trend_data = trend_data[::-1] 
    
    chart_trend = {
        'labels': [t.dia[5:] for t in trend_data], # MM-DD
        'data': [round(t.media, 1) for t in trend_data]
    }

    # --- PARETO DE OFENSORES (Simplificado para o Dashboard) ---
    # Analisa falhas nos registros recentes REPROVADOS
    subquery_ids = query.filter(EnsaioConsolidado.score_final < 70).with_entities(EnsaioConsolidado.id_ensaio).order_by(EnsaioConsolidado.data_hora.desc()).limit(100).subquery()
    
    logs = db.session.query(ScoreResultado.detalhes_log).filter(ScoreResultado.id_ensaio.in_(subquery_ids)).all()
    ofensores = {}
    
    for row in logs:
        if not row.detalhes_log: continue
        params = row.detalhes_log.get('params', row.detalhes_log)
        for p, info in params.items():
            if isinstance(info, dict) and info.get('nota', 100) < 70:
                ofensores[p] = ofensores.get(p, 0) + 1
    
    pareto_sorted = sorted(ofensores.items(), key=lambda x: x[1], reverse=True)[:5] # Top 5
    chart_pareto = {
        'labels': [x[0] for x in pareto_sorted],
        'data': [x[1] for x in pareto_sorted]
    }

    last_update_obj = EnsaioConsolidado.query.order_by(EnsaioConsolidado.updated_at.desc()).first()
    
    return render_template(
        'dashboard.html',
        kpi=kpi,
        chart_trend=chart_trend,
        chart_pareto=chart_pareto,
        date_start=d_start,
        date_end=d_end,
        ultimo_update=last_update_obj.updated_at if last_update_obj else None
    )

@app.route('/qualidade')
@login_required
def controle_qualidade():
    """
    ROTA OPERACIONAL: Tabela de Lotes, Filtros AvanÃƒÂ§ados, Busca.
    (Antiga dashboard, agora focada na lista)
    """
    is_htmx = bool(request.headers.get('HX-Request'))

    # Filtros
    search = request.args.get('search', '').strip().upper()
    f_mat = request.args.get('material_filter', '')
    f_acao = request.args.get('acao_filter', '')
    d_start = request.args.get('date_start', '')
    d_end = request.args.get('date_end', '')
    f_codigo = request.args.get('codigo_filter', '').strip()
    f_tipo_ensaio = request.args.get('tipo_ensaio', '')
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    page = max(request.args.get('page', 1, type=int), 1)
    LIMIT = 50 # Mais itens por pÃƒÂ¡gina na visÃƒÂ£o operacional

    # Carrega apenas campos usados na tabela para reduzir custo por pÃƒÂ¡gina.
    query = EnsaioConsolidado.query.options(load_only(
        EnsaioConsolidado.id_ensaio,
        EnsaioConsolidado.data_hora,
        EnsaioConsolidado.lote,
        EnsaioConsolidado.batch,
        EnsaioConsolidado.cod_sankhya,
        EnsaioConsolidado.massa_descricao,
        EnsaioConsolidado.temp_plato,
        EnsaioConsolidado.temp_reo,
        EnsaioConsolidado.temp_visc,
        EnsaioConsolidado.ts2,
        EnsaioConsolidado.t90,
        EnsaioConsolidado.viscosidade,
        EnsaioConsolidado.origem_viscosidade,
        EnsaioConsolidado.reometro_alta,
        EnsaioConsolidado.reometro_baixa,
        EnsaioConsolidado.score_final,
        EnsaioConsolidado.acao_recomendada,
        EnsaioConsolidado.metodo_identificacao,
        EnsaioConsolidado.ids_agrupados,
        EnsaioConsolidado.temps_plato
    ))

    if search:
        query = query.filter(or_(
            EnsaioConsolidado.lote.contains(search),
            EnsaioConsolidado.massa_descricao.contains(search),
            EnsaioConsolidado.batch.contains(search)
        ))
    if f_mat:
        query = query.filter(EnsaioConsolidado.massa_descricao == f_mat)

    if f_acao:
        if f_acao == "APROVADOS":
            query = query.filter(EnsaioConsolidado.score_final >= 70) # SimplificaÃƒÂ§ÃƒÂ£o
        elif f_acao == "REPROVADO":
            query = query.filter(EnsaioConsolidado.score_final < 70)

    if d_start:
        try:
            query = query.filter(EnsaioConsolidado.data_hora >= datetime.strptime(d_start, '%Y-%m-%d'))
        except Exception:
            pass
    if d_end:
        try:
            query = query.filter(EnsaioConsolidado.data_hora <= datetime.strptime(d_end, '%Y-%m-%d').replace(hour=23, minute=59))
        except Exception:
            pass

    if f_codigo.isdigit():
        query = query.filter(EnsaioConsolidado.cod_sankhya == int(f_codigo))

    if f_tipo_ensaio == 'REO':
        query = query.filter(EnsaioConsolidado.ts2.isnot(None), EnsaioConsolidado.t90.isnot(None))
    elif f_tipo_ensaio == 'VISC':
        query = query.filter(EnsaioConsolidado.viscosidade.isnot(None))

    # OrdenaÃƒÂ§ÃƒÂ£o
    col_map = {
        'id': EnsaioConsolidado.id_ensaio, 'data': EnsaioConsolidado.data_hora,
        'material': EnsaioConsolidado.massa_descricao,
        'lote': EnsaioConsolidado.lote, 'temp': EnsaioConsolidado.temp_plato,
        'ts2': EnsaioConsolidado.ts2, 't90': EnsaioConsolidado.t90,
        'visc': EnsaioConsolidado.viscosidade,
        'score': EnsaioConsolidado.score_final, 'acao': EnsaioConsolidado.acao_recomendada
    }
    col = col_map.get(sort_by, EnsaioConsolidado.data_hora)
    query = query.order_by(col.asc() if order == 'asc' else col.desc())

    # PaginaÃƒÂ§ÃƒÂ£o
    paginacao = query.paginate(page=page, per_page=LIMIT, error_out=False)
    
    # Filtros auxiliares: sÃƒÂ³ no render completo; paginaÃƒÂ§ÃƒÂ£o HTMX nÃƒÂ£o usa o select de materiais.
    materiais_filtro = []
    if not is_htmx:
        materiais = db.session.query(EnsaioConsolidado.massa_descricao).distinct().order_by(EnsaioConsolidado.massa_descricao).all()
        materiais_filtro = [{'descricao': m[0]} for m in materiais if m[0]]

    context = {
        'ensaios': paginacao.items,
        'total_registros_filtrados': paginacao.total,
        'pagina_atual': page, 'total_paginas': paginacao.pages,
        'materiais_filtro': materiais_filtro,
        'codigo_filter': f_codigo, 'date_start': d_start, 'date_end': d_end, 'tipo_ensaio_filter': f_tipo_ensaio,
        'search_term': search, 'material_filter': f_mat, 'acao_filter': f_acao,
        'sort_by': sort_by, 'order': order
    }

    if is_htmx:
        return render_template('tabela_dados.html', **context)
        
    return render_template('controle_qualidade.html', **context)


@app.route('/analise-tendencias')
@login_required
def analise_tendencias():
    massas = _listar_massas_tendencia()
    parametros = _listar_parametros_tendencia()

    cod_default = request.args.get('cod_sankhya', type=int)
    if cod_default is None and massas:
        cod_default = massas[0]['cod_sankhya']

    param_default = request.args.get('parametro', '')
    if param_default:
        param_default = _normalizar_metadados_parametro(param_default).get('key')
    elif parametros:
        param_default = parametros[0]['key']
    else:
        param_default = 't90_alta'

    periodo_default = str(request.args.get('periodo', '3m') or '3m').strip().lower()
    if periodo_default not in PERIODOS_TENDENCIA_DIAS:
        periodo_default = '3m'

    return render_template(
        'analise_tendencias.html',
        massas=massas,
        parametros=parametros,
        cod_sankhya_default=cod_default,
        parametro_default=param_default,
        periodo_default=periodo_default,
    )


@app.route('/api/estatisticas/massa/<int:cod_sankhya>/<string:parametro>')
@login_required
def api_estatisticas_massa(cod_sankhya, parametro):
    try:
        meta_param = _normalizar_metadados_parametro(parametro)
        periodo, data_inicio = _periodo_para_data_inicio(request.args.get('periodo', '3m'))
        temperatura_raw = str(request.args.get('temperatura', 'auto') or 'auto').strip().lower()

        agrupamento = str(request.args.get('agrupar', 'semana') or 'semana').strip().lower()
        if agrupamento not in ('semana', 'mes'):
            agrupamento = 'semana'

        query = EnsaioConsolidado.query.filter(EnsaioConsolidado.cod_sankhya == cod_sankhya)
        if data_inicio is not None:
            query = query.filter(EnsaioConsolidado.data_hora >= data_inicio)

        ensaios = query.order_by(EnsaioConsolidado.data_hora.asc()).all()

        massa_desc = None
        if ensaios:
            massa_desc = str(getattr(ensaios[0], 'massa_descricao', '') or '').strip()
        if not massa_desc:
            massa_desc = f"Massa {cod_sankhya}"

        dados_brutos = []
        limites_freq_total = Counter()
        temperaturas_freq = Counter()

        for ensaio in ensaios:
            faixa_temp = meta_param.get('faixa_temp')
            if faixa_temp and _faixa_temperatura_ensaio(ensaio) != faixa_temp:
                continue

            valor = _valor_medido_por_parametro(ensaio, meta_param)
            data_hora = getattr(ensaio, 'data_hora', None)
            if valor is None or data_hora is None:
                continue

            temperatura_analise = _temperatura_analise_ensaio(ensaio, meta_param)
            temperatura_key = int(round(temperatura_analise)) if temperatura_analise is not None else None
            if temperatura_key is not None:
                temperaturas_freq[temperatura_key] += 1

            is_aprovado = _ensaio_aprovado(ensaio)
            status_item = 'APROVADO' if is_aprovado else 'REPROVADO'
            cor_item = '#198754' if is_aprovado else '#dc3545'

            lie, lse, alvo, perfil_nome, nome_spec = _limites_parametro_para_ensaio(ensaio, meta_param)
            if lie is not None or lse is not None or alvo is not None:
                limites_freq_total[(lie, lse, alvo)] += 1

            dados_brutos.append({
                'ensaio_obj': ensaio,
                'id_ensaio': int(ensaio.id_ensaio),
                'data_iso': data_hora.isoformat(),
                'data_hora': data_hora,
                'valor': valor,
                'status': status_item,
                'aprovado': is_aprovado,
                'cor': cor_item,
                'lote': str(getattr(ensaio, 'lote', '') or ''),
                'batch': str(getattr(ensaio, 'batch', '') or ''),
                'faixa_temperatura': _faixa_temperatura_ensaio(ensaio),
                'temperatura_analise': temperatura_analise,
                'temperatura_key': temperatura_key,
                'perfil': perfil_nome,
                'parametro_spec': nome_spec,
                'lie': lie,
                'lse': lse,
                'alvo': alvo,
            })

        temperaturas_disponiveis = sorted(temperaturas_freq.keys())
        temperatura_aplicada = None
        temperatura_modo = 'sem_temperatura'
        aviso_temperatura = ''

        if temperaturas_disponiveis:
            temperatura_modo = 'auto'
            if temperatura_raw not in ('', 'auto', 'mais_frequente'):
                temp_manual = _parse_float_locale(temperatura_raw, default=None)
                if temp_manual is not None:
                    temp_manual_key = int(round(temp_manual))
                    if temp_manual_key in temperaturas_freq:
                        temperatura_aplicada = temp_manual_key
                        temperatura_modo = 'manual'
                    else:
                        aviso_temperatura = (
                            f"Temperatura {temp_manual_key} C nao encontrada para os filtros atuais. "
                            "Aplicado valor automatico."
                        )
                else:
                    aviso_temperatura = "Temperatura invalida informada. Aplicado valor automatico."

            if temperatura_aplicada is None:
                temperatura_aplicada = temperaturas_freq.most_common(1)[0][0]

        dados_filtrados = [
            item for item in dados_brutos
            if temperatura_aplicada is None or item['temperatura_key'] == temperatura_aplicada
        ]

        datas = []
        valores = []
        status = []
        cores = []
        pontos = []
        boxplot_map = {}
        pareto = Counter()
        limites_freq = Counter()

        aprovados = 0
        reprovados = 0

        for item in dados_filtrados:
            data_hora = item['data_hora']

            if item['aprovado']:
                aprovados += 1
            else:
                reprovados += 1
                pareto.update(_motivos_reprovacao_ensaio(item['ensaio_obj']))

            lie = item.get('lie')
            lse = item.get('lse')
            alvo = item.get('alvo')
            if lie is not None or lse is not None or alvo is not None:
                limites_freq[(lie, lse, alvo)] += 1

            datas.append(item['data_iso'])
            valores.append(item['valor'])
            status.append(item['status'])
            cores.append(item['cor'])

            grupo = _nome_grupo_boxplot(data_hora, agrupamento=agrupamento)
            if grupo:
                boxplot_map.setdefault(grupo, []).append(item['valor'])

            pontos.append({
                'id_ensaio': item['id_ensaio'],
                'data_hora': item['data_iso'],
                'valor': item['valor'],
                'status': item['status'],
                'aprovado': item['aprovado'],
                'lote': item['lote'],
                'batch': item['batch'],
                'faixa_temperatura': item['faixa_temperatura'],
                'temperatura_analise': item['temperatura_analise'],
                'perfil': item['perfil'],
                'parametro_spec': item['parametro_spec'],
            })

        if limites_freq:
            (lie_final, lse_final, alvo_final), _ = limites_freq.most_common(1)[0]
        elif limites_freq_total:
            (lie_final, lse_final, alvo_final), _ = limites_freq_total.most_common(1)[0]
        else:
            lie_final, lse_final, alvo_final, _, _ = _limites_parametro_na_config(cod_sankhya, meta_param)

        qtd_valores = len(valores)
        media_valores = statistics.mean(valores) if qtd_valores else None
        desvio_padrao = statistics.stdev(valores) if qtd_valores >= 2 else None

        cp = None
        cpk = None
        cpl = None
        cpu = None
        if desvio_padrao and desvio_padrao > 0:
            if lie_final is not None and lse_final is not None and lse_final > lie_final:
                cp = (lse_final - lie_final) / (6 * desvio_padrao)
            if lie_final is not None and media_valores is not None:
                cpl = (media_valores - lie_final) / (3 * desvio_padrao)
            if lse_final is not None and media_valores is not None:
                cpu = (lse_final - media_valores) / (3 * desvio_padrao)

            if cpl is not None and cpu is not None:
                cpk = min(cpl, cpu)
            elif cpl is not None:
                cpk = cpl
            elif cpu is not None:
                cpk = cpu

        boxplot = [
            {'grupo': grupo, 'valores': boxplot_map[grupo]}
            for grupo in sorted(boxplot_map.keys())
        ]

        pareto_lista = [
            {'motivo': motivo, 'quantidade': quantidade}
            for motivo, quantidade in pareto.most_common(10)
        ]

        mensagens = []
        if aviso_temperatura:
            mensagens.append(aviso_temperatura)
        if not pontos:
            mensagens.append(
                f"Sem dados de {meta_param.get('label')} para a massa {cod_sankhya} "
                f"no periodo selecionado."
            )
        mensagem = " ".join(mensagens)

        return jsonify({
            'massa': {
                'cod_sankhya': cod_sankhya,
                'descricao': massa_desc,
            },
            'parametro': {
                'solicitado': parametro,
                'chave': meta_param.get('key'),
                'label': meta_param.get('label'),
                'faixa_temp': meta_param.get('faixa_temp'),
            },
            'temperatura': {
                'solicitada': temperatura_raw,
                'aplicada': temperatura_aplicada,
                'modo': temperatura_modo,
                'disponiveis': temperaturas_disponiveis,
            },
            'periodo': periodo,
            'agrupamento': agrupamento,
            'limites': {
                'lie': lie_final,
                'lse': lse_final,
                'alvo': alvo_final,
            },
            'resumo': {
                'total': len(pontos),
                'aprovados': aprovados,
                'reprovados': reprovados,
            },
            'datas': datas,
            'valores': valores,
            'status_aprovacao': status,
            'series': {
                'datas': datas,
                'valores': valores,
                'status': status,
                'cores': cores,
            },
            'distribuicao': {
                'valores': valores,
            },
            'capabilidade': {
                'n': qtd_valores,
                'media': media_valores,
                'desvio_padrao': desvio_padrao,
                'cp': cp,
                'cpk': cpk,
                'cpl': cpl,
                'cpu': cpu,
            },
            'pontos': pontos,
            'boxplot': boxplot,
            'pareto_reprovacoes': pareto_lista,
            'mensagem': mensagem,
        })
    except Exception as exc:
        print(f"[ERRO] Falha na API de estatisticas de massa: {exc}")
        return jsonify({'error': str(exc)}), 500


@app.route('/api/grafico')
@login_required
def api_grafico():
    try:
        ids_str = request.args.get('ids', '')
        modo_lote = request.args.get('mode', '') == 'lote' 
        
        if not ids_str:
            return jsonify({})

        # 1. IDs das LINHAS selecionadas
        selected_parent_ids = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]
        
        # Limite dinÃƒÂ¢mico
        limite = 100 if modo_lote else 10
        
        if len(selected_parent_ids) > limite:
            return jsonify({'error': f'Muitos dados ({len(selected_parent_ids)}). Limite ÃƒÂ© {limite}.'}), 400

        all_ids_to_fetch = set()
        map_id_to_parent = {} 

        # 2. Metadados locais (SQLite / EnsaioConsolidado) + expansÃƒÂ£o via ids_agrupados do merge
        ensaios_base = (
            EnsaioConsolidado.query
            .filter(EnsaioConsolidado.id_ensaio.in_(selected_parent_ids))
            .all()
        )
        meta_by_id = {int(e.id_ensaio): e for e in ensaios_base}

        materiais = []
        for e in ensaios_base:
            desc = (e.massa_descricao or '').strip()
            if desc and desc not in materiais:
                materiais.append(desc)

        for parent_id in selected_parent_ids:
            parent_meta = meta_by_id.get(int(parent_id))
            if not parent_meta:
                all_ids_to_fetch.add(int(parent_id))
                continue

            ids_filhos = parent_meta.ids_agrupados_list or [int(parent_id)]
            for child_id in ids_filhos:
                try:
                    c_id_int = int(child_id)
                except Exception:
                    continue
                all_ids_to_fetch.add(c_id_int)
                map_id_to_parent[c_id_int] = parent_meta

        if not all_ids_to_fetch:
            return jsonify({'error': 'IDs nÃƒÂ£o encontrados no cache.'}), 404

        # 3. Busca SQL
        conn = connect_to_database()
        cursor = conn.cursor()
        
        lista_ids = sorted(list(all_ids_to_fetch))

        def _chunks(items, chunk_size):
            for i in range(0, len(items), chunk_size):
                yield items[i:i + chunk_size]

        def _rotulo_reometro(descricao_grupo):
            txt = str(descricao_grupo or '').strip().upper()
            if not txt:
                return None
            if 'PRETO' in txt:
                return 'PRETO'
            if 'BRANCO' in txt or 'CINZA' in txt:
                return 'BRANCO'
            return txt

        rows = []
        try:
            for chunk in _chunks(lista_ids, 2000):
                placeholders = ','.join(['?'] * len(chunk))
                query = f'''
                    SELECT 
                        V.COD_ENSAIO, 
                        V.TEMPO, 
                        V.TORQUE, 
                        E.TEMP_PLATO_INF,
                        E.COD_GRUPO
                    FROM dbo.ENSAIO_VALORES V
                    JOIN dbo.ENSAIO E ON V.COD_ENSAIO = E.COD_ENSAIO
                    WHERE V.COD_ENSAIO IN ({placeholders})
                    ORDER BY V.COD_ENSAIO, V.TEMPO
                '''
                cursor.execute(query, chunk)
                rows.extend(cursor.fetchall())
        finally:
            conn.close()

        if not rows:
            return jsonify({'error': 'Nenhum ponto de curva encontrado.'}), 404

        # 4. Processamento
        datasets_reo = {}
        datasets_visc = {}
        
        for row in rows:
            c_id = int(row[0])
            c_time = float(row[1])
            c_val = float(row[2])
            c_temp = float(row[3]) if row[3] else 0
            c_grupo = row[4]
            
            parent = map_id_to_parent.get(c_id)
            
            # ClassificaÃƒÂ§ÃƒÂ£o
            dados_grupo = _MAPA_GRUPOS.get(c_grupo, {})
            tipo_maquina = dados_grupo.get('tipo', 'INDEFINIDO')
            desc_grupo = dados_grupo.get('descricao')
            rotulo_reometro = _rotulo_reometro(desc_grupo)
            
            is_viscosity = False
            
            if tipo_maquina == 'VISCOSIMETRO': is_viscosity = True
            elif tipo_maquina == 'REOMETRO': is_viscosity = False
            else:
                if 90 <= c_temp <= 115: is_viscosity = True
                elif c_temp >= 120: is_viscosity = False
                else: is_viscosity = bool(getattr(parent, 'viscosidade', None) is not None)

            target_dict = datasets_visc if is_viscosity else datasets_reo
            
            if c_id not in target_dict:
                cod_s = getattr(parent, 'cod_sankhya', None) if parent else None
                cod_s = str(cod_s) if cod_s is not None else '??'

                batch_s = getattr(parent, 'batch', None) if parent else None
                batch_s = str(batch_s) if batch_s else '0'

                material_desc = getattr(parent, 'massa_descricao', None) if parent else None
                material_desc = (str(material_desc).strip() if material_desc else '')

                label = f"{cod_s} - Batch {batch_s} (ID {c_id})"
                
                # --- NOVO: ClassificaÃƒÂ§ÃƒÂ£o do Subtipo para o Filtro ---
                temp_type = 'GERAL'
                if not is_viscosity:
                    temp_type = 'ALTA' if c_temp >= 175 else 'BAIXA'
                # ----------------------------------------------------

                if not is_viscosity and rotulo_reometro:
                    label = f"{label} [{temp_type}/{rotulo_reometro}]"
                elif not is_viscosity:
                    label = f"{label} [{temp_type}]"

                target_dict[c_id] = {
                    'label': label,
                    'material': material_desc,
                    'tempType': temp_type, # <--- Enviando para o Frontend
                    'reometro': rotulo_reometro if not is_viscosity else None,
                    'data': [],
                    'pointRadius': 0,
                    'borderWidth': 2,
                    'tension': 0.4,
                    'fill': False,
                    # Cores dinÃƒÂ¢micas
                    'borderColor': '#dc3545' if temp_type == 'ALTA' else '#0d6efd'
                }
            
            target_dict[c_id]['data'].append({'x': c_time, 'y': c_val})

        ids_reo = sorted([int(k) for k in datasets_reo.keys()])
        ids_visc = sorted([int(k) for k in datasets_visc.keys()])
        ids_reo_alta = sorted([int(k) for k, v in datasets_reo.items() if (v.get('tempType') == 'ALTA')])
        ids_reo_baixa = sorted([int(k) for k, v in datasets_reo.items() if (v.get('tempType') == 'BAIXA')])
        reometros_alta = sorted({str(v.get('reometro')) for v in datasets_reo.values() if v.get('tempType') == 'ALTA' and v.get('reometro')})
        reometros_baixa = sorted({str(v.get('reometro')) for v in datasets_reo.values() if v.get('tempType') == 'BAIXA' and v.get('reometro')})

        return jsonify({
            'ids': lista_ids,
            'ids_reometria': ids_reo,
            'ids_reometria_alta': ids_reo_alta,
            'ids_reometria_baixa': ids_reo_baixa,
            'ids_viscosidade': ids_visc,
            'reometros_alta': reometros_alta,
            'reometros_baixa': reometros_baixa,
            'materiais': materiais,
            'reometria': list(datasets_reo.values()),
            'viscosidade': list(datasets_visc.values())
        })
        
    except Exception as e:
        print(f"ERRO API: {e}")
        return jsonify({'error': str(e)}), 500




# ==========================================
# ROTAS NOVAS (RELATÃƒâ€œRIOS E GESTÃƒÆ’O)
# ==========================================

MAPA_PROPRIEDADES_FISICAS = {
    'dureza': {
        'label': 'Dureza',
        'sigla': 'Dur',
        'unidade': 'Shore A',
        'spec_keys': ['Dureza', 'dureza'],
    },
    'densidade': {
        'label': 'Densidade',
        'sigla': 'Dens',
        'unidade': 'g/cm3',
        'spec_keys': ['Densidade', 'densidade'],
    },
    'abrasao': {
        'label': 'Abrasao',
        'sigla': 'Abr',
        'unidade': 'mm3',
        'spec_keys': ['Abrasao', 'AbrasÃƒÂ£o', 'abrasao'],
    },
    'resiliencia': {
        'label': 'Resiliencia',
        'sigla': 'Res',
        'unidade': '%',
        'spec_keys': ['Resiliencia', 'ResiliÃƒÂªncia', 'resiliencia'],
    },
    'tensao_ruptura': {
        'label': 'Tensao Ruptura',
        'sigla': 'TR',
        'unidade': 'MPa',
        'spec_keys': ['TensaoRuptura', 'Tensao Ruptura', 'TensÃƒÂ£o Ruptura', 'tensao_ruptura'],
    },
    'alongamento': {
        'label': 'Alongamento',
        'sigla': 'Along',
        'unidade': '%',
        'spec_keys': ['Alongamento', 'alongamento'],
    },
    'rasgo': {
        'label': 'Resistencia ao Rasgo',
        'sigla': 'Rasgo',
        'unidade': 'N/mm',
        'spec_keys': ['Rasgo', 'rasgo'],
    },
}


def _normalizar_chave_config(chave):
    txt = str(chave or '').strip().lower()
    txt = unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode('ascii')
    txt = txt.replace('-', '_').replace(' ', '_')
    while '__' in txt:
        txt = txt.replace('__', '_')
    return txt


def _to_float_or_none(valor):
    try:
        if valor is None:
            return None
        return float(valor)
    except Exception:
        return None


def _obter_limites_spec(spec):
    if spec is None:
        return None, None

    minimo = _to_float_or_none(getattr(spec, 'minimo', None))
    maximo = _to_float_or_none(getattr(spec, 'maximo', None))

    if isinstance(spec, dict):
        minimo = _to_float_or_none(spec.get('minimo', spec.get('min', minimo)))
        maximo = _to_float_or_none(spec.get('maximo', spec.get('max', maximo)))

    return minimo, maximo


PERIODOS_TENDENCIA_DIAS = {
    '30d': 30,
    '3m': 90,
    '6m': 180,
    '12m': 365,
    'all': None,
}

PARAMETROS_TENDENCIA_BASE = {
    'mh': {
        'label': 'MH',
        'attrs': ['mh'],
        'spec_keys': ['MH', 'Mh', 'mh'],
    },
    'ml': {
        'label': 'ML',
        'attrs': ['ml'],
        'spec_keys': ['ML', 'Ml', 'ml'],
    },
    'ts1': {
        'label': 'Ts1',
        'attrs': ['ts1', 'ts2'],
        'spec_keys': ['Ts1', 'TS1', 'ts1', 'Ts2', 'TS2', 'ts2'],
    },
    'ts2_alta': {
        'label': 'Ts2 (Alta temperatura)',
        'attrs': ['ts2', 'ts1'],
        'spec_keys': ['Ts2', 'TS2', 'ts2', 'Ts1', 'TS1', 'ts1'],
        'faixa_temp': 'alta',
    },
    'ts2_baixa': {
        'label': 'Ts2 (Baixa temperatura)',
        'attrs': ['ts2', 'ts1'],
        'spec_keys': ['Ts2', 'TS2', 'ts2', 'Ts1', 'TS1', 'ts1'],
        'faixa_temp': 'baixa',
    },
    't90_alta': {
        'label': 'T90 (Alta temperatura)',
        'attrs': ['t90'],
        'spec_keys': ['T90', 't90'],
        'faixa_temp': 'alta',
    },
    't90_baixa': {
        'label': 'T90 (Baixa temperatura)',
        'attrs': ['t90'],
        'spec_keys': ['T90', 't90'],
        'faixa_temp': 'baixa',
    },
    'viscosidade': {
        'label': 'Viscosidade',
        'attrs': ['viscosidade'],
        'spec_keys': ['Viscosidade', 'viscosidade', 'mooney', 'Mooney'],
    },
    'dureza': {
        'label': 'Dureza',
        'attrs': ['dureza'],
        'spec_keys': ['Dureza', 'dureza', 'Hardness', 'hardness'],
    },
    'densidade': {
        'label': 'Densidade',
        'attrs': ['densidade'],
        'spec_keys': ['Densidade', 'densidade', 'Density', 'density'],
    },
    'abrasao': {
        'label': 'Abrasao',
        'attrs': ['abrasao'],
        'spec_keys': ['Abrasao', 'Abrasão', 'abrasao', 'Abrasion', 'abrasion'],
    },
    'resiliencia': {
        'label': 'Resiliencia',
        'attrs': ['resiliencia'],
        'spec_keys': ['Resiliencia', 'Resiliência', 'resiliencia', 'Resilience', 'resilience'],
    },
    'tensao_ruptura': {
        'label': 'Tensao Ruptura',
        'attrs': ['tensao_ruptura', 'tensaoruptura'],
        'spec_keys': ['TensaoRuptura', 'Tensao Ruptura', 'Tensão Ruptura', 'tensao_ruptura', 'tensaoruptura'],
    },
    'alongamento': {
        'label': 'Alongamento',
        'attrs': ['alongamento'],
        'spec_keys': ['Alongamento', 'alongamento', 'Elongation', 'elongation'],
    },
    'rasgo': {
        'label': 'Rasgo',
        'attrs': ['rasgo'],
        'spec_keys': ['Rasgo', 'rasgo', 'Tear', 'tear'],
    },
    'modulo_100': {
        'label': 'Modulo 100',
        'attrs': ['modulo_100', 'modulo100'],
        'spec_keys': ['Modulo100', 'modulo_100', 'modulo100'],
    },
    'modulo_300': {
        'label': 'Modulo 300',
        'attrs': ['modulo_300', 'modulo300'],
        'spec_keys': ['Modulo300', 'modulo_300', 'modulo300'],
    },
}

ALIASES_PARAMETRO_TENDENCIA = {
    'ts_1': 'ts1',
    'ts': 'ts2',
    'ts_2': 'ts2',
    'ts2alta': 'ts2_alta',
    'ts2_alta_temperatura': 'ts2_alta',
    'ts2baixa': 'ts2_baixa',
    'ts2_baixa_temperatura': 'ts2_baixa',
    't90alta': 't90_alta',
    't90_alta_temperatura': 't90_alta',
    't90baixa': 't90_baixa',
    't90_baixa_temperatura': 't90_baixa',
    'mooney': 'viscosidade',
    'viscosidade_mooney': 'viscosidade',
    'mu': 'viscosidade',
    'hardness': 'dureza',
    'density': 'densidade',
    'abrasion': 'abrasao',
    'resilience': 'resiliencia',
    'tensaoruptura': 'tensao_ruptura',
    'modulo100': 'modulo_100',
    'modulo300': 'modulo_300',
}

VARIANTES_CHAVE_PARAMETRO = {
    'ts1': ['ts2'],
    'ts2': ['ts1'],
    'tensaoruptura': ['tensao_ruptura'],
    'tensao_ruptura': ['tensaoruptura'],
    'modulo100': ['modulo_100'],
    'modulo_100': ['modulo100'],
    'modulo300': ['modulo_300'],
    'modulo_300': ['modulo300'],
}

ORDEM_PARAMETROS_TENDENCIA = [
    'mh', 'ml', 'ts1', 'ts2_alta', 'ts2_baixa', 't90_alta', 't90_baixa', 'viscosidade',
    'dureza', 'densidade', 'abrasao', 'resiliencia', 'tensao_ruptura',
    'alongamento', 'rasgo', 'modulo_100', 'modulo_300',
]


def _obter_alvo_spec(spec):
    if spec is None:
        return None

    alvo = _to_float_or_none(getattr(spec, 'alvo', None))
    if isinstance(spec, dict):
        alvo = _to_float_or_none(spec.get('alvo', alvo))
    return alvo


def _periodo_para_data_inicio(periodo_raw):
    periodo = str(periodo_raw or '3m').strip().lower()
    if periodo not in PERIODOS_TENDENCIA_DIAS:
        periodo = '3m'

    dias = PERIODOS_TENDENCIA_DIAS.get(periodo)
    if dias is None:
        return periodo, None

    return periodo, datetime.now() - timedelta(days=dias)


def _faixa_temperatura_ensaio(ensaio):
    temp_ref = (
        _to_float_or_none(getattr(ensaio, 'temp_reo', None))
        or _to_float_or_none(getattr(ensaio, 'temp_plato', None))
        or _to_float_or_none(getattr(ensaio, 'temp_visc', None))
        or 0.0
    )
    return 'alta' if temp_ref >= 175 else 'baixa'


def _temperatura_analise_ensaio(ensaio, meta_param=None):
    chave_param = _normalizar_chave_config((meta_param or {}).get('key'))

    if chave_param.startswith('ts2') or chave_param.startswith('t90') or chave_param in ('ts1', 'mh', 'ml'):
        temp = _to_float_or_none(getattr(ensaio, 'temp_reo', None))
        if temp is not None:
            return temp

    if chave_param == 'viscosidade':
        temp = _to_float_or_none(getattr(ensaio, 'temp_visc', None))
        if temp is not None:
            return temp

    temp = _to_float_or_none(getattr(ensaio, 'temp_plato', None))
    if temp is not None:
        return temp

    temp = _to_float_or_none(getattr(ensaio, 'temp_reo', None))
    if temp is not None:
        return temp

    return _to_float_or_none(getattr(ensaio, 'temp_visc', None))


def _perfis_preferenciais_ensaio(ensaio, faixa_temp=None):
    faixa = faixa_temp or _faixa_temperatura_ensaio(ensaio)
    if faixa == 'alta':
        return ['alta_cinza', 'alta_preto', 'alta', 'baixa']
    return ['baixa', 'alta_cinza', 'alta_preto', 'alta']


def _chave_parametro_por_perfil(nome_norm, prefixo_perfil):
    if nome_norm not in ('ts2', 't90'):
        return nome_norm

    if prefixo_perfil == 'baixa_':
        return f"{nome_norm}_baixa"
    if prefixo_perfil in ('alta_cinza_', 'alta_preto_', 'alta_'):
        return f"{nome_norm}_alta"
    return nome_norm


def _variantes_chave_normalizada(chave):
    chave_norm = _normalizar_chave_config(chave)
    if not chave_norm:
        return []

    variantes = [chave_norm]
    sem_underscore = chave_norm.replace('_', '')
    if sem_underscore and sem_underscore not in variantes:
        variantes.append(sem_underscore)

    for extra in VARIANTES_CHAVE_PARAMETRO.get(chave_norm, []):
        extra_norm = _normalizar_chave_config(extra)
        if extra_norm and extra_norm not in variantes:
            variantes.append(extra_norm)

    return variantes


def _listar_parametros_tendencia():
    catalogo = {}
    for chave, meta in PARAMETROS_TENDENCIA_BASE.items():
        catalogo[chave] = {
            'key': chave,
            'label': meta['label'],
            'attrs': list(meta.get('attrs', [])),
            'spec_keys': list(meta.get('spec_keys', [])),
            'faixa_temp': meta.get('faixa_temp'),
        }

    try:
        configs = carregar_configuracoes() or {}
    except Exception:
        configs = {}

    for _cod, specs in configs.items():
        if not isinstance(specs, dict):
            continue

        for chave_cfg in specs.keys():
            chave_txt = str(chave_cfg or '')
            nome_param = None
            prefixo_encontrado = None
            for prefixo in ('alta_cinza_', 'alta_preto_', 'alta_', 'baixa_'):
                if chave_txt.startswith(prefixo):
                    nome_param = chave_txt[len(prefixo):]
                    prefixo_encontrado = prefixo
                    break

            if not nome_param:
                continue

            nome_norm = _normalizar_chave_config(nome_param)
            if nome_norm in ('temp_padrao', 'tempo_total'):
                continue
            nome_norm = ALIASES_PARAMETRO_TENDENCIA.get(nome_norm, nome_norm)
            if not nome_norm:
                continue

            chave_catalogo = _chave_parametro_por_perfil(nome_norm, prefixo_encontrado)

            if chave_catalogo not in catalogo:
                faixa_temp = None
                if str(chave_catalogo).endswith('_alta'):
                    faixa_temp = 'alta'
                elif str(chave_catalogo).endswith('_baixa'):
                    faixa_temp = 'baixa'

                catalogo[chave_catalogo] = {
                    'key': chave_catalogo,
                    'label': str(nome_param).replace('_', ' '),
                    'attrs': [nome_norm],
                    'spec_keys': [nome_param, nome_norm],
                    'faixa_temp': faixa_temp,
                }
            else:
                if nome_param not in catalogo[chave_catalogo]['spec_keys']:
                    catalogo[chave_catalogo]['spec_keys'].append(nome_param)

    indice_ordem = {chave: idx for idx, chave in enumerate(ORDEM_PARAMETROS_TENDENCIA)}
    return sorted(
        catalogo.values(),
        key=lambda item: (indice_ordem.get(item['key'], 999), str(item.get('label', '')).lower())
    )


def _normalizar_metadados_parametro(parametro_raw):
    catalogo = {p['key']: p for p in _listar_parametros_tendencia()}

    chave = _normalizar_chave_config(parametro_raw)
    chave = ALIASES_PARAMETRO_TENDENCIA.get(chave, chave)

    if chave in catalogo:
        item = catalogo[chave]
        return {
            'key': item['key'],
            'label': item['label'],
            'attrs': list(item.get('attrs', [])),
            'spec_keys': list(item.get('spec_keys', [])),
            'faixa_temp': item.get('faixa_temp'),
        }

    label_fallback = str(parametro_raw or '').strip() or str(chave or 'parametro')
    return {
        'key': chave or 'parametro',
        'label': label_fallback,
        'attrs': [chave] if chave else [],
        'spec_keys': [label_fallback] + ([chave] if chave else []),
        'faixa_temp': None,
    }


def _valor_medido_por_chave(ensaio, chave, extras=None):
    candidatos = []
    for base in [chave] + (extras or []):
        for variante in _variantes_chave_normalizada(base):
            if variante not in candidatos:
                candidatos.append(variante)

    for cand in candidatos:
        if hasattr(ensaio, cand):
            valor = _to_float_or_none(getattr(ensaio, cand, None))
            if valor is not None:
                return valor

    valores = getattr(ensaio, 'valores_medidos', None)
    if isinstance(valores, dict):
        normalizados = {}
        for k, v in valores.items():
            k_norm = _normalizar_chave_config(k)
            if k_norm:
                normalizados[k_norm] = v

        for cand in candidatos:
            if cand in normalizados:
                valor = _to_float_or_none(normalizados.get(cand))
                if valor is not None:
                    return valor

    return None


def _valor_medido_por_parametro(ensaio, meta_param):
    chave = meta_param.get('key')
    extras = list(meta_param.get('attrs', [])) + list(meta_param.get('spec_keys', []))
    return _valor_medido_por_chave(ensaio, chave, extras=extras)


def _buscar_spec_no_perfil(perfil, meta_param):
    if not isinstance(perfil, dict):
        return None, None

    chaves_candidatas = set()
    for origem in [meta_param.get('key')] + list(meta_param.get('attrs', [])) + list(meta_param.get('spec_keys', [])):
        for variante in _variantes_chave_normalizada(origem):
            chaves_candidatas.add(variante)

    for chave_cfg, spec in perfil.items():
        chave_norm = _normalizar_chave_config(chave_cfg)
        if chave_norm in ('temp_padrao', 'tempo_total'):
            continue
        if chave_norm in chaves_candidatas:
            return spec, chave_cfg
    return None, None


def _limites_parametro_na_config(cod_sankhya, meta_param):
    try:
        cfg_massa = (carregar_configuracoes() or {}).get(str(cod_sankhya), {}) or {}
    except Exception:
        cfg_massa = {}

    if not isinstance(cfg_massa, dict):
        return None, None, None, None, None

    faixa_temp = meta_param.get('faixa_temp')
    prefixos_permitidos = {
        'alta': {'alta_cinza_', 'alta_preto_', 'alta_'},
        'baixa': {'baixa_'},
    }.get(faixa_temp)

    chaves_candidatas = set()
    for origem in [meta_param.get('key')] + list(meta_param.get('attrs', [])) + list(meta_param.get('spec_keys', [])):
        for variante in _variantes_chave_normalizada(origem):
            chaves_candidatas.add(variante)

    for chave_cfg, spec in cfg_massa.items():
        chave_txt = str(chave_cfg or '')
        nome_param = None
        perfil_nome = None
        for prefixo in ('alta_cinza_', 'alta_preto_', 'alta_', 'baixa_'):
            if chave_txt.startswith(prefixo):
                if prefixos_permitidos and prefixo not in prefixos_permitidos:
                    nome_param = None
                    break
                nome_param = chave_txt[len(prefixo):]
                perfil_nome = prefixo.rstrip('_')
                break
        if not nome_param:
            continue

        nome_norm = _normalizar_chave_config(nome_param)
        if nome_norm in chaves_candidatas:
            lie, lse = _obter_limites_spec(spec)
            alvo = _obter_alvo_spec(spec)
            return lie, lse, alvo, perfil_nome, nome_param

    return None, None, None, None, None


def _limites_parametro_para_ensaio(ensaio, meta_param):
    perfis = getattr(getattr(ensaio, 'massa', None), 'perfis', {}) or {}
    for perfil_nome in _perfis_preferenciais_ensaio(ensaio, faixa_temp=meta_param.get('faixa_temp')):
        spec, nome_spec = _buscar_spec_no_perfil(perfis.get(perfil_nome), meta_param)
        if spec is None:
            continue
        lie, lse = _obter_limites_spec(spec)
        alvo = _obter_alvo_spec(spec)
        return lie, lse, alvo, perfil_nome, nome_spec

    return _limites_parametro_na_config(getattr(ensaio, 'cod_sankhya', None), meta_param)


def _nome_grupo_boxplot(data_hora, agrupamento='semana'):
    if not data_hora:
        return None

    if agrupamento == 'mes':
        return data_hora.strftime('%Y-%m')

    ano, semana, _ = data_hora.isocalendar()
    return f"{ano}-S{semana:02d}"


def _ensaio_aprovado(ensaio):
    acao = str(getattr(ensaio, 'acao_recomendada', '') or '').upper()
    if 'REPROV' in acao:
        return False

    score = _to_float_or_none(getattr(ensaio, 'score_final', None))
    if score is not None:
        return score >= 70

    return bool(acao)


def _motivos_reprovacao_ensaio(ensaio):
    if _ensaio_aprovado(ensaio):
        return []

    perfis = getattr(getattr(ensaio, 'massa', None), 'perfis', {}) or {}
    motivos = []
    vistos = set()

    for perfil_nome in _perfis_preferenciais_ensaio(ensaio):
        perfil = perfis.get(perfil_nome) or {}
        if not isinstance(perfil, dict):
            continue

        for chave_cfg, spec in perfil.items():
            chave_norm = _normalizar_chave_config(chave_cfg)
            if chave_norm in ('temp_padrao', 'tempo_total') or chave_norm in vistos:
                continue
            vistos.add(chave_norm)

            valor = _valor_medido_por_chave(ensaio, chave_norm, extras=[chave_cfg])
            if valor is None:
                continue

            lie, lse = _obter_limites_spec(spec)
            if lie is not None and valor < lie:
                motivos.append(f"{chave_cfg} abaixo LIE")
            if lse is not None and valor > lse:
                motivos.append(f"{chave_cfg} acima LSE")

    if motivos:
        return sorted(set(motivos))

    score = _to_float_or_none(getattr(ensaio, 'score_final', None))
    if score is not None and score < 70:
        return ['Score abaixo de 70']

    acao = str(getattr(ensaio, 'acao_recomendada', '') or '').strip()
    if acao:
        return [acao]

    return ['Reprovacao sem motivo detalhado']


def _listar_massas_tendencia():
    rows = (
        db.session.query(
            EnsaioConsolidado.cod_sankhya,
            EnsaioConsolidado.massa_descricao
        )
        .filter(EnsaioConsolidado.cod_sankhya.isnot(None))
        .order_by(EnsaioConsolidado.massa_descricao.asc(), EnsaioConsolidado.cod_sankhya.asc())
        .distinct()
        .all()
    )

    saida = []
    vistos = set()
    for cod, desc in rows:
        try:
            cod_int = int(cod)
        except Exception:
            continue

        if cod_int in vistos:
            continue

        vistos.add(cod_int)
        descricao = str(desc or '').strip() or f"Massa {cod_int}"
        saida.append({
            'cod_sankhya': cod_int,
            'descricao': descricao,
        })

    saida.sort(key=lambda item: (item['descricao'].lower(), item['cod_sankhya']))
    return saida


def _encontrar_spec_fisica(perfil_alta, perfil_baixa, candidatos):
    chaves_candidatas = {_normalizar_chave_config(c) for c in candidatos}

    for perfil in (perfil_alta or {}, perfil_baixa or {}):
        if not isinstance(perfil, dict):
            continue
        for chave, valor in perfil.items():
            if _normalizar_chave_config(chave) in chaves_candidatas:
                return valor
    return None


def _calcular_resumo_propriedades_fisicas(ensaios):
    if not ensaios:
        return []

    ensaio_ref = ensaios[0]
    perfil_alta = (
        ensaio_ref.massa.perfis.get('alta_cinza')
        or ensaio_ref.massa.perfis.get('alta_preto')
        or ensaio_ref.massa.perfis.get('alta')
        or {}
    )
    perfil_baixa = ensaio_ref.massa.perfis.get('baixa') or {}

    saida = []
    for nome_attr, meta in MAPA_PROPRIEDADES_FISICAS.items():
        valores = []
        for ens in ensaios:
            vf = _to_float_or_none(getattr(ens, nome_attr, None))
            if vf is not None and vf > 0:
                valores.append(vf)

        media_v = statistics.mean(valores) if valores else None
        min_v = min(valores) if valores else None
        max_v = max(valores) if valores else None

        spec = _encontrar_spec_fisica(perfil_alta, perfil_baixa, meta['spec_keys'])
        spec_min, spec_max = _obter_limites_spec(spec)
        tem_spec = (spec_min is not None) or (spec_max is not None)

        fora = False
        if tem_spec and media_v is not None:
            if spec_min is not None and media_v < spec_min:
                fora = True
            if spec_max is not None and media_v > spec_max:
                fora = True

        if media_v is None:
            status = 'sem_dado'
        elif not tem_spec:
            status = 'sem_spec'
        elif fora:
            status = 'fora'
        else:
            status = 'ok'

        saida.append({
            'key': nome_attr,
            'label': meta['label'],
            'sigla': meta['sigla'],
            'unidade': meta['unidade'],
            'qtd': len(valores),
            'media': media_v,
            'min': min_v,
            'max': max_v,
            'spec_min': spec_min,
            'spec_max': spec_max,
            'fora_faixa': fora,
            'tem_spec': tem_spec,
            'status': status,
        })

    return saida


def _resumo_status_propriedades_fisicas(props):
    resumo = {'ok': 0, 'fora': 0, 'sem_spec': 0, 'sem_dado': 0, 'avaliadas': 0}
    for p in props:
        status = p.get('status')
        if status in resumo:
            resumo[status] += 1
        if status in ('ok', 'fora'):
            resumo['avaliadas'] += 1
    return resumo


def _obter_regra_abrasao_5n(cod_sankhya):
    """
    Busca no config_massas.json os limites de dureza para metodologia de abrasao 5 N.
    Fallback padrao: min 40 / max 50 Shore A.
    """
    regra = {'dureza_min': 40.0, 'dureza_max': 50.0}
    try:
        cfg = carregar_configuracoes().get(str(cod_sankhya), {}) or {}
        if not isinstance(cfg, dict):
            return regra

        bloco = cfg.get('regra_abrasao_5n') if isinstance(cfg.get('regra_abrasao_5n'), dict) else {}

        min_candidates = [
            bloco.get('dureza_min'),
            bloco.get('min'),
            cfg.get('abrasao_5n_dureza_min'),
            cfg.get('abrasao_metodologia_5n_dureza_min'),
        ]
        max_candidates = [
            bloco.get('dureza_max'),
            bloco.get('max'),
            cfg.get('abrasao_5n_dureza_max'),
            cfg.get('abrasao_metodologia_5n_dureza_max'),
        ]

        for v in min_candidates:
            fv = _to_float_or_none(v)
            if fv is not None:
                regra['dureza_min'] = fv
                break

        for v in max_candidates:
            fv = _to_float_or_none(v)
            if fv is not None:
                regra['dureza_max'] = fv
                break
    except Exception:
        pass

    return regra


def _usa_metodologia_abrasao_5n(props, regra_abrasao_5n=None):
    """
    abrasao usa metodologia 5 N quando a especificacao de dureza do composto
    estiver dentro da faixa configurada.
    """
    prop_dureza = next((p for p in props if p.get('key') == 'dureza'), None)
    if not prop_dureza:
        return False

    spec_min = _to_float_or_none(prop_dureza.get('spec_min'))
    spec_max = _to_float_or_none(prop_dureza.get('spec_max'))
    if spec_min is None or spec_max is None:
        return False

    regra = regra_abrasao_5n or {'dureza_min': 40.0, 'dureza_max': 50.0}
    ref_min = _to_float_or_none(regra.get('dureza_min'))
    ref_max = _to_float_or_none(regra.get('dureza_max'))
    if ref_min is None or ref_max is None:
        ref_min, ref_max = 40.0, 50.0

    return spec_min >= ref_min and spec_max <= ref_max


@app.route('/relatorios')
@login_required
def pagina_relatorios():
    dados_cache = cache_service.get()
    ensaios_cache = dados_cache['dados'] if dados_cache else []
    ultimo_update = dados_cache.get('ultimo_update') if dados_cache else None
    
    # Captura filtros
    busca = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'nome')
    order = request.args.get('order', 'asc')

    relatorio_estruturado = gerar_estrutura_relatorio(
        ensaios_cache, busca=busca, ordenar_por=sort_by, ordem=order
    )
    
    context = {
        'relatorio': relatorio_estruturado,
        'ultimo_update': ultimo_update,
        'total_massas': len(relatorio_estruturado),
        'search_term': busca, 'sort_by': sort_by, 'order': order
    }

    if request.headers.get('HX-Request'):
        return render_template('tabela_relatorios.html', **context)
    
    return render_template('relatorios.html', **context)

# Rota da Lista de Lotes (Agora com Filtros e OrdenaÃƒÂ§ÃƒÂ£o)
@app.route('/relatorios/detalhes/<int:cod_sankhya>')
@login_required
def detalhes_lotes_massa(cod_sankhya):
    dados_cache = cache_service.get()
    if not dados_cache: return redirect(url_for('dashboard_home'))
    
    # Filtra os dados brutos
    lista_filtrada = [e for e in dados_cache['dados'] if e.massa.cod_sankhya == cod_sankhya]
    
    # Gera a ÃƒÂ¡rvore (com os novos KPIs de lote)
    relatorio = gerar_estrutura_relatorio(lista_filtrada)
    if not relatorio: return "Material nÃƒÂ£o encontrado ou sem dados."
    
    massa_node = relatorio[0]
    
    # --- LÃƒÂ³gica de OrdenaÃƒÂ§ÃƒÂ£o e Filtro da Lista de Lotes ---
    lotes_lista = list(massa_node['lotes'].values())
    regra_abrasao_5n = _obter_regra_abrasao_5n(cod_sankhya)
    
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    search_lote = request.args.get('search', '').upper()
    
    # Filtro de Busca
    if search_lote:
        lotes_lista = [l for l in lotes_lista if search_lote in str(l['numero']).upper()]

    for lote in lotes_lista:
        props = _calcular_resumo_propriedades_fisicas(lote.get('batches', []))
        lote['propriedades_fisicas'] = props
        lote['resumo_props_fisicas'] = _resumo_status_propriedades_fisicas(props)
        lote['metodologia_abrasao_5n'] = _usa_metodologia_abrasao_5n(props, regra_abrasao_5n)
        lote['regra_abrasao_5n'] = regra_abrasao_5n
    
    # OrdenaÃƒÂ§ÃƒÂ£o
    reverse = (order == 'desc')
    if sort_by == 'data':
        lotes_lista.sort(key=lambda x: x.get('data_recente') or datetime.min, reverse=reverse)
    elif sort_by == 'lote':
        lotes_lista.sort(key=lambda x: x['numero'], reverse=reverse)
    elif sort_by == 'score':
        lotes_lista.sort(key=lambda x: x['kpi_lote']['score_medio'], reverse=reverse)
    elif sort_by == 'qtd':
        lotes_lista.sort(key=lambda x: x['kpi_lote']['total'], reverse=reverse)

    context = {
        'massa': massa_node,
        'lotes': lotes_lista,
        'sort_by': sort_by,
        'order': order,
        'search_term': search_lote
    }

    # Se for HTMX, retorna sÃƒÂ³ o tbody
    if request.headers.get('HX-Request'):
        return render_template('partial_lista_lotes.html', **context)

    return render_template('lista_lotes.html', **context)


@app.route('/relatorios/lote/<int:cod_sankhya>/<path:numero_lote>')
@login_required
def detalhe_lote_view(cod_sankhya, numero_lote):
    dados_cache = cache_service.get()
    if not dados_cache: return "Cache vazio."
    
    # 1. Recupera Ensaios do Lote
    ensaios_do_lote = [
        e for e in dados_cache['dados'] 
        if e.massa.cod_sankhya == cod_sankhya and str(e.lote) == str(numero_lote)
    ]
    
    if not ensaios_do_lote: return "Lote nÃƒÂ£o encontrado."

    # 2. LÃƒÂ³gica de OrdenaÃƒÂ§ÃƒÂ£o da Tabela
    sort_by = request.args.get('sort', 'batch') # PadrÃƒÂ£o: Batch
    order = request.args.get('order', 'asc')    # PadrÃƒÂ£o: Crescente
    reverse = (order == 'desc')

    def safe_sort_key(obj, attr, default=0):
        val = getattr(obj, attr, default)
        return val if val is not None else default

    if sort_by == 'id':
        ensaios_do_lote.sort(key=lambda x: x.id_ensaio, reverse=reverse)
    elif sort_by == 'hora':
        ensaios_do_lote.sort(key=lambda x: x.data_hora, reverse=reverse)
    elif sort_by == 'batch':
        # Tenta converter para int para ordenar corretamente (1, 2, 10 e nÃƒÂ£o 1, 10, 2)
        def batch_key(x):
            try: return int(x.batch)
            except: return 0
        ensaios_do_lote.sort(key=batch_key, reverse=reverse)
    elif sort_by == 'temp':
        ensaios_do_lote.sort(key=lambda x: x.temp_plato, reverse=reverse)
    elif sort_by == 'score':
        ensaios_do_lote.sort(key=lambda x: x.score_final, reverse=reverse)

    # 3. Gera ÃƒÂrvore para KPIs (Score Geral, AprovaÃƒÂ§ÃƒÂ£o)
    arvore = gerar_estrutura_relatorio(ensaios_do_lote)
    dados_lote = arvore[0]['lotes'][numero_lote]
    dados_massa = arvore[0]

    # 4. CÃƒÂ¡lculo Robusto de MÃƒÂ©dias (Corrigindo Viscosidade Zerada)
    coleta = {
        'alta': {'Ts2': [], 'T90': []},
        'baixa': {'Ts2': [], 'T90': []},
        'visc': []
    }

    for e in ensaios_do_lote:
        temp = e.temp_plato or 0
        contexto = 'alta' if temp >= 175 else 'baixa'
        vals = e.valores_medidos
        
        if vals.get('Ts2') and vals['Ts2'] > 0: coleta[contexto]['Ts2'].append(vals['Ts2'])
        if vals.get('T90') and vals['T90'] > 0: coleta[contexto]['T90'].append(vals['T90'])
        
        # CorreÃƒÂ§ÃƒÂ£o aqui: SÃƒÂ³ adiciona se for maior que 0
        if vals.get('Viscosidade') and vals['Viscosidade'] > 0.1: 
            coleta['visc'].append(vals['Viscosidade'])

    def media_segura(lista):
        return statistics.mean(lista) if lista else 0

    dados_lote['medias_detalhadas'] = {
        'alta_ts2': media_segura(coleta['alta']['Ts2']),
        'alta_t90': media_segura(coleta['alta']['T90']),
        'baixa_ts2': media_segura(coleta['baixa']['Ts2']),
        'baixa_t90': media_segura(coleta['baixa']['T90']),
        'visc': media_segura(coleta['visc']),
        'tem_alta': bool(coleta['alta']['Ts2'] or coleta['alta']['T90']),
        'tem_baixa': bool(coleta['baixa']['Ts2'] or coleta['baixa']['T90'])
    }

    # 5. Resumo e avaliacao das propriedades fisicas do lote
    regra_abrasao_5n = _obter_regra_abrasao_5n(cod_sankhya)
    dados_lote['propriedades_fisicas'] = _calcular_resumo_propriedades_fisicas(ensaios_do_lote)
    dados_lote['metodologia_abrasao_5n'] = _usa_metodologia_abrasao_5n(
        dados_lote['propriedades_fisicas'], regra_abrasao_5n
    )
    dados_lote['regra_abrasao_5n'] = regra_abrasao_5n

    return render_template(
        'detalhe_lote.html', 
        lote=dados_lote, 
        massa=dados_massa,
        ensaios=ensaios_do_lote,
        # Passamos os params para manter a ordenaÃƒÂ§ÃƒÂ£o nos links
        sort_by=sort_by,
        order=order
    )


# --- ROTAS DE FORMULAÃƒâ€¡ÃƒÆ’O ---
def _normalizar_chave_mp(chave):
    raw = str(chave or '').strip().lower()
    if not raw:
        return None

    if raw.startswith('mp_'):
        raw = raw[3:]

    if not raw.isdigit():
        return None

    return f"mp_{int(raw)}"


def _ingredientes_da_formula(formula):
    ingredientes = {}
    for item in formula.itens:
        chave = _normalizar_chave_mp(item.cd_materia_prima)
        phr = _to_float_or_none(item.qt_phr)
        if not chave or phr is None:
            continue
        ingredientes[chave] = float(phr)
    return ingredientes


def _parse_ingredientes_payload(payload):
    saida = {}
    if not isinstance(payload, dict):
        return saida

    for chave, valor in payload.items():
        chave_norm = _normalizar_chave_mp(chave)
        phr = _to_float_or_none(valor)
        if not chave_norm or phr is None:
            continue
        saida[chave_norm] = float(phr)

    return saida


def _listar_materias_primas_catalogo():
    catalogo = get_catalogo_codigo() or {}
    produtos = list(catalogo.values())

    # Se houver classificacao por tipo no catalogo, filtra apenas materia-prima.
    tem_tipo_catalogo = any(str(getattr(p, 'tipo', '') or '').strip() for p in produtos)

    materias_primas = []
    for produto in produtos:
        tipo = str(getattr(produto, 'tipo', '') or '').strip().upper()
        if tem_tipo_catalogo and tipo != 'MATERIA_PRIMA':
            continue

        codigo = getattr(produto, 'cod_sankhya', None)
        descricao = str(getattr(produto, 'descricao', '') or '').strip()
        if codigo is None or not descricao:
            continue

        try:
            codigo_norm = int(codigo)
        except Exception:
            codigo_norm = str(codigo).strip()
            if not codigo_norm:
                continue

        materias_primas.append({
            'codigo': codigo_norm,
            'nome': descricao
        })

    materias_primas.sort(key=lambda x: str(x['nome']).upper())
    return materias_primas



@app.route('/simulador', methods=['GET', 'POST'])
@login_required
def simulador():
    if request.method == 'GET':
        return render_template(
            'simulador.html',
            materias_primas=_listar_materias_primas_catalogo(),
            simulador_ia_disponivel=(MultiTargetSimulator is not None)
        )

    payload = request.get_json(silent=True) or {}
    ingredientes = _parse_ingredientes_payload(payload.get('ingredientes', {}))
    
    if not ingredientes:
        return jsonify({'success': False, 'erro': 'Ingredientes invalidos.'}), 400

    try:
        # 1. Simulate
        raw_results = MultiTargetSimulator.predict(ingredientes)
        
        # 2. Apply Knowledge Rules
        final_results = KnowledgeService.apply_rules(raw_results, ingredientes)
        
        return jsonify({
            'success': True,
            'results': final_results
        })
        
    except Exception as e:
        print(f"Erro na simulacao: {e}")
        return jsonify({'success': False, 'erro': str(e)}), 500

@app.route('/simulador/search', methods=['POST'])
@login_required
def simulador_search():
    payload = request.get_json(silent=True) or {}
    ingredientes = _parse_ingredientes_payload(payload.get('ingredientes', {}))
    
    if not ingredientes:
        return jsonify({'success': False, 'erro': 'Ingredientes invalidos.'}), 400
        
    try:
        results = SearchService.find_similar(ingredientes)
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'erro': str(e)}), 500

@app.route('/knowledge', methods=['GET', 'POST'])
@login_required
def knowledge_base():
    if request.method == 'GET':
        rules = KnowledgeService.get_rules()
        return jsonify([{
            'id': r.id, 
            'property': r.target_property,
            'effect': f"{r.effect_type} {r.effect_value}",
            'desc': r.description
        } for r in rules])
        
    # POST - Add Rule
    data = request.get_json()
    try:
        KnowledgeService.add_rule(
            target_property=data['target_property'],
            effect_value=data['effect_value'],
            effect_type=data.get('effect_type', 'linear'),
            ingredient_code=data.get('ingredient_code'),
            min_phr=data.get('min_phr'),
            max_phr=data.get('max_phr'),
            description=data.get('description')
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'erro': str(e)}), 500
@app.route('/detalhe_formula/<int:cd_produto>')
@login_required
def detalhe_formula(cd_produto):
    # Retrieve the formula by its code, return 404 if not found
    formula = Formula.query.get_or_404(cd_produto)
    return render_template(
        'detalhe_formula.html',
        formula=formula,
        catalogo=get_catalogo_codigo() or {},
    )

@app.route('/api/xai/treinar', methods=['POST'])
@login_required
def api_xai_treinar():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if SimuladorIAService is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de IA indisponivel. Verifique as dependencias xgboost/shap.'
        }), 503

    try:
        resultado = SimuladorIAService.treinar_modelo()
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': f'Falha no treinamento: {e}'}), 500


@app.route('/api/xai/simular/<int:cd_produto>', methods=['POST'])
@login_required
def api_xai_simular_formula(cd_produto):
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if SimuladorIAService is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de IA indisponivel. Verifique as dependencias xgboost/shap.'
        }), 503

    formula = Formula.query.get(cd_produto)
    if not formula:
        return jsonify({'status': 'erro', 'mensagem': 'Formula nao encontrada.'}), 404

    payload = request.get_json(silent=True) or {}
    usar_formula_base_raw = payload.get('usar_formula_base', True)
    if isinstance(usar_formula_base_raw, str):
        usar_formula_base = usar_formula_base_raw.strip().lower() not in ('0', 'false', 'no', 'off')
    else:
        usar_formula_base = bool(usar_formula_base_raw)

    ingredientes = _ingredientes_da_formula(formula) if usar_formula_base else {}
    ajustes = _parse_ingredientes_payload(payload.get('ingredientes', {}))
    ingredientes.update(ajustes)

    if not ingredientes:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Nenhum ingrediente valido foi informado para simulacao.'
        }), 400

    try:
        if SimuladorIAService is not None:
            resultado = SimuladorIAService.simular_nova_receita(ingredientes)
        else:
            prior = hardness_prior_details(ingredientes) if hardness_prior_details else {}
            hardness_rule = _to_float_or_none(
                (prior or {}).get('hardness_rule_final') or (prior or {}).get('hardness_rule')
            )
            resultado = {
                'hardness_rule': hardness_rule,
                'hardness_rule_final': hardness_rule,
                'hardness_model': None,
                'hardness_final': hardness_rule,
                'dureza_prevista': hardness_rule,
                'unidade': 'Shore A',
                'impacto_ingredientes': {},
                'base_value': None,
                'base_blend_shore': (prior or {}).get('base_blend_shore'),
                'elastomer_blend_breakdown': (prior or {}).get('elastomer_blend_breakdown') or [],
                'prior_diagnostics': prior,
                'guardrail': 'rule_engine_only'
            }
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': f'Falha na simulacao: {e}'}), 500

    if isinstance(resultado, dict) and resultado.get('erro'):
        return jsonify({'status': 'erro', 'mensagem': resultado.get('erro')}), 400

    impactos = (resultado or {}).get('impacto_ingredientes') or {}
    chaves_receita = set(ingredientes.keys())
    impactos_filtrados = {}

    # Mantem no retorno apenas MPs efetivamente usadas na simulacao.
    for chave, valor in impactos.items():
        if chave not in chaves_receita:
            continue
        val_float = _to_float_or_none(valor)
        if val_float is None:
            continue
        impactos_filtrados[chave] = val_float

    # Se o servico truncar impactos pequenos, completa com zero para as MPs da receita
    # apenas quando houver retorno SHAP do modelo.
    if impactos:
        for chave in chaves_receita:
            if chave not in impactos_filtrados:
                impactos_filtrados[chave] = 0.0

    impactos_ordenados = sorted(impactos_filtrados.items(), key=lambda x: abs(x[1]), reverse=True)

    hardness_rule = _to_float_or_none((resultado or {}).get('hardness_rule'))
    hardness_rule_final = _to_float_or_none(
        (resultado or {}).get('hardness_rule_final') or (resultado or {}).get('hardness_rule')
    )
    hardness_model = _to_float_or_none((resultado or {}).get('hardness_model'))
    hardness_final = _to_float_or_none((resultado or {}).get('hardness_final'))
    if hardness_final is None:
        hardness_final = hardness_rule_final
    if hardness_final is None:
        hardness_final = _to_float_or_none((resultado or {}).get('dureza_prevista'))

    return jsonify({
        'status': 'sucesso',
        'cd_produto': cd_produto,
        'ingredientes_utilizados': ingredientes,
        'hardness_rule': hardness_rule,
        'hardness_rule_final': hardness_rule_final,
        'hardness_model': hardness_model,
        'hardness_final': hardness_final,
        'base_blend_shore': _to_float_or_none((resultado or {}).get('base_blend_shore')),
        'elastomer_blend_breakdown': (resultado or {}).get('elastomer_blend_breakdown') or [],
        'guardrail': (resultado or {}).get('guardrail'),
        'prior_diagnostics': (resultado or {}).get('prior_diagnostics') or {},
        'propriedades_estimadas': {
            'dureza': {
                'valor': hardness_final,
                'unidade': (resultado or {}).get('unidade', 'Shore A')
            }
        },
        'xai': {
            'base_value': (resultado or {}).get('base_value'),
            'impacto_ingredientes': dict(impactos_ordenados)
        }
    }), 200


def _to_bool(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in ("1", "true", "yes", "on", "sim"):
        return True
    if raw in ("0", "false", "no", "off", "nao", "não"):
        return False
    return bool(default)


@app.route('/api/formulation/train', methods=['POST'])
@login_required
def api_formulation_train():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if formulation_engine_service is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de formulacao indisponivel.'
        }), 503

    payload = request.get_json(silent=True) or {}
    min_samples = payload.get('min_samples', 20)
    include_legacy = _to_bool(payload.get('include_legacy', True), True)

    try:
        resultado = formulation_engine_service.train(
            min_samples=int(min_samples),
            auto_include_legacy=include_legacy,
        )
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as exc:
        return jsonify({'status': 'erro', 'mensagem': f'Falha no treinamento: {exc}'}), 500


@app.route('/predict-formulation', methods=['POST'])
@login_required
def predict_formulation():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if formulation_engine_service is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de formulacao indisponivel.'
        }), 503

    payload = request.get_json(silent=True) or {}
    formulation = payload.get('formulation') or {'ingredients': payload.get('ingredients') or {}}
    process = payload.get('process_parameters') or payload.get('process') or {}
    auto_train = _to_bool(payload.get('auto_train_if_missing', True), True)

    try:
        resultado = formulation_engine_service.predict(
            formulation_payload=formulation,
            process_payload=process,
            auto_train_if_missing=auto_train,
        )
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as exc:
        return jsonify({'status': 'erro', 'mensagem': f'Falha na previsao: {exc}'}), 500


@app.route('/optimize-formulation', methods=['POST'])
@login_required
def optimize_formulation():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if formulation_engine_service is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de formulacao indisponivel.'
        }), 503

    payload = request.get_json(silent=True) or {}
    auto_train = _to_bool(payload.get('auto_train_if_missing', True), True)

    try:
        resultado = formulation_engine_service.optimize(
            payload=payload,
            auto_train_if_missing=auto_train,
        )
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as exc:
        return jsonify({'status': 'erro', 'mensagem': f'Falha na otimizacao: {exc}'}), 500


@app.route('/explain-formulation', methods=['POST'])
@login_required
def explain_formulation():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if formulation_engine_service is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de formulacao indisponivel.'
        }), 503

    payload = request.get_json(silent=True) or {}
    formulation = payload.get('formulation') or {'ingredients': payload.get('ingredients') or {}}
    process = payload.get('process_parameters') or payload.get('process') or {}
    top_n = payload.get('top_n', 12)
    auto_train = _to_bool(payload.get('auto_train_if_missing', True), True)

    try:
        resultado = formulation_engine_service.explain(
            formulation_payload=formulation,
            process_payload=process,
            top_n=int(top_n),
            auto_train_if_missing=auto_train,
        )
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as exc:
        return jsonify({'status': 'erro', 'mensagem': f'Falha na explicacao: {exc}'}), 500


@app.route('/suggest-next-experiments', methods=['GET'])
@login_required
def suggest_next_experiments():
    if current_user.role != 'admin':
        return jsonify({'status': 'erro', 'mensagem': 'Acesso negado.'}), 403

    if formulation_engine_service is None:
        return jsonify({
            'status': 'erro',
            'mensagem': 'Servico de formulacao indisponivel.'
        }), 503

    top_n = request.args.get('top_n', default=10, type=int)
    candidate_pool_size = request.args.get('candidate_pool_size', default=260, type=int)
    auto_train = _to_bool(request.args.get('auto_train_if_missing', 'true'), True)

    try:
        resultado = formulation_engine_service.suggest_next_experiments(
            top_n=top_n,
            candidate_pool_size=candidate_pool_size,
            auto_train_if_missing=auto_train,
        )
        status_http = 200 if resultado.get('status') == 'sucesso' else 400
        return jsonify(resultado), status_http
    except Exception as exc:
        return jsonify({'status': 'erro', 'mensagem': f'Falha no active learning: {exc}'}), 500


@app.route('/contratos-api')
@login_required
def pagina_contratos_api():
    docs_xai_path = os.path.join(app.root_path, 'docs', 'motor_xai.md')
    return render_template(
        'contratos_api.html',
        docs_xai_disponivel=os.path.exists(docs_xai_path)
    )


@app.route('/formulas')
@login_required
def lista_formulas():
    formulas = Formula.query.order_by(Formula.cd_produto).all()
    # Passamos o catÃƒÂ¡logo tambÃƒÂ©m, caso precise corrigir nomes na listagem
    return render_template('lista_formulas.html', formulas=formulas, catalogo=get_catalogo_codigo())

if __name__ == '__main__':
    app.run(debug=True)

