from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models.usuario import db, Usuario
from cache_manager import CacheManager

from datetime import datetime
import math
import os

# Configurações e Modelos
from config import Config
from services.config_manager import salvar_configuracao
from services.config_manager import carregar_regras_acao, salvar_regras_acao

# --- IMPORTAÇÃO: SERVIÇO DE ETL ---
from services.etl_service import (
    processar_carga_dados, 
    carregar_referencias_estaticas,
    get_catalogo_codigo,
    _MAPA_GRUPOS  # <--- ADICIONE ESTA IMPORTAÇÃO
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

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# ==========================================
# 1. INICIALIZAÇÃO E CACHE
# ==========================================
print("\n=== REOSCORE V13 (MODULARIZED) ===")

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

# ==========================================
# 3. ROTAS PRINCIPAIS (DASHBOARD)
# ==========================================

@app.route('/atualizar_dados')
@login_required
def rota_atualizar():
    try:
        # --- PASSO 1: TENTATIVA DE DOWNLOAD VIA SHAREPOINT ---
        if baixar_excel_sharepoint:
            print("--- ☁️ Iniciando Sync com SharePoint ---")
            caminho_baixado = preparar_planilha_sharepoint(forcar_download=True)
            
            if caminho_baixado:
                flash("✅ Planilha baixada do SharePoint com sucesso!", "success")
            else:
                flash("⚠️ Falha no download do SharePoint (verifique logs). Usando cache anterior.", "warning")
        else:
            print("ℹ️ SharePoint Loader não disponível. Pulando download.")
        # -----------------------------------------------------

        # --- PASSO 2: EXECUÇÃO DO ETL (BANCO + PLANILHA) ---
        resultado = processar_carga_dados()
        
        if resultado:
            # Atualiza o cache na memória
            cache_service.set(resultado)
            
            # Pega estatísticas para feedback
            stats = cache_service.get_stats()
            flash(f"Dados processados! {stats['registros']} registros carregados. ({stats['tamanho_mb']} MB)", "info")
        else:
            flash("Erro ao processar carga de dados (ETL retornou vazio).", "danger")
            
    except Exception as e:
        print(f"❌ Erro Crítico na Rota Atualizar: {e}")
        flash(f"Erro crítico: {str(e)}", "danger")

    return redirect(url_for('dashboard'))

@app.route('/')
@login_required
def dashboard():
    # Tenta pegar dados do cache
    dados_cache = cache_service.get()

    # Se cache vazio ou expirado, força carga
    if dados_cache is None:
        print("--- Cache expirado ou vazio. Iniciando carga... ---")
        # Nota: Na carga automática ao abrir, não forçamos o download do SharePoint para ser mais rápido.
        # O download ocorre apenas no botão "Atualizar Dados".
        resultado = processar_carga_dados()
        if resultado:
            cache_service.set(resultado)
            dados_cache = resultado 
        else:
            dados_cache = {'dados': [], 'materiais': [], 'ultimo_update': None} 

    # Trabalha com a lista vinda do cache seguro
    ensaios_filtrados = list(dados_cache['dados'])
    total_geral = len(ensaios_filtrados)
    
    # --- FILTROS DE VIEW ---
    search = request.args.get('search', '').strip().upper()
    f_mat = request.args.get('material_filter', '')
    f_cod = request.args.get('codigo_filter', '').strip()
    f_acao = request.args.get('acao_filter', '')
    f_tipo = request.args.get('tipo_ensaio', '')
    d_start = request.args.get('date_start', '')
    d_end = request.args.get('date_end', '')
    
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    page = request.args.get('page', 1, type=int)
    LIMIT = 20

    # Aplicação dos Filtros
    if search:
        ensaios_filtrados = [e for e in ensaios_filtrados if search in str(e.lote).upper() or search in str(e.batch) or search in str(e.massa.descricao).upper()]
    if f_mat:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.massa.descricao == f_mat]
    if f_cod:
        ensaios_filtrados = [e for e in ensaios_filtrados if str(e.massa.cod_sankhya) == f_cod]
    if f_acao:
        if f_acao == "APROVADOS": ensaios_filtrados = [e for e in ensaios_filtrados if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada]
        elif f_acao == "RESSALVA": ensaios_filtrados = [e for e in ensaios_filtrados if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada]
        elif f_acao == "REPROVADO": ensaios_filtrados = [e for e in ensaios_filtrados if "REPROVAR" in e.acao_recomendada]
    if f_tipo:
        alvo = f_tipo.upper()
        ensaios_filtrados = [e for e in ensaios_filtrados if getattr(e, 'tipo_ensaio', '').upper() == alvo]
    if d_start:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora >= datetime.strptime(d_start, '%Y-%m-%d')]
    if d_end:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora <= datetime.strptime(d_end, '%Y-%m-%d').replace(hour=23, minute=59, second=59)]

    # Cálculo de KPIs
    total_filtrado = len(ensaios_filtrados)
    kpi = {
        'total': total_filtrado,
        'aprovados': sum(1 for e in ensaios_filtrados if "PRIME" in e.acao_recomendada or "LIBERAR" == e.acao_recomendada),
        'ressalvas': sum(1 for e in ensaios_filtrados if "RESSALVA" in e.acao_recomendada or "CORTAR" in e.acao_recomendada),
        'reprovados': sum(1 for e in ensaios_filtrados if "REPROVAR" in e.acao_recomendada)
    }

    # Ordenação
    reverse = (order == 'desc')
    def safe_sort(v): return v if v is not None else -1
    
    key_funcs = {
        'id': lambda x: x.id_ensaio, 'data': lambda x: x.data_hora if x.data_hora else datetime.min,
        'lote': lambda x: x.lote, 'score': lambda x: x.score_final, 'material': lambda x: x.massa.descricao,
        'ts2': lambda x: safe_sort(x.valores_medidos.get('Ts2')),
        't90': lambda x: safe_sort(x.valores_medidos.get('T90')),
        'visc': lambda x: safe_sort(x.valores_medidos.get('Viscosidade')),
        'acao': lambda x: x.acao_recomendada, 'temp': lambda x: safe_sort(x.temp_plato)
    }
    if sort_by in key_funcs: ensaios_filtrados.sort(key=key_funcs[sort_by], reverse=reverse)

    # Paginação
    total_paginas = max(1, math.ceil(total_filtrado / LIMIT))
    page = min(max(1, page), total_paginas)
    ensaios_paginados = ensaios_filtrados[(page-1)*LIMIT : page*LIMIT]

    context = {
        'ensaios': ensaios_paginados, 'kpi': kpi,
        'total_registros_filtrados': total_filtrado, 'total_geral': total_geral,
        'pagina_atual': page, 'total_paginas': total_paginas,
        'materiais_filtro': dados_cache['materiais'],
        'search_term': search, 'material_filter': f_mat, 'codigo_filter': f_cod, 'acao_filter': f_acao, 'tipo_ensaio_filter': f_tipo,
        'date_start': d_start, 'date_end': d_end, 'sort_by': sort_by, 'order': order,
        'ultimo_update': dados_cache['ultimo_update']
    }
    
    if request.headers.get('HX-Request'): return render_template('tabela_dados.html', **context)
    return render_template('index.html', **context)

