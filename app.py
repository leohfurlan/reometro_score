from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models.usuario import db, Usuario
from models.consolidado import EnsaioConsolidado # Novo Modelo
from cache_manager import CacheManager

from datetime import datetime
import math
import os
import statistics 
import json
import sqlite3
from sqlalchemy import or_, func, case, desc, and_ # Adicionado para conexão local


# Configurações e Modelos
from config import Config
from services.config_manager import carregar_regras_acao, salvar_regras_acao, salvar_configuracao
from services.learning_service import ensinar_lote
from services.report_service import gerar_estrutura_relatorio
from models.score_versioning import ScoreResultado

# --- IMPORTAÇÃO: SERVIÇO DE ETL ---
from services.etl_service import (
    processar_carga_dados, 
    carregar_referencias_estaticas,
    get_catalogo_codigo,
    _MAPA_GRUPOS
)

# --- NOVA IMPORTAÇÃO: SHAREPOINT LOADER ---
try:
    from sharepoint_loader import baixar_excel_sharepoint
except ImportError:
    baixar_excel_sharepoint = None
    print("⚠️ Aviso: 'sharepoint_loader.py' não encontrado. O download automático será desativado.")

# Caminho local padrão para o cache baixado do SharePoint
CACHE_PLANILHA_SHAREPOINT = "cache_reg403_sharepoint.xlsx"

def preparar_planilha_sharepoint(forcar_download=False):
    """
    Garante que a planilha venha do SharePoint e define CAMINHO_REG403
    apontando para o arquivo cacheado localmente.
    """
    if not baixar_excel_sharepoint:
        return None

    caminho_cache = os.path.abspath(CACHE_PLANILHA_SHAREPOINT)

    # Se já temos um cache e não foi solicitado força de download, reutiliza.
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
        print(f"⚠️ Falha ao baixar planilha do SharePoint: {e}")

    return None

from connection import connect_to_database

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

# --- CONFIGURAÇÃO DO BANCO DE USUÁRIOS (SQLite Local) ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users_reoscore.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Cria o banco de dados na primeira execução se não existir
with app.app_context():
    db.create_all()
    # Garante colunas novas sem precisar de migração formal
    def ensure_ids_agrupados_column():
        try:
            db_path = 'users_reoscore.db'
            if os.path.exists(os.path.join('instance', db_path)):
                db_path = os.path.join('instance', db_path)

            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(ensaio_consolidado)")
            colunas = [row[1] for row in cursor.fetchall()]
            if 'ids_agrupados' not in colunas:
                cursor.execute("ALTER TABLE ensaio_consolidado ADD COLUMN ids_agrupados TEXT")
                conn.commit()
        except Exception as e:
            print(f"⚠️ Falha ao ajustar esquema do ensaio_consolidado: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    ensure_ids_agrupados_column()

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))


