from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models.usuario import db, Usuario

from datetime import datetime
import math
import os

# Configurações e Modelos
from config import Config
from services.config_manager import salvar_configuracao

# --- NOVA IMPORTAÇÃO: SERVIÇO DE ETL ---
# Importamos a variável _CATALOGO_CODIGO para que a tela de Config 
# acesse os mesmos objetos que o ETL usa.
from services.etl_service import (
    processar_carga_dados, 
    carregar_referencias_estaticas,
    get_catalogo_codigo  # <--- Nova função importada
)

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

# Inicializa as referências estáticas (Catálogos, Lotes, Configs)
carregar_referencias_estaticas()

# Cache em memória para exibição rápida
CACHE_GLOBAL = {
    'dados': [],            
    'materiais': [],        
    'ultimo_update': None,
    'total_registros_brutos': 0
}

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
    """Rota disparada pelo botão manual para forçar o ETL."""
    try:
        resultado = processar_carga_dados()
        
        if resultado:
            CACHE_GLOBAL.update(resultado)
            flash(f"Dados atualizados com sucesso! {len(CACHE_GLOBAL['dados'])} registros carregados.", "success")
        else:
            flash("Erro ao atualizar dados. Verifique a conexão com o Banco de Dados.", "danger")
            
    except Exception as e:
        flash(f"Erro crítico durante atualização: {str(e)}", "danger")

    return redirect(url_for('dashboard'))

@app.route('/')
@login_required
def dashboard():
    # Se cache vazio, força primeira carga
    if CACHE_GLOBAL['ultimo_update'] is None:
        print("--- Cache vazio. Iniciando carga automática... ---")
        resultado = processar_carga_dados()
        if resultado:
            CACHE_GLOBAL.update(resultado)
    
    # Trabalha com cópia da lista para filtragem
    ensaios_filtrados = list(CACHE_GLOBAL['dados'])
    total_geral = len(ensaios_filtrados)
    
    # --- FILTROS DE VIEW (Controller) ---
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
        'materiais_filtro': CACHE_GLOBAL['materiais'],
        'search_term': search, 'material_filter': f_mat, 'codigo_filter': f_cod, 'acao_filter': f_acao, 'tipo_ensaio_filter': f_tipo,
        'date_start': d_start, 'date_end': d_end, 'sort_by': sort_by, 'order': order,
        'ultimo_update': CACHE_GLOBAL['ultimo_update']
    }
    
    if request.headers.get('HX-Request'): return render_template('tabela_dados.html', **context)
    return render_template('index.html', **context)

# ==========================================
# 4. ROTAS DE CONFIGURAÇÃO (ADMIN)
# ==========================================

# No arquivo app.py

@app.route('/config')
@login_required
def pagina_config():
    if current_user.role != 'admin':
        flash("Acesso negado. Apenas administradores podem alterar configurações.", "warning")
        return redirect(url_for('dashboard'))
    
    # 1. Parâmetros da URL (Filtros, Paginação e Ordenação)
    query = request.args.get('q', '').strip().upper()
    filtro_tipo = request.args.get('tipo', '')
    filtro_status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'descricao') # Padrão: por descrição
    order = request.args.get('order', 'asc')        # Padrão: A-Z
    LIMIT = 20

    # 2. Obtém catálogo
    CATALOGO_ATUAL = get_catalogo_codigo()
    produtos_filtrados = []

    # 3. Filtragem
    for p in CATALOGO_ATUAL.values():
        # Filtro Texto
        if query and (query not in str(p.cod_sankhya) and query not in p.descricao.upper()):
            continue
        # Filtro Tipo
        if filtro_tipo and p.tipo != filtro_tipo:
            continue
        # Filtro Status
        if filtro_status:
            tem_conteudo = (
                (p.perfis and (p.perfis.get('alta') or p.perfis.get('baixa'))) or 
                (p.parametros and len(p.parametros) > 0)
            )
            if filtro_status == 'OK' and not tem_conteudo: continue
            if filtro_status == 'PENDENTE' and tem_conteudo: continue

        produtos_filtrados.append(p)
    
    # 4. Ordenação Dinâmica
    reverse = (order == 'desc')
    
    if sort_by == 'cod':
        produtos_filtrados.sort(key=lambda x: x.cod_sankhya, reverse=reverse)
    elif sort_by == 'status':
        # Ordena quem tem config primeiro ou por último
        def get_status_sort(p):
            return (
                (p.perfis and (p.perfis.get('alta') or p.perfis.get('baixa'))) or 
                (p.parametros and len(p.parametros) > 0)
            )
        produtos_filtrados.sort(key=get_status_sort, reverse=reverse)
    else:
        # Padrão: Descrição
        produtos_filtrados.sort(key=lambda x: x.descricao, reverse=reverse)

    # 5. Paginação
    total_itens = len(produtos_filtrados)
    total_paginas = math.ceil(total_itens / LIMIT)
    page = max(1, min(page, total_paginas)) if total_paginas > 0 else 1
    
    start = (page - 1) * LIMIT
    end = start + LIMIT
    produtos_paginados = produtos_filtrados[start:end]

    return render_template(
        'config.html', 
        produtos=produtos_paginados,
        query=query,
        filtro_tipo=filtro_tipo,
        filtro_status=filtro_status,
        pagina_atual=page,
        total_paginas=total_paginas,
        total_itens=total_itens,
        sort_by=sort_by,
        order=order
    )