# ==========================================
# 4. ROTAS DE CONFIGURAÇÃO (ADMIN)
# ==========================================

@app.route('/config')
@login_required
def pagina_config():
    if current_user.role != 'admin':
        flash("Acesso negado.", "warning")
        return redirect(url_for('dashboard'))
    
    # 1. Carrega Regras para a segunda aba
    regras_acao = carregar_regras_acao()

    # 2. Lógica existente de filtros e paginação de Materiais
    query = request.args.get('q', '').strip().upper()
    filtro_tipo = request.args.get('tipo', '')
    filtro_status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)
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
    total_paginas = math.ceil(total_itens / LIMIT)
    page = max(1, min(page, total_paginas)) if total_paginas > 0 else 1
    
    start = (page - 1) * LIMIT
    end = start + LIMIT
    produtos_paginados = produtos_filtrados[start:end]

    return render_template(
        'config.html', 
        produtos=produtos_paginados,
        regras_acao=regras_acao, # <--- Enviando regras para o template
        query=query, filtro_tipo=filtro_tipo, filtro_status=filtro_status,
        pagina_atual=page, total_paginas=total_paginas, total_itens=total_itens,
        sort_by=sort_by, order=order
    )

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
    
    def f(val): return float(val.replace(',', '.')) if val and val.strip() else 0.0
    def i(val): return int(val) if val and val.strip() else 0
    
    specs = {}
    
    t_alta = request.form.get('alta_temp_padrao')
    t_baixa = request.form.get('baixa_temp_padrao')
    tempo_alta = request.form.get('alta_tempo_total')
    tempo_baixa = request.form.get('baixa_tempo_total')
    
    if t_alta and t_alta.strip(): specs['alta_temp_padrao'] = f(t_alta)
    if t_baixa and t_baixa.strip(): specs['baixa_temp_padrao'] = f(t_baixa)
    if tempo_alta and tempo_alta.strip(): specs['alta_tempo_total'] = f(tempo_alta)
    if tempo_baixa and tempo_baixa.strip(): specs['baixa_tempo_total'] = f(tempo_baixa)

    perfis = ['alta', 'baixa']
    params = ['Ts2', 'T90', 'Viscosidade']
    
    for perfil in perfis:
        for p in params:
            prefix = f"{perfil}_{p}"
            min_v = request.form.get(f"{prefix}_min")
            alvo_v = request.form.get(f"{prefix}_alvo")
            max_v = request.form.get(f"{prefix}_max")
            peso_v = request.form.get(f"{prefix}_peso")
            
            if min_v or alvo_v or max_v:
                specs[prefix] = {
                    "min": f(min_v), "alvo": f(alvo_v), "max": f(max_v), "peso": i(peso_v)
                }

    novos_nomes = request.form.getlist('din_nome[]')
    novos_pesos = request.form.getlist('din_peso[]')
    novos_mins = request.form.getlist('din_min[]')
    novos_alvos = request.form.getlist('din_alvo[]')
    novos_maxs = request.form.getlist('din_max[]')
    
    for idx, nome in enumerate(novos_nomes):
        if nome.strip():
            specs[nome] = {
                "min": f(novos_mins[idx]), "alvo": f(novos_alvos[idx]),
                "max": f(novos_maxs[idx]), "peso": i(novos_pesos[idx])
            }
            
    salvar_configuracao(cod, specs)
    carregar_referencias_estaticas()
    
    flash(f"Configuração do produto {cod} salva e aplicada!", "success")
    return redirect(url_for('pagina_config', q=cod))