# ==========================================
# 0. CONFIGURAÇÃO SIDECAR (ARQUIVO LOCAL DE REGRAS)
# ==========================================
def get_local_db():
    """Conecta ao banco SQLite local onde temos permissão de escrita"""
    # Tenta conectar na pasta instance (padrão Flask) ou raiz
    db_path = 'users_reoscore.db'
    if os.path.exists(os.path.join('instance', db_path)):
        db_path = os.path.join('instance', db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def iniciar_tabela_aprendizado():
    """Cria tabela e aplica migrações de colunas novas se necessário"""
    try:
        conn = get_local_db()
        cursor = conn.cursor()
        
        # 1. Cria a tabela básica se não existir (Schema antigo + novos campos)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aprendizado_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_original TEXT UNIQUE NOT NULL,
                lote_novo TEXT NOT NULL,
                massa_nova TEXT NOT NULL,
                usuario_log TEXT,
                data_log TEXT
            )
        """)
        
        # 2. Migração: Tenta adicionar as colunas caso o banco já exista (versão antiga)
        try:
            cursor.execute("ALTER TABLE aprendizado_local ADD COLUMN usuario_log TEXT")
        except sqlite3.OperationalError: pass
            
        try:
            cursor.execute("ALTER TABLE aprendizado_local ADD COLUMN data_log TEXT")
        except sqlite3.OperationalError: pass

        conn.commit()
        conn.close()
        print("✅ Tabela de aprendizado local verificada e atualizada (Schema Logs).")
    except Exception as e:
        print(f"❌ Erro ao inicializar tabela local: {e}")

def aplicar_sobreposicao_local(dados_brutos):
    """
    Lê as regras do SQLite e aplica sobre a lista de objetos Ensaio em memória.
    Isso 'corrige' os dados vindos do SQL Server sem precisar de UPDATE lá.
    """
    try:
        # 1. Carrega todas as regras
        conn = get_local_db()
        # Verifica se a tabela existe antes de consultar
        try:
            regras = conn.execute("SELECT chave_original, lote_novo, massa_nova FROM aprendizado_local").fetchall()
        except sqlite3.OperationalError:
            # Tabela ainda não criada
            conn.close()
            return dados_brutos
            
        conn.close()
        
        # Cria mapa para busca rápida: {'TEXTO_FEIO': {'lote': '999', 'massa': 'X'}, ...}
        mapa_correcoes = {r[0]: {'lote': r[1], 'massa': r[2]} for r in regras}
        
        if not mapa_correcoes:
            return dados_brutos

        count = 0
        # 2. Varre os dados e aplica o patch em memória
        for ensaio in dados_brutos:
            # Tenta casar pelo Lote Original ou pelo Material Original
            lote_orig = str(getattr(ensaio, 'lote_original', '')).strip().upper()
            mat_orig = str(getattr(ensaio, 'material_original', '')).strip().upper()
            
            # Verifica se alguma das chaves originais está no mapa de correção
            regra = None
            if lote_orig in mapa_correcoes:
                regra = mapa_correcoes[lote_orig]
            elif mat_orig in mapa_correcoes:
                regra = mapa_correcoes[mat_orig]
                
            if regra:
                # APLICA A CORREÇÃO NO OBJETO EM MEMÓRIA
                ensaio.lote = regra['lote']
                
                # Se tiver objeto de massa, atualiza a descrição visualmente
                if ensaio.massa:
                    ensaio.massa.descricao = regra['massa']
                
                # Marca como corrigido manualmente
                ensaio.metodo_identificacao = "MANUAL"
                
                # Opcional: Recalcular score se necessário (normalmente specs estão atrelados ao cod_sankhya)
                # Se a massa mudou drasticamente, o score antigo pode estar inválido, 
                # mas recalcular exigiria recarregar specs. Para visualização rápida, isso basta.
                
                count += 1
                
        print(f"🧠 Sobrescrita Local: {count} registros corrigidos em memória via SQLite.")
        return dados_brutos

    except Exception as e:
        print(f"⚠️ Erro ao aplicar regras locais: {e}")
        return dados_brutos

# Inicializa tabela auxiliar
iniciar_tabela_aprendizado()


# ==========================================
# 1. INICIALIZAÇÃO E CACHE
# ==========================================
print("\n=== REOSCORE V13 (MODULARIZED & SIDECAR) ===")

# Certifica que o ETL vai usar somente a planilha baixada do SharePoint
caminho_sharepoint_inicial = preparar_planilha_sharepoint(forcar_download=False)
if caminho_sharepoint_inicial:
    print(f"   > Planilha SharePoint configurada em: {caminho_sharepoint_inicial}")
else:
    print("⚠️ Aviso: Planilha do SharePoint não configurada. Use 'Atualizar Dados' para sincronizar.")

carregar_referencias_estaticas()

# Inicializa o gerenciador com TTL de 30 min e Max 500MB
cache_service = CacheManager(ttl_minutes=30, max_size_mb=500)

# ==========================================
# 2. ROTAS DE AUTENTICAÇÃO
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = Usuario.query.filter_by(username=username).first()
        
        if user and user.check_password(password, bcrypt):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Login ou senha inválidos.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('login'))

@app.route('/criar_admin')
def criar_admin():
    if Usuario.query.filter_by(username='admin').first():
        return "Admin já existe."
    
    hashed_pw = bcrypt.generate_password_hash('senha123').decode('utf-8')
    novo_admin = Usuario(username='admin', password_hash=hashed_pw, role='admin')
    db.session.add(novo_admin)
    db.session.commit()
    return "Admin criado com sucesso! (User: admin / Pass: senha123)"

@app.route('/criar_operador')
def criar_operador():
    if Usuario.query.filter_by(username='operador').first():
        return "Usuário 'operador' já existe."
    
    hashed_pw = bcrypt.generate_password_hash('vulca123').decode('utf-8')
    novo_user = Usuario(username='operador', password_hash=hashed_pw, role='operador')
    db.session.add(novo_user)
    db.session.commit()
    return "Usuário 'operador' criado com sucesso! (User: operador / Pass: vulca123)"

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
                flash("✅ Planilha baixada do SharePoint com sucesso!", "success")
            else:
                flash("⚠️ Falha no download do SharePoint. Usando cache.", "warning")

        # --- PASSO 2: EXECUÇÃO DO ETL ---
        stats = processar_carga_dados()
        
        if stats:
            total = stats.get('total', 0)
            tempo = stats.get('tempo', 0)
            flash(f"Base atualizada com sucesso! {total} registros processados em {tempo:.1f}s.", "info")
        else:
            flash("Erro ao processar carga de dados (ETL retornou vazio).", "danger")
            
    except Exception as e:
        print(f"❌ Erro Crítico na Rota Atualizar: {e}")
        flash(f"Erro crítico: {str(e)}", "danger")

    # CORREÇÃO AQUI: Redireciona para o novo nome da função da home
    return redirect(url_for('dashboard_home'))

@app.route('/')
@login_required
def dashboard_home():
    """
    ROTA ESTRATÉGICA: Visão geral de KPIs e Gráficos Gerenciais.
    Não carrega a lista de 50k registros, focando em agregações rápidas.
    """
    # Filtros de Período (Único filtro relevante para o Dashboard Global)
    d_start = request.args.get('date_start', '')
    d_end = request.args.get('date_end', '')
    
    query = EnsaioConsolidado.query
    
    if d_start:
        try: query = query.filter(EnsaioConsolidado.data_hora >= datetime.strptime(d_start, '%Y-%m-%d'))
        except: pass
    if d_end:
        try: query = query.filter(EnsaioConsolidado.data_hora <= datetime.strptime(d_end, '%Y-%m-%d').replace(hour=23, minute=59))
        except: pass

    # --- CÁLCULO DE KPIS (Agregado) ---
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

    # --- GRÁFICO DE TENDÊNCIA (Últimos 30 dias com dados) ---
    trend_data = query.with_entities(
        func.strftime('%Y-%m-%d', EnsaioConsolidado.data_hora).label('dia'),
        func.avg(EnsaioConsolidado.score_final).label('media')
    ).group_by('dia').order_by(desc('dia')).limit(30).all()
    
    # Reverte para cronológico
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
    ROTA OPERACIONAL: Tabela de Lotes, Filtros Avançados, Busca.
    (Antiga dashboard, agora focada na lista)
    """
    # Filtros
    search = request.args.get('search', '').strip().upper()
    f_mat = request.args.get('material_filter', '')
    f_acao = request.args.get('acao_filter', '')
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    page = request.args.get('page', 1, type=int)
    LIMIT = 50 # Mais itens por página na visão operacional

    query = EnsaioConsolidado.query

    if search:
        query = query.filter(or_(
            EnsaioConsolidado.lote.contains(search),
            EnsaioConsolidado.massa_descricao.contains(search),
            EnsaioConsolidado.batch.contains(search)
        ))
    if f_mat: query = query.filter(EnsaioConsolidado.massa_descricao == f_mat)
    if f_acao:
        if f_acao == "APROVADOS": query = query.filter(EnsaioConsolidado.score_final >= 70) # Simplificação
        elif f_acao == "REPROVADO": query = query.filter(EnsaioConsolidado.score_final < 70)

    # Ordenação
    col_map = {
        'id': EnsaioConsolidado.id_ensaio, 'data': EnsaioConsolidado.data_hora,
        'lote': EnsaioConsolidado.lote, 'score': EnsaioConsolidado.score_final
    }
    col = col_map.get(sort_by, EnsaioConsolidado.data_hora)
    query = query.order_by(col.asc() if order == 'asc' else col.desc())

    # Paginação
    paginacao = query.paginate(page=page, per_page=LIMIT, error_out=False)
    
    # Filtros auxiliares
    materiais = db.session.query(EnsaioConsolidado.massa_descricao).distinct().order_by(EnsaioConsolidado.massa_descricao).all()
    materiais_filtro = [{'descricao': m[0]} for m in materiais if m[0]]

    context = {
        'ensaios': paginacao.items,
        'total_registros_filtrados': paginacao.total,
        'pagina_atual': page, 'total_paginas': paginacao.pages,
        'materiais_filtro': materiais_filtro,
        'search_term': search, 'material_filter': f_mat, 'acao_filter': f_acao,
        'sort_by': sort_by, 'order': order
    }

    if request.headers.get('HX-Request'):
        return render_template('tabela_dados.html', **context)
        
    return render_template('controle_qualidade.html', **context)