@app.route('/salvar_config', methods=['POST'])
@login_required
def salvar_config():
    if current_user.role != 'admin':
        return redirect(url_for('dashboard'))

    cod = request.form.get('cod_sankhya')
    
    # Funções auxiliares de conversão
    def f(val): return float(val.replace(',', '.')) if val and val.strip() else 0.0
    def i(val): return int(val) if val and val.strip() else 0
    
    specs = {}
    
    # 1. Captura as Temperaturas Padrão e tempo alvo
    t_alta = request.form.get('alta_temp_padrao')
    t_baixa = request.form.get('baixa_temp_padrao')
    tempo_alta = request.form.get('alta_tempo_total')
    tempo_baixa = request.form.get('baixa_tempo_total')
    
    if t_alta and t_alta.strip(): specs['alta_temp_padrao'] = f(t_alta)
    if t_baixa and t_baixa.strip(): specs['baixa_temp_padrao'] = f(t_baixa)
    if tempo_alta and tempo_alta.strip(): specs['alta_tempo_total'] = f(tempo_alta)
    if tempo_baixa and tempo_baixa.strip(): specs['baixa_tempo_total'] = f(tempo_baixa)

    # 2. Captura os Parâmetros Fixos
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

    # 3. Mantém compatibilidade Legacy
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
            
    # Salva no arquivo JSON
    salvar_configuracao(cod, specs)
    
    # Recarrega as configurações na memória do ETL para aplicar imediatamente
    carregar_referencias_estaticas()
    
    flash(f"Configuração do produto {cod} salva e aplicada!", "success")
    return redirect(url_for('pagina_config', q=cod))

@app.route('/api/grafico')
@login_required
def api_grafico():
    ids_str = request.args.get('ids', '')
    if not ids_str: return jsonify({})

    selected_ids = [int(x) for x in ids_str.split(',') if x.isdigit()]
    if len(selected_ids) > 10:
        return jsonify({'error': 'Max 10 items.'}), 400

    # 1. Expand IDs (Get Group Partners)
    all_ids_to_fetch = []
    map_id_to_parent = {} 

    for cached in CACHE_GLOBAL['dados']:
        if cached.id_ensaio in selected_ids:
            for sub_id in cached.ids_agrupados:
                all_ids_to_fetch.append(sub_id)
                map_id_to_parent[sub_id] = cached

    if not all_ids_to_fetch:
        return jsonify({'error': 'Not found.'}), 404

    # 2. Fetch Data + Temperature (JOIN)
    conn = connect_to_database()
    cursor = conn.cursor()
    
    placeholders = ','.join('?' * len(all_ids_to_fetch))
    
    # We join with ENSAIO to get the temperature of that specific test run
    query = f'''
        SELECT V.COD_ENSAIO, V.TEMPO, V.TORQUE, E.TEMP_PLATO_INF
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

    # 3. Structure and Separate
    datasets_reo = {}
    datasets_visc = {}
    materiais = set()
    
    for row in rows:
        c_id, c_time, c_val, c_temp = row
        
        parent = map_id_to_parent.get(c_id)
        if not parent: continue
        
        materiais.add(parent.massa.descricao)
        
        # --- CLASSIFICATION BY TEMPERATURE ---
        # Strictly apply the rule: 90-115C = Viscosity
        temp = float(c_temp) if c_temp else 0
        is_viscosity = (90 <= temp <= 115)
        
        # Additional check: If the parent is explicitly VISCOSITY type, trust it
        if getattr(parent, 'tipo_ensaio', '') == 'VISCOSIDADE':
            is_viscosity = True

        target_dict = datasets_visc if is_viscosity else datasets_reo
        
        if c_id not in target_dict:
            # Smart Label: "LOTE (ID)"
            label = f"{parent.lote} (ID: {c_id})"
            
            target_dict[c_id] = {
                'label': label,
                'material': parent.massa.descricao,
                'data': [],
                'borderColor': '', 
                'fill': False,
                'pointRadius': 0,
                'borderWidth': 2,
                'tension': 0.4
            }
        
        target_dict[c_id]['data'].append({'x': float(c_time), 'y': float(c_val)})

    return jsonify({
        'reometria': list(datasets_reo.values()),
        'viscosidade': list(datasets_visc.values()),
        'materiais': list(materiais)
    })


if __name__ == '__main__':
    app.run(debug=True)