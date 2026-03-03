from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from models.usuario import db, Usuario
from models.consolidado import EnsaioConsolidado # Novo Modelo
from cache_manager import CacheManager

from datetime import datetime
import math
import os
import statistics 
<<<<<<< ours
import json
import re
import unicodedata
from urllib.parse import urlparse, urljoin
from sqlalchemy import or_, func, case, desc, and_, text # Adicionado para conexÃƒÂ£o local
from sqlalchemy.orm import load_only

=======
import sqlite3 # Adicionado para conexão local
import json
>>>>>>> theirs

# ConfiguraÃƒÂ§ÃƒÂµes e Modelos
from config import Config
from services.config_manager import (
    carregar_configuracoes,
    carregar_regras_acao,
    salvar_regras_acao,
    salvar_configuracao,
)
from services.learning_service import ensinar_lote, carregar_aprendizado_mapa
from services.report_service import gerar_estrutura_relatorio
<<<<<<< ours
from models.score_versioning import ScoreResultado
from models.formula import Formula, FormulaItem
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
=======
from services.kinetics_service import list_ensaios_for_fit, run_fit, load_fit_payload, get_preview_curve
from services.vulcanization_service import run_simulation, load_simulation
>>>>>>> theirs

try:
    from services.formulation_engine_service import FormulationEngineService
except Exception as e:
    print(f"Aviso: Motor de formulacao indisponivel: {e}")
    FormulationEngineService = None

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

# Caminho local padrÃƒÂ£o para o cache baixado do SharePoint
CACHE_PLANILHA_SHAREPOINT = "cache_reg403_sharepoint.xlsx"

def _is_safe_redirect_url(target: str) -> bool:
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc

def recarregar_cache_memoria():
    """
    FunÃƒÂ§ÃƒÂ£o auxiliar: Busca todos os dados do SQLite e atualiza o CacheManager.
    Essencial para as telas de RelatÃƒÂ³rios e Auditoria funcionarem.
    """
    try:
        print("[INFO] Recarregando cache em memoria a partir do banco...")
        # Busca todos os dados ordenados
        todos_ensaios = EnsaioConsolidado.query.order_by(EnsaioConsolidado.data_hora.desc()).all()
        overlay_fn = globals().get('aplicar_sobreposicao_local')
        if callable(overlay_fn):
            todos_ensaios = overlay_fn(todos_ensaios)
        
        if not todos_ensaios:
            print("[WARN] Banco de dados vazio. Cache nao atualizado.")
            return 0

        # Extrai lista de materiais para filtros (usado na config)
        materiais_unicos = sorted(list(set(e.massa_descricao for e in todos_ensaios if e.massa_descricao)))
        
        # Monta o objeto que as telas esperam
        dados_para_cache = {
            'dados': todos_ensaios,
            'materiais': materiais_unicos,
            'ultimo_update': datetime.now()
        }
        
        # Salva no Cache Service
        cache_service.set(dados_para_cache)
        print(f"[OK] Cache atualizado com {len(todos_ensaios)} registros.")
        return len(todos_ensaios)
        
    except Exception as e:
        print(f"[ERRO] Erro ao recarregar cache: {e}")
        return 0

def _normalizar_lista_materiais(itens):
    """
    Converte listas heterogeneas de materiais (str/dict/objeto) para
    uma lista unica de descricoes.
    """
    nomes = []
    vistos = set()

    for item in (itens or []):
        nome = None

        if isinstance(item, str):
            nome = item
        elif isinstance(item, dict):
            nome = item.get('descricao') or item.get('massa_descricao') or item.get('nome')
        else:
            nome = (
                getattr(item, 'descricao', None)
                or getattr(item, 'massa_descricao', None)
                or getattr(item, 'nome', None)
            )

        nome = str(nome or '').strip()
        if not nome:
            continue

        chave = nome.upper()
        if chave in vistos:
            continue

        vistos.add(chave)
        nomes.append(nome)

    nomes.sort()
    return nomes

def preparar_planilha_sharepoint(forcar_download=False):
    """
    Garante que a planilha venha do SharePoint e define CAMINHO_REG403
    apontando para o arquivo cacheado localmente.
    """
    if not baixar_excel_sharepoint:
        return None

    caminho_cache = os.path.abspath(CACHE_PLANILHA_SHAREPOINT)

    # Se jÃƒÂ¡ temos um cache e nÃƒÂ£o foi solicitado forÃƒÂ§a de download, reutiliza.
    if not forcar_download and os.path.exists(caminho_cache) and os.path.getsize(caminho_cache) > 0:
        os.environ["CAMINHO_REG403"] = caminho_cache
        return caminho_cache

    try:
        caminho_baixado = baixar_excel_sharepoint(nome_destino=CACHE_PLANILHA_SHAREPOINT)
        if caminho_baixado:
            caminho_abs = os.path.abspath(caminho_baixado)
            os.environ["CAMINHO_REG403"] = caminho_abs
            return caminho_abs
    except Exception as e:
        print(f"[WARN] Falha ao baixar planilha do SharePoint: {e}")

    return None

