from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models.usuario import db, Usuario
from cache_manager import CacheManager

from datetime import datetime
import math
import os
import statistics 

# Configurações e Modelos
from config import Config
from services.config_manager import carregar_regras_acao, salvar_regras_acao, salvar_configuracao
from services.learning_service import ensinar_lote
from services.report_service import gerar_estrutura_relatorio


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
    try:
        dados_cache = cache_service.get()
        if not dados_cache:
            return jsonify({'error': 'Cache vazio. Atualize os dados.'}), 400

        ids_str = request.args.get('ids', '')
        modo_lote = request.args.get('mode', '') == 'lote' 
        
        if not ids_str: return jsonify({})

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
                ids_filhos = getattr(cached, 'ids_agrupados', []) or [cached_id]
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
            if not parent: continue
            
            # Classificação
            dados_grupo = _MAPA_GRUPOS.get(c_grupo, {})
            tipo_maquina = dados_grupo.get('tipo', 'INDEFINIDO')
            
            is_viscosity = False
            
            if tipo_maquina == 'VISCOSIMETRO': is_viscosity = True
            elif tipo_maquina == 'REOMETRO': is_viscosity = False
            else:
                if 90 <= c_temp <= 115: is_viscosity = True
                elif c_temp >= 120: is_viscosity = False
                else: is_viscosity = (getattr(parent, 'tipo_ensaio', '').upper() == 'VISCOSIDADE')

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
                    'tempType': temp_type, # <--- Enviando para o Frontend
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
        print(f"ERRO API: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/auditoria')
@login_required
def pagina_auditoria():
    # 1. Recuperação Rápida do Cache (Sem ir ao Banco)
    dados_cache = cache_service.get()
    if not dados_cache:
        flash("Cache vazio. Atualize os dados primeiro.", "warning")
        return redirect(url_for('dashboard'))
    
    ensaios = dados_cache['dados']
    materiais = dados_cache['materiais']

    # --- FILTROS ---
    f_data = request.args.get('data', '')
    f_status = request.args.get('status', '')
    f_busca = request.args.get('busca', '').upper().strip()
    
    # Paginação
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Exibe 50 itens por vez (leve para o navegador)

    lista_exibicao = []
    
    # --- APLICAÇÃO DOS FILTROS ---
    # (Filtragem em memória é muito rápida, o gargalo é o HTML)
    for e in ensaios:
        # Filtro Data
        if f_data and e.data_hora.strftime('%Y-%m-%d') != f_data:
            continue
            
        # Filtro Status
        if f_status and e.metodo_identificacao != f_status:
            continue
        
        # Filtro Busca (Lote Limpo ou Original)
        # Verifica se o termo está no lote visualizado
        if f_busca and f_busca not in str(e.lote).upper():
            continue

        # Lógica Padrão: Se sem filtros, esconde os "LOTE" (Verdes) para focar no erro
        if not f_status and not f_busca and not f_data:
            if e.metodo_identificacao == 'LOTE': 
                continue

        lista_exibicao.append(e)

    # --- ORDENAÇÃO ---
    # Vermelho (0) -> Amarelo (1) -> Azul (2) -> Verde (3)
    peso = {'FANTASMA': 0, 'TEXTO': 1, 'MANUAL': 2, 'LOTE': 3}
    lista_exibicao.sort(key=lambda x: (peso.get(x.metodo_identificacao, 9), x.data_hora), reverse=False)

    # --- PAGINAÇÃO (O Corte) ---
    total_registros = len(lista_exibicao)
    total_paginas = math.ceil(total_registros / per_page)
    
    # Garante que a página esteja dentro dos limites
    page = max(1, min(page, total_paginas)) if total_paginas > 0 else 1
    
    start = (page - 1) * per_page
    end = start + per_page
    ensaios_paginados = lista_exibicao[start:end]

    return render_template(
        'auditoria.html', 
        ensaios=ensaios_paginados, 
        materiais=materiais,
        # Metadados da Paginação
        pagina_atual=page,
        total_paginas=total_paginas,
        total_registros=total_registros,
        # Filtros (para manter na navegação)
        request_args=request.args
    )

@app.route('/salvar_correcao', methods=['POST'])
@login_required
def salvar_correcao():
    # Campos vindos do template (auditoria.html)
    texto_original = (
        request.form.get('texto_original')
        or request.form.get('lote_original_key')
        or ''
    )
    lote_correto = (
        request.form.get('lote_correto')
        or request.form.get('novo_lote')
        or ''
    )
    massa_correta = (
        request.form.get('massa_correta')
        or request.form.get('massa')
        or ''
    )

    key_original = str(texto_original).strip().upper()
    lote_correto = str(lote_correto).strip().upper()
    massa_correta = str(massa_correta).strip().upper()

    if not key_original or not lote_correto or not massa_correta:
        flash("Preencha Lote original, Lote real e Massa para salvar a correção.", "warning")
        return redirect(url_for('pagina_auditoria'))

    # 1) Salva a regra de aprendizado (JSON)
    ok = ensinar_lote(key_original, lote_correto, massa_correta)
    if not ok:
        flash("Falha ao salvar a regra de aprendizado.", "danger")
        return redirect(url_for('pagina_auditoria'))

    # 2) Aplica retroativamente no banco (SQL Server)
    linhas_afetadas = 0
    try:
        conn = connect_to_database()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE dbo.ENSAIO
            SET NUMERO_LOTE = ?, AMOSTRA = ?
            WHERE UPPER(LTRIM(RTRIM(NUMERO_LOTE))) = ?
               OR UPPER(LTRIM(RTRIM(AMOSTRA))) = ?
            """,
            (lote_correto, massa_correta, key_original, key_original),
        )
        linhas_afetadas = cursor.rowcount or 0
        conn.commit()
    except Exception as e:
        print(f"❌ Erro ao aplicar correção no banco: {e}")
        flash("Correção salva, mas não foi possível atualizar o banco retroativamente.", "warning")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    # 3) Atualiza o cache atual (sem precisar recarregar tudo)
    try:
        dados_cache = cache_service.get() or {}
        ensaios = dados_cache.get('dados') or []
        materiais = dados_cache.get('materiais') or []

        massa_obj = next(
            (m for m in materiais if str(getattr(m, 'descricao', '')).strip().upper() == massa_correta),
            None,
        )

        atualizados_cache = 0
        for ensaio in ensaios:
            lote_orig = str(getattr(ensaio, 'lote_original', '')).strip().upper()
            mat_orig = str(getattr(ensaio, 'material_original', '')).strip().upper()

            if key_original in {lote_orig, mat_orig}:
                ensaio.lote = lote_correto
                if massa_obj:
                    ensaio.massa = massa_obj
                ensaio.metodo_identificacao = "MANUAL"
                try:
                    ensaio.calcular_score()
                except Exception:
                    pass
                atualizados_cache += 1

        if atualizados_cache:
            cache_service.set(dados_cache)
    except Exception as e:
        print(f"⚠️ Não foi possível atualizar o cache retroativamente: {e}")

    print(f"🔄 Retroativo: {linhas_afetadas} registros no banco foram atualizados.")
    flash(f"Correção salva. Retroativo no banco: {linhas_afetadas} registro(s).", "success")
    return redirect(url_for('pagina_auditoria'))


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