@app.route('/api/grafico')
@login_required
def api_grafico():
    dados_cache = cache_service.get()
    ids_str = request.args.get('ids', '')
    if not ids_str: return jsonify({})

    # 1. IDs das LINHAS selecionadas (Checkboxes)
    selected_parent_ids = [int(x) for x in ids_str.split(',') if x.isdigit()]
    
    if len(selected_parent_ids) > 10:
        return jsonify({'error': 'Selecione no máximo 10 linhas.'}), 400

    all_ids_to_fetch = []
    map_id_to_parent = {} 

    # 2. Apenas os IDs explicitamente selecionados (por linha)
    for cached in dados_cache['dados']:
        if cached.id_ensaio in selected_parent_ids:
            all_ids_to_fetch.append(cached.id_ensaio)
            map_id_to_parent[cached.id_ensaio] = cached

    if not all_ids_to_fetch:
        return jsonify({'error': 'Nenhum dado encontrado.'}), 404

    # 3. Busca Dados Brutos + COD_GRUPO para classificação precisa
    conn = connect_to_database()
    cursor = conn.cursor()
    
    placeholders = ','.join('?' * len(all_ids_to_fetch))
    
    # [Diagrama do Processo de Classificação]
    #     
    # ADICIONAMOS 'E.COD_GRUPO' NA QUERY PARA SABER A MÁQUINA EXATA
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
    
    try:
        cursor.execute(query, all_ids_to_fetch)
        rows = cursor.fetchall()
    finally:
        conn.close()

    # 4. Processamento e Separação Rigorosa
    datasets_reo = {}
    datasets_visc = {}
    materiais = set()
    
    for row in rows:
        c_id, c_time, c_val, c_temp, c_grupo = row
        
        parent = map_id_to_parent.get(c_id)
        if not parent: continue
        
        materiais.add(parent.massa.descricao)
        
        # --- AVALIAÇÃO INDIVIDUAL (Reometria vs Viscosidade) ---
        # Prioridade 1: O que diz o mapa de equipamentos? (Mais seguro)
        dados_grupo = _MAPA_GRUPOS.get(c_grupo, {})
        tipo_maquina = dados_grupo.get('tipo', 'INDEFINIDO')
        
        is_viscosity = False
        
        if tipo_maquina == 'VISCOSIMETRO':
            is_viscosity = True
        elif tipo_maquina == 'REOMETRO':
            is_viscosity = False
        else:
            # Prioridade 2: Fallback pela Temperatura (se a máquina não estiver mapeada)
            temp = float(c_temp) if c_temp else 0
            if 90 <= temp <= 115:
                is_viscosity = True
            elif temp >= 120:
                is_viscosity = False
            else:
                # Prioridade 3: Fallback pelo pai (último caso)
                pai_tipo = getattr(parent, 'tipo_ensaio', '').upper()
                is_viscosity = (pai_tipo == 'VISCOSIDADE')

        # Seleciona o dicionário correto
        target_dict = datasets_visc if is_viscosity else datasets_reo
        
        if c_id not in target_dict:
            # Label ex: "LOTE 123 (ID: 456)"
            label = f"{parent.lote} (ID: {c_id})"
            
            target_dict[c_id] = {
                'label': label,
                'material': parent.massa.descricao,
                'data': [],
                'borderColor': '', 
                'fill': False,
                'pointRadius': 0,
                'borderWidth': 2,
                'tension': 0.4,
                'dataset_temp': float(c_temp) if c_temp else 0
            }
        
        target_dict[c_id]['data'].append({'x': float(c_time), 'y': float(c_val)})

    return jsonify({
        'reometria': list(datasets_reo.values()),
        'viscosidade': list(datasets_visc.values()),
        'materiais': list(materiais)
    })



if __name__ == '__main__':
    app.run(debug=True)