@app.route('/analise/<int:id_ensaio>')
@login_required
def analise_curva(id_ensaio):
    """
    ROTA ANALÍTICA: Detalhes profundos de um ensaio específico.
    """
    ensaio = EnsaioConsolidado.query.get_or_404(id_ensaio)
    
    # Busca o detalhe do cálculo (logs da engine)
    resultado = ScoreResultado.query.filter_by(id_ensaio=id_ensaio).order_by(ScoreResultado.id.desc()).first()
    detalhes_score = resultado.detalhes_log if resultado else {}
    
    # Se 'params' estiver aninhado (dependendo da versão da engine)
    if 'params' in detalhes_score:
        detalhes_score = detalhes_score['params']

    return render_template('detalhe_curva.html', ensaio=ensaio, detalhes=detalhes_score)

# ==========================================
# 4. ROTAS DE CONFIGURAÇÃO (ADMIN)
# ==========================================

@app.route('/config')
@login_required
def pagina_config():
    # OBS: Se o usuário NÃO for admin, ele ainda vai carregar os dados de materiais
    # abaixo, mas o template não vai mostrar. Não é crítico para performance.
    
    # === PARTE 1: CONFIGURAÇÃO DE MATERIAIS ===
    regras_acao = carregar_regras_acao()

    query = request.args.get('q', '').strip().upper()
    filtro_tipo = request.args.get('tipo', '')
    filtro_status = request.args.get('status', '')
    
    # Paginação de Materiais
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
        materiais_audit = dados_cache['materiais']
        
        f_data = request.args.get('audit_data', '')
        f_status = request.args.get('audit_status', '')
        f_busca = request.args.get('audit_busca', '').upper().strip()
        
        page_audit = request.args.get('page_audit', 1, type=int)
        per_page_audit = 50

        # Filtragem em memória
        for e in ensaios_raw:
            if f_data and e.data_hora.strftime('%Y-%m-%d') != f_data: continue
            if f_status and e.metodo_identificacao != f_status: continue
            if f_busca and f_busca not in str(e.lote).upper(): continue

            # Lógica Padrão: Esconde 'LOTE' se sem filtros
            if not f_status and not f_busca and not f_data:
                if e.metodo_identificacao == 'LOTE': continue

            ensaios_audit.append(e)

        # Ordenação Auditoria
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

    # === PARTE 3: GESTÃO DE USUÁRIOS (Admin) ===
    usuarios_lista = []
    if current_user.role == 'admin':
        usuarios_lista = Usuario.query.all()

    return render_template(
        'config.html', 
        # Dados Materiais
        produtos=produtos_paginados,
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

        # Dados Usuários
        usuarios=usuarios_lista
    )