from connection import connect_to_database

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

# --- CONFIGURAÃƒâ€¡ÃƒÆ’O DO BANCO DE USUÃƒÂRIOS (SQLite Local) ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users_reoscore.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Inicializa o cache cedo para ficar disponivel durante o bootstrap.
cache_service = CacheManager(ttl_minutes=30, max_size_mb=500)
formulation_engine_service = FormulationEngineService() if FormulationEngineService else None

# Cria o banco de dados na primeira execuÃƒÂ§ÃƒÂ£o se nÃƒÂ£o existir
with app.app_context():
    db.create_all()

    # MigraÃƒÂ§ÃƒÂ£o leve: adiciona colunas novas no consolidado sem precisar de Alembic.
    try:
        cols = [r[1] for r in db.session.execute(text("PRAGMA table_info(ensaio_consolidado)")).all()]
        alter_needed = False

        # MantÃƒÂ©m compatibilidade com bancos locais legados.
        colunas_esperadas = {
            "updated_at": "DATETIME",
            "dureza": "REAL",
            "densidade": "REAL",
            "abrasao": "REAL",
            "resiliencia": "REAL",
            "tensao_ruptura": "REAL",
            "alongamento": "REAL",
            "rasgo": "REAL",
            "modulo_100": "REAL",
            "modulo_300": "REAL",
            "origem_lab_file": "TEXT",
            "ids_agrupados": "TEXT",
            "temps_plato": "TEXT",
            "temp_reo": "REAL",
            "temp_visc": "REAL",
            "ids_reo": "TEXT",
            "ids_visc": "TEXT",
            "origem_viscosidade": "TEXT",
            "metodo_identificacao": "TEXT",
            "lote_original": "TEXT",
            "material_original": "TEXT",
        }

        for nome_coluna, tipo_sql in colunas_esperadas.items():
            if nome_coluna not in cols:
                db.session.execute(text(f"ALTER TABLE ensaio_consolidado ADD COLUMN {nome_coluna} {tipo_sql}"))
                alter_needed = True

        if alter_needed:
            db.session.commit()
            print("Migracao aplicada: schema de ensaio_consolidado atualizado.")
    except Exception as e:
        db.session.rollback()
        print(f"Aviso: falha na migracao de ensaio_consolidado: {e}")
    if EnsaioConsolidado.query.first():
        recarregar_cache_memoria()

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))


# ==========================================
# 0. CONFIGURAÃƒâ€¡ÃƒÆ’O DE APRENDIZADO (SQLALCHEMY)
# ==========================================

def aplicar_sobreposicao_local(dados_brutos):
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
# 1. INICIALIZAÃƒâ€¡ÃƒÆ’O E CACHE
# ==========================================
print("\n=== REOSCORE V13 (MODULARIZED & SIDECAR) ===")

# Certifica que o ETL vai usar somente a planilha baixada do SharePoint
caminho_sharepoint_inicial = preparar_planilha_sharepoint(forcar_download=False)
if caminho_sharepoint_inicial:
    print(f"  > Planilha SharePoint configurada em: {caminho_sharepoint_inicial}")
else:
    print("[WARN] Aviso: Planilha do SharePoint nao configurada. Use 'Atualizar Dados' para sincronizar.")

with app.app_context():
    carregar_referencias_estaticas()