@app.route('/adicionar_usuario', methods=['POST'])
@login_required
def adicionar_usuario():
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard'))
        
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    
    if Usuario.query.filter_by(username=username).first():
        flash(f"Usuário '{username}' já existe.", "warning")
    else:
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        novo_user = Usuario(username=username, password_hash=hashed_pw, role=role)
        db.session.add(novo_user)
        db.session.commit()
        flash(f"Usuário '{username}' criado com sucesso!", "success")
        
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/editar_usuario', methods=['POST'])
@login_required
def editar_usuario():
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard'))
        
    user_id = request.form.get('user_id')
    novo_username = request.form.get('username')
    nova_senha = request.form.get('password')
    novo_role = request.form.get('role')
    
    user = Usuario.query.get(user_id)
    if not user:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for('pagina_config', _anchor='usuarios'))
        
    # Verifica se o novo username já existe (se for diferente do atual)
    if novo_username != user.username:
        existente = Usuario.query.filter_by(username=novo_username).first()
        if existente:
            flash(f"O nome de usuário '{novo_username}' já está em uso.", "warning")
            return redirect(url_for('pagina_config', _anchor='usuarios'))
    
    # Atualiza dados
    user.username = novo_username
    user.role = novo_role
    
    # Só atualiza a senha se for fornecida
    if nova_senha and nova_senha.strip():
        user.password_hash = bcrypt.generate_password_hash(nova_senha).decode('utf-8')
        
    try:
        db.session.commit()
        flash(f"Usuário '{user.username}' atualizado com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar usuário: {e}", "danger")
        
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/remover_usuario/<int:user_id>')
@login_required
def remover_usuario(user_id):
    if current_user.role != 'admin':
        flash("Acesso negado.", "danger")
        return redirect(url_for('dashboard'))
        
    user = Usuario.query.get(user_id)
    if user:
        if user.id == current_user.id:
            flash("Você não pode remover a si mesmo.", "danger")
        else:
            db.session.delete(user)
            db.session.commit()
            flash(f"Usuário '{user.username}' removido.", "success")
    
    return redirect(url_for('pagina_config', _anchor='usuarios'))

@app.route('/salvar_regras', methods=['POST'])
@login_required
def salvar_regras():
    if current_user.role != 'admin': return redirect(url_for('dashboard'))

    # Coleta dados das listas do formulário
    nomes = request.form.getlist('nome[]')
    scores = request.form.getlist('min_score[]')
    acoes = request.form.getlist('acao[]')
    cores = request.form.getlist('cor[]')
    marcados = request.form.getlist('exige_visc_real') 
    
    novas_regras = []
    for i in range(len(nomes)):
        # Verifica se o índice atual está na lista de checkboxes marcados
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
    flash("Regras de ação globais atualizadas!", "success")
    return redirect(url_for('pagina_config'))


@app.route('/salvar_config', methods=['POST'])
@login_required
def salvar_config():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cod = request.form.get('cod_sankhya')
    
    def f(val): return float(val.replace(',', '.')) if val and val.strip() else None
    def i(val): return int(val) if val and val.strip() else 0
    
    specs = {}
    
    # --- 1. CAPTURA DE CABEÇALHO (TEMP/TEMPO) ---
    # Captura Cinza
    t_cinza = f(request.form.get('alta_cinza_temp_padrao'))
    tempo_cinza = f(request.form.get('alta_cinza_tempo_total'))
    
    # Captura Preto
    t_preto = f(request.form.get('alta_preto_temp_padrao'))
    tempo_preto = f(request.form.get('alta_preto_tempo_total'))
    
    # REGRA DE CÓPIA (Cabeçalho)
    # Se Cinza tem e Preto não -> Preto recebe Cinza
    if t_cinza and not t_preto: t_preto = t_cinza
    if tempo_cinza and not tempo_preto: tempo_preto = tempo_cinza
    
    # Se Preto tem e Cinza não -> Cinza recebe Preto (vice-versa)
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

    # --- 2. CAPTURA DE PARÂMETROS (LIMITES) ---
    params = ['Ts2', 'T90', 'Viscosidade']
    
    for p in params:
        # Peso é compartilhado (vem de um input só)
        peso_v = i(request.form.get(f"alta_{p}_peso"))
        
        # --- LÓGICA ALTA (CINZA vs PRETO) ---
        # Leitura Cinza
        min_c = f(request.form.get(f"alta_cinza_{p}_min"))
        alvo_c = f(request.form.get(f"alta_cinza_{p}_alvo"))
        max_c = f(request.form.get(f"alta_cinza_{p}_max"))
        
        # Leitura Preto
        min_p = f(request.form.get(f"alta_preto_{p}_min"))
        alvo_p = f(request.form.get(f"alta_preto_{p}_alvo"))
        max_p = f(request.form.get(f"alta_preto_{p}_max"))
        
        # REGRA DE CÓPIA (Limites)
        # Se configurou Cinza mas esqueceu Preto -> Copia
        if (min_c or alvo_c or max_c) and not (min_p or alvo_p or max_p):
            min_p, alvo_p, max_p = min_c, alvo_c, max_c
            
        # Se configurou Preto mas esqueceu Cinza -> Copia
        elif (min_p or alvo_p or max_p) and not (min_c or alvo_c or max_c):
            min_c, alvo_c, max_c = min_p, alvo_p, max_p

        # Gravação Cinza
        if min_c is not None or alvo_c is not None or max_c is not None:
            specs[f"alta_cinza_{p}"] = {
                "min": min_c if min_c is not None else 0, 
                "alvo": alvo_c if alvo_c is not None else 0, 
                "max": max_c if max_c is not None else 0, 
                "peso": peso_v
            }
            
        # Gravação Preto
        if min_p is not None or alvo_p is not None or max_p is not None:
            specs[f"alta_preto_{p}"] = {
                "min": min_p if min_p is not None else 0, 
                "alvo": alvo_p if alvo_p is not None else 0, 
                "max": max_p if max_p is not None else 0, 
                "peso": peso_v
            }

        # --- LÓGICA BAIXA (Mantida Simples) ---
        min_b = f(request.form.get(f"baixa_{p}_min"))
        alvo_b = f(request.form.get(f"baixa_{p}_alvo"))
        max_b = f(request.form.get(f"baixa_{p}_max"))
        peso_b = i(request.form.get(f"baixa_{p}_peso"))
        
        if min_b is not None or alvo_b is not None or max_b is not None:
            specs[f"baixa_{p}"] = {
                "min": min_b if min_b is not None else 0, "alvo": alvo_b if alvo_b is not None else 0,
                "max": max_b if max_b is not None else 0, "peso": peso_b
            }

    salvar_configuracao(cod, specs)
    carregar_referencias_estaticas()
    
    flash(f"Configuração do produto {cod} salva (Sincronizada Cinza/Preto)!", "success")
    return redirect(url_for('pagina_config', q=cod))