# ==========================================
# 2. ROTAS DE AUTENTICAÃƒâ€¡ÃƒÆ’O
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = Usuario.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard_home'))
        else:
            flash('Login ou senha invÃƒÂ¡lidos.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('VocÃƒÂª saiu do sistema.', 'info')
    return redirect(url_for('login'))

@app.route('/criar_admin')
def criar_admin():
    if Usuario.query.filter_by(username='admin').first():
        return "Admin jÃƒÂ¡ existe."
    
    novo_admin = Usuario(username='admin', role='admin')
    novo_admin.set_password('senha123')
    db.session.add(novo_admin)
    db.session.commit()
    return "Admin criado com sucesso! (User: admin / Pass: senha123)"

@app.route('/criar_operador')
def criar_operador():
    if Usuario.query.filter_by(username='operador').first():
        return "UsuÃƒÂ¡rio 'operador' jÃƒÂ¡ existe."
    
    novo_user = Usuario(username='operador', role='operador')
    novo_user.set_password('vulca123')
    db.session.add(novo_user)
    db.session.commit()
    return "UsuÃƒÂ¡rio 'operador' criado com sucesso! (User: operador / Pass: vulca123)"

# ==========================================
# 3. ROTAS PRINCIPAIS (DASHBOARD)
# ==========================================

@app.route('/atualizar_dados')
@login_required
def rota_atualizar():
    try:
        # --- PASSO 1: DOWNLOAD SHAREPOINT ---
        if baixar_excel_sharepoint:
            caminho_baixado = preparar_planilha_sharepoint(forcar_download=True)
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
        total_ajustados = aplicar_correcoes_persistidas_no_consolidado()
        total_lotes_limpos = aplicar_limpeza_lotes_no_consolidado()
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

    return redirect(url_for('pagina_config', _anchor='ensinar'))

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

@app.route('/analise/<int:id_ensaio>')
@login_required
def analise_curva(id_ensaio):
    """
    ROTA ANALÃƒÂTICA: Detalhes profundos de um ensaio especÃƒÂ­fico.
    """
    ensaio = EnsaioConsolidado.query.get_or_404(id_ensaio)
    
    # Busca o detalhe do cÃƒÂ¡lculo (logs da engine)
    resultado = ScoreResultado.query.filter_by(id_ensaio=id_ensaio).order_by(ScoreResultado.id.desc()).first()
    detalhes_score = resultado.detalhes_log if resultado else {}
    
    # Se 'params' estiver aninhado (dependendo da versÃƒÂ£o da engine)
    if 'params' in detalhes_score:
        detalhes_score = detalhes_score['params']

    return render_template('detalhe_curva.html', ensaio=ensaio, detalhes=detalhes_score)

# ==========================================
# 4. ROTAS DE CONFIGURAÃƒâ€¡ÃƒÆ’O (ADMIN)
# ==========================================

@app.route('/config')
@login_required
def pagina_config():
    # OBS: Se o usuÃƒÂ¡rio NÃƒÆ’O for admin, ele ainda vai carregar os dados de materiais
    # abaixo, mas o template nÃƒÂ£o vai mostrar. NÃƒÂ£o ÃƒÂ© crÃƒÂ­tico para performance.
    
    # === PARTE 1: CONFIGURAÃƒâ€¡ÃƒÆ’O DE MATERIAIS ===
    regras_acao = carregar_regras_acao()
    configs_massas = carregar_configuracoes()

    query = request.args.get('q', '').strip().upper()
    filtro_tipo = request.args.get('tipo', '')
    filtro_status = request.args.get('status', '')
    
    # PaginaÃƒÂ§ÃƒÂ£o de Materiais
    page_mat = request.args.get('page_mat', 1, type=int) 
    
    sort_by = request.args.get('sort', 'descricao') 
    order = request.args.get('order', 'asc')
    LIMIT = 20

    CATALOGO_ATUAL = get_catalogo_codigo()
    produtos_filtrados = []

    for p in CATALOGO_ATUAL.values():
        if query and (query not in str(p.cod_sankhya) and query not in p.descricao.upper()): continue
        if filtro_tipo and p.tipo != filtro_tipo: continue
        if filtro_status:
            tem_conteudo = (
                (p.perfis and (p.perfis.get('alta') or p.perfis.get('baixa'))) or 
                (p.parametros and len(p.parametros) > 0)
            )
            if filtro_status == 'OK' and not tem_conteudo: continue
            if filtro_status == 'PENDENTE' and tem_conteudo: continue
        produtos_filtrados.append(p)
    
    reverse = (order == 'desc')
    if sort_by == 'cod': produtos_filtrados.sort(key=lambda x: x.cod_sankhya, reverse=reverse)
    elif sort_by == 'status':
        def get_status_sort(p):
            return (p.perfis and (p.perfis.get('alta') or p.perfis.get('baixa'))) or (p.parametros and len(p.parametros) > 0)
        produtos_filtrados.sort(key=get_status_sort, reverse=reverse)
    else: produtos_filtrados.sort(key=lambda x: x.descricao, reverse=reverse)

    total_itens = len(produtos_filtrados)
    total_paginas_mat = math.ceil(total_itens / LIMIT)
    page_mat = max(1, min(page_mat, total_paginas_mat)) if total_paginas_mat > 0 else 1
    
    start = (page_mat - 1) * LIMIT
    end = start + LIMIT
    produtos_paginados = produtos_filtrados[start:end]

    # === PARTE 2: DADOS DE AUDITORIA ===
    # Recupera cache
    dados_cache = cache_service.get()
    ensaios_audit = []
    materiais_audit = []
    
    if dados_cache:
        ensaios_raw = dados_cache['dados']
        materiais_audit = _normalizar_lista_materiais(dados_cache.get('materiais'))
        
        f_data = request.args.get('audit_data', '')
        f_status = request.args.get('audit_status', '')
        f_busca = request.args.get('audit_busca', '').upper().strip()
        
        page_audit = request.args.get('page_audit', 1, type=int)
        per_page_audit = 50

        # Filtragem em memÃƒÂ³ria
        for e in ensaios_raw:
            if f_data and e.data_hora.strftime('%Y-%m-%d') != f_data: continue
            if f_status and e.metodo_identificacao != f_status: continue
            if f_busca and f_busca not in str(e.lote).upper(): continue

            # LÃƒÂ³gica PadrÃƒÂ£o: Esconde 'LOTE' se sem filtros
            if not f_status and not f_busca and not f_data:
                if e.metodo_identificacao == 'LOTE': continue

            ensaios_audit.append(e)

        # OrdenaÃƒÂ§ÃƒÂ£o Auditoria
        peso = {'FANTASMA': 100, 'TEXTO': 90, 'MANUAL': 10, 'LOTE': 0}
        def get_data_segura(x): return x.data_hora if x.data_hora else datetime.min
        
        ensaios_audit.sort(
            key=lambda x: (peso.get(x.metodo_identificacao, 0), get_data_segura(x)), 
            reverse=True
        )

        total_registros_audit = len(ensaios_audit)
        total_paginas_audit = math.ceil(total_registros_audit / per_page_audit)
        page_audit = max(1, min(page_audit, total_paginas_audit)) if total_paginas_audit > 0 else 1
        
        start_a = (page_audit - 1) * per_page_audit
        end_a = start_a + per_page_audit
        ensaios_audit_paginados = ensaios_audit[start_a:end_a]
    else:
        ensaios_audit_paginados = []
        total_paginas_audit = 1
        total_registros_audit = 0
        page_audit = 1

    # === PARTE 3: GESTÃƒÆ’O DE USUÃƒÂRIOS (Admin) ===
    usuarios_lista = []
    if current_user.role == 'admin':
        usuarios_lista = Usuario.query.all()

    return render_template(
        'config.html', 
        # Dados Materiais
        produtos=produtos_paginados,
        configs_massas=configs_massas,
        regras_acao=regras_acao,
        query=query, filtro_tipo=filtro_tipo, filtro_status=filtro_status,
        pagina_atual_mat=page_mat, total_paginas_mat=total_paginas_mat, total_itens_mat=total_itens,
        sort_by=sort_by, order=order,
        
        # Dados Auditoria
        ensaios_audit=ensaios_audit_paginados,
        materiais_audit=materiais_audit,
        pagina_atual_audit=page_audit,
        total_paginas_audit=total_paginas_audit,
        total_registros_audit=total_registros_audit,
        audit_busca=request.args.get('audit_busca', ''),
        audit_status=request.args.get('audit_status', ''),
        audit_data=request.args.get('audit_data', ''),

        # Dados UsuÃƒÂ¡rios
        usuarios=usuarios_lista
    )

@app.route('/adicionar_usuario', methods=['POST'])
@login_required
def adicionar_usuario():
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard_home'))
        
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')

    if not username or not password or not role:
        flash("Preencha usuÃƒÂ¡rio, senha e perfil.", "warning")
        return redirect(url_for('pagina_config', _anchor='usuarios'))

    if Usuario.query.filter_by(username=username).first():
        flash(f"UsuÃƒÂ¡rio '{username}' jÃƒÂ¡ existe.", "warning")
    else:
        novo_user = Usuario(username=username, role=role)
        novo_user.set_password(password)
        db.session.add(novo_user)
        db.session.commit()
        flash(f"UsuÃƒÂ¡rio '{username}' criado com sucesso!", "success")
        
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/editar_usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard_home'))
        
    user_id = request.form.get('user_id')
    novo_username = request.form.get('username')
    nova_senha = request.form.get('password')
    novo_role = request.form.get('role')
    
    user = Usuario.query.get(user_id)
    if not user:
        flash("UsuÃƒÂ¡rio nÃƒÂ£o encontrado.", "danger")
        return redirect(url_for('pagina_config', _anchor='usuarios'))
        
    # Verifica se o novo username jÃƒÂ¡ existe (se for diferente do atual)
    if novo_username != user.username:
        existente = Usuario.query.filter_by(username=novo_username).first()
        if existente:
            flash(f"O nome de usuÃƒÂ¡rio '{novo_username}' jÃƒÂ¡ estÃƒÂ¡ em uso.", "warning")
            return redirect(url_for('pagina_config', _anchor='usuarios'))
    
    # Atualiza dados
    user.username = novo_username
    user.role = novo_role
    
    # SÃƒÂ³ atualiza a senha se for fornecida
    if nova_senha and nova_senha.strip():
        user.set_password(nova_senha)
        
    try:
        db.session.commit()
        flash(f"UsuÃƒÂ¡rio '{user.username}' atualizado com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar usuÃƒÂ¡rio: {e}", "danger")
        
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/remover_usuario/<int:user_id>')
@login_required
def remover_usuario(user_id):
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard_home'))
        
    user = Usuario.query.get(user_id)
    if user:
        if user.id == current_user.id:
            flash("VocÃƒÂª nÃƒÂ£o pode remover a si mesmo.", "danger")
        else:
            db.session.delete(user)
            db.session.commit()
            flash(f"UsuÃƒÂ¡rio '{user.username}' removido.", "success")
    
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/salvar_regras', methods=['POST'])
@login_required
def salvar_regras():
    if current_user.role != 'admin': return redirect(url_for('dashboard_home'))

    # Coleta dados das listas do formulÃƒÂ¡rio
    nomes = request.form.getlist('nome[]')
    scores = request.form.getlist('min_score[]')
    acoes = request.form.getlist('acao[]')
    cores = request.form.getlist('cor[]')
    marcados = request.form.getlist('exige_visc_real') 
    
    novas_regras = []
    for i in range(len(nomes)):
        # Verifica se o ÃƒÂ­ndice atual estÃƒÂ¡ na lista de checkboxes marcados
        eh_marcado = str(i) in marcados
        novas_regras.append({
            "id": i+1,
            "nome": nomes[i],
            "min_score": float(scores[i]) if scores[i] else 0,
            "exige_visc_real": eh_marcado,
            "acao": acoes[i],
            "cor": cores[i]
        })
        
    salvar_regras_acao(novas_regras)
    flash("Regras de aÃƒÂ§ÃƒÂ£o globais atualizadas!", "success")
    return redirect(url_for('pagina_config'))


@app.route('/salvar_config', methods=['POST'])
@login_required
def salvar_config():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard_home'))

    cod = request.form.get('cod_sankhya')
    
    def f(val): return float(val.replace(',', '.')) if val and val.strip() else None
    def i(val): return int(val) if val and val.strip() else 0
    
    specs = {}
    cfg_atual_produto = (carregar_configuracoes().get(str(cod), {}) or {})
    if isinstance(cfg_atual_produto, dict):
        for chave_custom in (
            'regra_abrasao_5n',
            'abrasao_5n_dureza_min',
            'abrasao_5n_dureza_max',
            'abrasao_metodologia_5n_dureza_min',
            'abrasao_metodologia_5n_dureza_max',
        ):
            if chave_custom in cfg_atual_produto:
                specs[chave_custom] = cfg_atual_produto[chave_custom]
    
    # --- 1. CAPTURA DE CABEÃƒâ€¡ALHO (TEMP/TEMPO) ---
    # Captura Cinza
    t_cinza = f(request.form.get('alta_cinza_temp_padrao'))
    tempo_cinza = f(request.form.get('alta_cinza_tempo_total'))
    
    # Captura Preto
    t_preto = f(request.form.get('alta_preto_temp_padrao'))
    tempo_preto = f(request.form.get('alta_preto_tempo_total'))
    
    # REGRA DE CÃƒâ€œPIA (CabeÃƒÂ§alho)
    # Se Cinza tem e Preto nÃƒÂ£o -> Preto recebe Cinza
    if t_cinza and not t_preto: t_preto = t_cinza
    if tempo_cinza and not tempo_preto: tempo_preto = tempo_cinza
    
    # Se Preto tem e Cinza nÃƒÂ£o -> Cinza recebe Preto (vice-versa)
    if t_preto and not t_cinza: t_cinza = t_preto
    if tempo_preto and not tempo_cinza: tempo_cinza = tempo_preto

    # Salva Alta
    if t_cinza: specs['alta_cinza_temp_padrao'] = t_cinza
    if tempo_cinza: specs['alta_cinza_tempo_total'] = tempo_cinza
    if t_preto: specs['alta_preto_temp_padrao'] = t_preto
    if tempo_preto: specs['alta_preto_tempo_total'] = tempo_preto

    # Salva Baixa (Simples)
    t_baixa = f(request.form.get('baixa_temp_padrao'))
    tempo_baixa = f(request.form.get('baixa_tempo_total'))
    if t_baixa: specs['baixa_temp_padrao'] = t_baixa
    if tempo_baixa: specs['baixa_tempo_total'] = tempo_baixa

    # Regra opcional da metodologia de abrasao 5 N (se vier da UI)
    ab5n_min = f(request.form.get('abrasao_5n_dureza_min'))
    ab5n_max = f(request.form.get('abrasao_5n_dureza_max'))
    if ab5n_min is not None or ab5n_max is not None:
        regra_existente = specs.get('regra_abrasao_5n', {}) if isinstance(specs.get('regra_abrasao_5n'), dict) else {}
        specs['regra_abrasao_5n'] = {
            'dureza_min': ab5n_min if ab5n_min is not None else regra_existente.get('dureza_min', 40.0),
            'dureza_max': ab5n_max if ab5n_max is not None else regra_existente.get('dureza_max', 50.0),
        }

    # --- 2. CAPTURA DE PARÃƒâ€šMETROS (LIMITES) ---
    params = ['Ts2', 'T90', 'Viscosidade']
    
    for p in params:
        # Peso ÃƒÂ© compartilhado (vem de um input sÃƒÂ³)
        peso_v = i(request.form.get(f"alta_{p}_peso"))
        
        # --- LÃƒâ€œGICA ALTA (CINZA vs PRETO) ---
        # Leitura Cinza
        min_c = f(request.form.get(f"alta_cinza_{p}_min"))
        alvo_c = f(request.form.get(f"alta_cinza_{p}_alvo"))
        max_c = f(request.form.get(f"alta_cinza_{p}_max"))
        
        # Leitura Preto
        min_p = f(request.form.get(f"alta_preto_{p}_min"))
        alvo_p = f(request.form.get(f"alta_preto_{p}_alvo"))
        max_p = f(request.form.get(f"alta_preto_{p}_max"))
        
        # REGRA DE CÃƒâ€œPIA (Limites)
        # Se configurou Cinza mas esqueceu Preto -> Copia
        if (min_c or alvo_c or max_c) and not (min_p or alvo_p or max_p):
            min_p, alvo_p, max_p = min_c, alvo_c, max_c
            
        # Se configurou Preto mas esqueceu Cinza -> Copia
        elif (min_p or alvo_p or max_p) and not (min_c or alvo_c or max_c):
            min_c, alvo_c, max_c = min_p, alvo_p, max_p

        # GravaÃƒÂ§ÃƒÂ£o Cinza
        if min_c is not None or alvo_c is not None or max_c is not None:
            specs[f"alta_cinza_{p}"] = {
                "min": min_c if min_c is not None else 0, 
                "alvo": alvo_c if alvo_c is not None else 0, 
                "max": max_c if max_c is not None else 0, 
                "peso": peso_v
            }
            
        # GravaÃƒÂ§ÃƒÂ£o Preto
        if min_p is not None or alvo_p is not None or max_p is not None:
            specs[f"alta_preto_{p}"] = {
                "min": min_p if min_p is not None else 0, 
                "alvo": alvo_p if alvo_p is not None else 0, 
                "max": max_p if max_p is not None else 0, 
                "peso": peso_v
            }

        # --- LÃƒâ€œGICA BAIXA (Mantida Simples) ---
        min_b = f(request.form.get(f"baixa_{p}_min"))
        alvo_b = f(request.form.get(f"baixa_{p}_alvo"))
        max_b = f(request.form.get(f"baixa_{p}_max"))
        peso_b = i(request.form.get(f"baixa_{p}_peso"))
        
        if min_b is not None or alvo_b is not None or max_b is not None:
            specs[f"baixa_{p}"] = {
                "min": min_b if min_b is not None else 0, "alvo": alvo_b if alvo_b is not None else 0,
                "max": max_b if max_b is not None else 0, "peso": peso_b
            }

    # --- 3. ESPECIFICACOES DE PROPRIEDADES FISICAS ---
    def add_spec_fisica(nome, min_v=None, max_v=None):
        if min_v is None and max_v is None:
            return
        specs[f"baixa_{nome}"] = {
            "min": min_v,
            "alvo": None,
            "max": max_v,
            "peso": 0
        }

    # Min/Max
    add_spec_fisica(
        "Dureza",
        f(request.form.get("fisica_Dureza_min")),
        f(request.form.get("fisica_Dureza_max")),
    )
    add_spec_fisica(
        "Densidade",
        f(request.form.get("fisica_Densidade_min")),
        f(request.form.get("fisica_Densidade_max")),
    )
    add_spec_fisica(
        "Resiliencia",
        f(request.form.get("fisica_Resiliencia_min")),
        f(request.form.get("fisica_Resiliencia_max")),
    )

    # Apenas Max
    add_spec_fisica(
        "Abrasao",
        None,
        f(request.form.get("fisica_Abrasao_max")),
    )

    # Apenas Min
    add_spec_fisica(
        "TensaoRuptura",
        f(request.form.get("fisica_TensaoRuptura_min")),
        None,
    )
    add_spec_fisica(
        "Alongamento",
        f(request.form.get("fisica_Alongamento_min")),
        None,
    )
    add_spec_fisica(
        "Rasgo",
        f(request.form.get("fisica_Rasgo_min")),
        None,
    )

    salvar_configuracao(cod, specs)
    carregar_referencias_estaticas()
    
    flash(f"ConfiguraÃƒÂ§ÃƒÂ£o do produto {cod} salva (Sincronizada Cinza/Preto)!", "success")
    return redirect(url_for('pagina_config', q=cod))

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

                target_dict[c_id] = {
                    'label': label,
                    'material': material_desc,
                    'tempType': temp_type, # <--- Enviando para o Frontend
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

        return jsonify({
            'ids': lista_ids,
            'ids_reometria': ids_reo,
            'ids_viscosidade': ids_visc,
            'materiais': materiais,
            'reometria': list(datasets_reo.values()),
            'viscosidade': list(datasets_visc.values())
        })
        
    except Exception as e:
        print(f"ERRO API: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/auditoria')
@login_required
def pagina_auditoria():
    # ... (seu cÃƒÂ³digo existente de filtro e ordenaÃƒÂ§ÃƒÂ£o) ...

    # --- CORREÃƒâ€¡ÃƒÆ’O AQUI ---
    # Cria um dicionÃƒÂ¡rio mutÃƒÂ¡vel a partir dos argumentos da URL
    filtros_para_template = dict(request.args)
    # Remove 'page' para evitar conflito no url_for do template
    if 'page' in filtros_para_template:
        del filtros_para_template['page']

    # Recupera dados se for chamado diretamente (mantendo compatibilidade)
    dados_cache = cache_service.get()
    ensaios = dados_cache['dados'] if dados_cache else []
    materiais = _normalizar_lista_materiais(dados_cache.get('materiais')) if dados_cache else []
    
    # PaginaÃƒÂ§ÃƒÂ£o simples para manter a rota funcionando
    page = request.args.get('page', 1, type=int)
    per_page = 50
    total_registros = len(ensaios)
    total_paginas = math.ceil(total_registros / per_page)
    ensaios_paginados = ensaios[(page-1)*per_page : page*per_page]

    return render_template(
        'auditoria.html', 
        ensaios=ensaios_paginados, 
        materiais=materiais,
        pagina_atual=page,
        total_paginas=total_paginas,
        total_registros=total_registros,
        request_args=filtros_para_template 
    )

@app.route('/salvar_correcao', methods=['POST'])
@login_required
def salvar_correcao():
    # 1. Coleta dados
    texto_original = request.form.get('lote_original_key') or request.form.get('texto_original')
    lote_correto = request.form.get('novo_lote') or request.form.get('lote_correto')
    massa_correta = request.form.get('massa') or request.form.get('massa_correta')

    if not texto_original or not lote_correto:
        flash("Dados incompletos para salvar.", "warning")
        return redirect(url_for('pagina_config', _anchor='ensinar'))

    massa_raw = str(massa_correta or '').strip()
    if not massa_raw or massa_raw.upper() in {'NONE', 'NULL', 'NULO', 'SELECIONE...', 'SELECIONE'}:
        flash("Selecione uma massa valida antes de salvar.", "warning")
        return redirect(url_for('pagina_config', _anchor='ensinar'))

    key_original = str(texto_original).strip().upper()
    lote_clean = str(lote_correto).strip().upper()
    massa_clean = massa_raw.upper()

    # --- NOVOS DADOS DE LOG ---
    user_log = current_user.username
    time_log = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        ensinar_lote(key_original, lote_clean, massa_clean, usuario=user_log)
        recarregar_aprendizado_memoria()
        total_ajustados = aplicar_correcoes_persistidas_no_consolidado([key_original])
        recarregar_cache_memoria()

        flash(
            f"Regra salva e aplicada! Ajustados: {total_ajustados} "
            f"- {user_log} as {time_log}.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] Erro ao salvar no SQLite: {e}")
        flash("Erro ao salvar regra localmente.", "danger")

    return redirect(url_for('pagina_config', _anchor='ensinar'))


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


<<<<<<< ours
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

=======
@app.route('/reometria/fit')
@login_required
def reometria_fit():
    filters = {
        'date_start': request.args.get('date_start', ''),
        'date_end': request.args.get('date_end', ''),
        'q': request.args.get('q', ''),
    }
    ensaios = list_ensaios_for_fit(filters['date_start'] or None, filters['date_end'] or None, filters['q'] or None)
    return render_template('reometria/fit.html', ensaios=ensaios, filters=filters)


@app.route('/reometria/fit/preview/<int:cod_ensaio>')
@login_required
def reometria_fit_preview(cod_ensaio):
    curve = get_preview_curve(cod_ensaio)
    if not curve:
        flash('Curva não encontrada.', 'warning')
        return redirect(url_for('reometria_fit'))
    return render_template('reometria/preview.html', curve=curve)


@app.route('/reometria/fit/run', methods=['POST'])
@login_required
def reometria_fit_run():
    cod_ensaios = [int(x) for x in request.form.getlist('cod_ensaios') if str(x).strip()]
    if len(cod_ensaios) < 2:
        flash('Selecione pelo menos 2 curvas para o ajuste.', 'warning')
        return redirect(url_for('reometria_fit'))

    payload = run_fit(cod_ensaios)
    if not payload.get('success'):
        flash(payload.get('message', 'Ajuste falhou.'), 'danger')
    else:
        flash('Ajuste executado com sucesso.', 'success')
    return redirect(url_for('reometria_fit_result', fit_id=payload['fit_id']))


@app.route('/reometria/fit/result/<fit_id>')
@login_required
def reometria_fit_result(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        flash('Resultado de fit não encontrado.', 'danger')
        return redirect(url_for('reometria_fit'))
    return render_template('reometria/fit_result.html', payload=payload)


@app.route('/reometria/simulate/<fit_id>')
@login_required
def reometria_simulate_form(fit_id):
    mode = request.args.get('mode', 'prensa')
    if mode not in ('prensa', 'autoclave'):
        mode = 'prensa'
    payload = load_fit_payload(fit_id)
    if not payload:
        flash('Fit não encontrado.', 'danger')
        return redirect(url_for('reometria_fit'))
    return render_template('reometria/sim_form.html', fit_id=fit_id, mode=mode)


@app.route('/reometria/simulate/run/<fit_id>', methods=['POST'])
@login_required
def reometria_simulate_run(fit_id):
    payload = load_fit_payload(fit_id)
    if not payload:
        flash('Fit não encontrado.', 'danger')
        return redirect(url_for('reometria_fit'))

    mode = request.form.get('mode', 'prensa')
    dim = int(request.form.get('dim', 1))
    shape = request.form.get('shape', '200')
    dx = float(request.form.get('dx', 0.001))
    dt = float(request.form.get('dt', 0.5))
    t_end = float(request.form.get('t_end', 600))
    mold_temp_c = float(request.form.get('mold_temp_c', 170))
    init_temp_c = float(request.form.get('init_temp_c', 25))
    ramp_rate = float(request.form.get('ramp_rate', 0))
    snapshot_every = int(request.form.get('snapshot_every', 20))

    sim_id, _ = run_simulation(payload, mode, dim, shape, dx, dt, t_end, mold_temp_c, init_temp_c, ramp_rate, snapshot_every)
    return redirect(url_for('reometria_simulate_view', sim_id=sim_id))


@app.route('/reometria/simulate/view/<sim_id>')
@login_required
def reometria_simulate_view(sim_id):
    sim = load_simulation(sim_id)
    if not sim:
        flash('Simulação não encontrada.', 'danger')
        return redirect(url_for('reometria_fit'))

    serializable = {
        **sim,
        'times': sim['times'].tolist(),
        't_snaps': sim['t_snaps'].tolist(),
        'alpha_snaps': sim['alpha_snaps'].tolist(),
    }
    return render_template('reometria/sim_view.html', sim=sim, sim_json=json.dumps(serializable))
>>>>>>> theirs


if __name__ == '__main__':
    app.run(debug=True)