@app.route('/api/grafico')
@login_required
def api_grafico():
    # Recupera snapshot do cache no início para evitar NameError em rotas paralelas
    dados_cache = cache_service.get()
    if not dados_cache:
        return jsonify({'error': 'Cache vazio. Atualize os dados.'}), 400

    try:
        ids_str = request.args.get('ids', '')
        modo_lote = request.args.get('mode', '') == 'lote'

        if not ids_str:
            return jsonify({})

        # 1. IDs das LINHAS selecionadas
        selected_parent_ids = [int(x) for x in ids_str.split(',') if x.isdigit()]

        # Limite dinâmico
        limite = 100 if modo_lote else 10

        if len(selected_parent_ids) > limite:
            return jsonify({'error': f'Muitos dados ({len(selected_parent_ids)}). Limite é {limite}.'}), 400

        all_ids_to_fetch = set()
        map_id_to_parent = {}

        # 2. Expandir IDs
        for cached in dados_cache['dados']:
            cached_id = int(cached.id_ensaio)
            if cached_id in selected_parent_ids:
                ids_filhos = getattr(cached, 'ids_agrupados_lista', None) or [cached_id]
                for child_id in ids_filhos:
                    c_id_int = int(child_id)
                    all_ids_to_fetch.add(c_id_int)
                    map_id_to_parent[c_id_int] = cached

        if not all_ids_to_fetch:
            return jsonify({'error': 'IDs não encontrados no cache.'}), 404

        # 3. Busca SQL
        conn = connect_to_database()
        cursor = conn.cursor()

        lista_ids = list(all_ids_to_fetch)
        placeholders = ','.join('?' * len(lista_ids))

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

        cursor.execute(query, lista_ids)
        rows = cursor.fetchall()
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
            if not parent:
                continue

            # Classificação
            dados_grupo = _MAPA_GRUPOS.get(c_grupo, {})
            tipo_maquina = dados_grupo.get('tipo', 'INDEFINIDO')

            is_viscosity = False

            if tipo_maquina == 'VISCOSIMETRO':
                is_viscosity = True
            elif tipo_maquina == 'REOMETRO':
                is_viscosity = False
            else:
                if 90 <= c_temp <= 115:
                    is_viscosity = True
                elif c_temp >= 120:
                    is_viscosity = False
                else:
                    is_viscosity = (getattr(parent, 'tipo_ensaio', '').upper() == 'VISCOSIDADE')

            target_dict = datasets_visc if is_viscosity else datasets_reo

            if c_id not in target_dict:
                cod_s = parent.massa.cod_sankhya if parent.massa else '??'
                batch_s = parent.batch if parent.batch else '0'
                label = f"{cod_s} - Batch {batch_s}"

                # --- NOVO: Classificação do Subtipo para o Filtro ---
                temp_type = 'GERAL'
                if not is_viscosity:
                    temp_type = 'ALTA' if c_temp >= 175 else 'BAIXA'
                # ----------------------------------------------------

                target_dict[c_id] = {
                    'label': label,
                    'tempType': temp_type,  # <--- Enviando para o Frontend
                    'data': [],
                    'pointRadius': 0,
                    'borderWidth': 2,
                    'tension': 0.4,
                    'fill': False,
                    # Cores dinâmicas
                    'borderColor': '#dc3545' if temp_type == 'ALTA' else '#0d6efd'
                }

            target_dict[c_id]['data'].append({'x': c_time, 'y': c_val})

        return jsonify({
            'reometria': list(datasets_reo.values()),
            'viscosidade': list(datasets_visc.values())
        })

    except Exception as e:
        app.logger.exception('ERRO API /api/grafico')
        return jsonify({'error': str(e)}), 500


@app.route('/auditoria')
@login_required
def pagina_auditoria():
    # ... (seu código existente de filtro e ordenação) ...

    # --- CORREÇÃO AQUI ---
    # Cria um dicionário mutável a partir dos argumentos da URL
    filtros_para_template = dict(request.args)
    # Remove 'page' para evitar conflito no url_for do template
    if 'page' in filtros_para_template:
        del filtros_para_template['page']

    # Recupera dados se for chamado diretamente (mantendo compatibilidade)
    dados_cache = cache_service.get()
    ensaios = dados_cache['dados'] if dados_cache else []
    materiais = dados_cache['materiais'] if dados_cache else []
    
    # Paginação simples para manter a rota funcionando
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

    key_original = str(texto_original).strip().upper()
    lote_clean = str(lote_correto).strip().upper()
    massa_clean = str(massa_correta).strip().upper()

    # --- NOVOS DADOS DE LOG ---
    user_log = current_user.username
    time_log = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        conn = get_local_db()
        cursor = conn.cursor()
        
        # INSERT OR REPLACE atualizado com as novas colunas
        cursor.execute("""
            INSERT OR REPLACE INTO aprendizado_local 
            (chave_original, lote_novo, massa_nova, usuario_log, data_log)
            VALUES (?, ?, ?, ?, ?)
        """, (key_original, lote_clean, massa_clean, user_log, time_log))
        
        conn.commit()
        conn.close()
        
        flash(f"✅ Regra salva! (Log: {user_log} às {time_log})", "success")
        
    except Exception as e:
        print(f"❌ Erro ao salvar no SQLite: {e}")
        flash("Erro ao salvar regra localmente.", "danger")

    return redirect(url_for('pagina_config', _anchor='ensinar'))


# ==========================================
# ROTAS NOVAS (RELATÓRIOS E GESTÃO)
# ==========================================

@app.route('/relatorios')
@login_required
def pagina_relatorios():
    dados_cache = cache_service.get()
    if not dados_cache: return redirect(url_for('dashboard'))
    
    # Captura filtros
    busca = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'nome')
    order = request.args.get('order', 'asc')

    relatorio_estruturado = gerar_estrutura_relatorio(
        dados_cache['dados'], busca=busca, ordenar_por=sort_by, ordem=order
    )
    
    context = {
        'relatorio': relatorio_estruturado,
        'ultimo_update': dados_cache['ultimo_update'],
        'total_massas': len(relatorio_estruturado),
        'search_term': busca, 'sort_by': sort_by, 'order': order
    }

    if request.headers.get('HX-Request'):
        return render_template('tabela_relatorios.html', **context)
    
    return render_template('relatorios.html', **context)

# Rota da Lista de Lotes (Agora com Filtros e Ordenação)
@app.route('/relatorios/detalhes/<int:cod_sankhya>')
@login_required
def detalhes_lotes_massa(cod_sankhya):
    dados_cache = cache_service.get()
    if not dados_cache: return redirect(url_for('dashboard'))
    
    # Filtra os dados brutos
    lista_filtrada = [e for e in dados_cache['dados'] if e.massa.cod_sankhya == cod_sankhya]
    
    # Gera a árvore (com os novos KPIs de lote)
    relatorio = gerar_estrutura_relatorio(lista_filtrada)
    if not relatorio: return "Material não encontrado ou sem dados."
    
    massa_node = relatorio[0]
    
    # --- Lógica de Ordenação e Filtro da Lista de Lotes ---
    lotes_lista = list(massa_node['lotes'].values())
    
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    search_lote = request.args.get('search', '').upper()
    
    # Filtro de Busca
    if search_lote:
        lotes_lista = [l for l in lotes_lista if search_lote in str(l['numero']).upper()]
    
    # Ordenação
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

    # Se for HTMX, retorna só o tbody
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
    
    if not ensaios_do_lote: return "Lote não encontrado."

    # 2. Lógica de Ordenação da Tabela
    sort_by = request.args.get('sort', 'batch') # Padrão: Batch
    order = request.args.get('order', 'asc')    # Padrão: Crescente
    reverse = (order == 'desc')

    def safe_sort_key(obj, attr, default=0):
        val = getattr(obj, attr, default)
        return val if val is not None else default

    if sort_by == 'id':
        ensaios_do_lote.sort(key=lambda x: x.id_ensaio, reverse=reverse)
    elif sort_by == 'hora':
        ensaios_do_lote.sort(key=lambda x: x.data_hora, reverse=reverse)
    elif sort_by == 'batch':
        # Tenta converter para int para ordenar corretamente (1, 2, 10 e não 1, 10, 2)
        def batch_key(x):
            try: return int(x.batch)
            except: return 0
        ensaios_do_lote.sort(key=batch_key, reverse=reverse)
    elif sort_by == 'temp':
        ensaios_do_lote.sort(key=lambda x: x.temp_plato, reverse=reverse)
    elif sort_by == 'score':
        ensaios_do_lote.sort(key=lambda x: x.score_final, reverse=reverse)

    # 3. Gera Árvore para KPIs (Score Geral, Aprovação)
    arvore = gerar_estrutura_relatorio(ensaios_do_lote)
    dados_lote = arvore[0]['lotes'][numero_lote]
    dados_massa = arvore[0]

    # 4. Cálculo Robusto de Médias (Corrigindo Viscosidade Zerada)
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
        
        # Correção aqui: Só adiciona se for maior que 0
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

    return render_template(
        'detalhe_lote.html', 
        lote=dados_lote, 
        massa=dados_massa,
        ensaios=ensaios_do_lote,
        # Passamos os params para manter a ordenação nos links
        sort_by=sort_by,
        order=order
    )


if __name__ == '__main__':
    app.run(debug=True)