from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models.usuario import db, Usuario

from datetime import datetime
import math, re, json, os
from difflib import get_close_matches
import pandas as pd 

# Modelos
from models.massa import Massa
from models.ensaio import Ensaio
from config import Config
# Serviços
from connection import connect_to_database      
from etl_planilha import carregar_dicionario_lotes 
from services.sankhya_service import importar_catalogo_sankhya 
from services.config_manager import aplicar_configuracoes_no_catalogo, salvar_configuracao

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = os.getenv("FLASK_SECRET_KEY") # Necessário para mensagens de feedback (flash)

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
# 1. INICIALIZAÇÃO
# ==========================================
print("\n=== REOSCORE V13 (MANUAL UPDATE + VISC LOGIC) ===")

CATALOGO_POR_CODIGO, CATALOGO_POR_NOME = importar_catalogo_sankhya()
aplicar_configuracoes_no_catalogo(CATALOGO_POR_CODIGO)
MAPA_LOTES_PLANILHA = carregar_dicionario_lotes()

MAPA_GRUPOS = {}
DE_PARA_CORRECOES = {}

if os.path.exists("mapa_tipo_equipamentos.xlsx"):
    try:
        df_g = pd.read_excel("mapa_tipo_equipamentos.xlsx")
        MAPA_GRUPOS = dict(zip(df_g['COD_GRUPO'], df_g['TIPO SUGERIDO']))
    except: pass

if os.path.exists("de_para_massas.json"):
    try:
        with open("de_para_massas.json", 'r', encoding='utf-8') as f:
            DE_PARA_CORRECOES = json.load(f)
    except: pass

# ==========================================
# 2. CACHE GLOBAL (SEM TIMER AUTOMÁTICO)
# ==========================================
CACHE_GLOBAL = {
    'dados': [],            
    'materiais': [],        
    'ultimo_update': None,
    'total_registros_brutos': 0
}

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================

def safe_float(val):
    """Converte para float se possível, senão retorna None (e não 0.0)"""
    if val is None: return None
    try:
        f = float(val)
        if math.isnan(f): return None
        if f == 0: return None # Trata 0 como ausência de dado também
        return f
    except: return None

def extrair_lote_da_string(texto_sujo):
    if not texto_sujo: return None, None
    texto = str(texto_sujo).strip().upper()
    
    if '*' in texto:
        partes = texto.split('*')
        if len(partes) >= 2:
            candidato = partes[1].strip().lstrip('0')
            if not candidato: candidato = '0'
            if candidato in MAPA_LOTES_PLANILHA: return candidato, "Asterisco"

    if texto in MAPA_LOTES_PLANILHA: return texto, "Exato"

    todos_numeros = re.findall(r'\d+', texto)
    for num in reversed(todos_numeros): 
        candidato = num.lstrip('0')
        if candidato in MAPA_LOTES_PLANILHA: return candidato, "Regex"
    return None, None

def match_nome_inteligente(texto_bruto):
    if not texto_bruto: return None
    texto = str(texto_bruto).strip().upper()
    
    if texto in DE_PARA_CORRECOES: texto = DE_PARA_CORRECOES[texto]

    if texto in CATALOGO_POR_NOME: return CATALOGO_POR_NOME[texto]
    if ("MASSA " + texto) in CATALOGO_POR_NOME: return CATALOGO_POR_NOME["MASSA " + texto]
    if texto.startswith("MASSA "):
        sem = texto.replace("MASSA ", "").strip()
        if sem in CATALOGO_POR_NOME: return CATALOGO_POR_NOME[sem]

    opcoes = list(CATALOGO_POR_NOME.keys())
    matches = get_close_matches(texto, opcoes, n=1, cutoff=0.7)
    
    if matches:
        melhor_match = matches[0]
        palavras_orig = set(texto.split())
        palavras_match = set(melhor_match.split())
        diferenca = palavras_match - palavras_orig
        palavras_risco = {'ORB', 'AQ', 'PRETO', 'BRANCO', 'STD', 'ESPECIAL'}
        if not diferenca.intersection(palavras_risco):
            return CATALOGO_POR_NOME[melhor_match]
    return None

# ==========================================
# 4. ATUALIZAÇÃO DO CACHE (MANUAL)
# ==========================================
def atualizar_cache_do_banco():
    print("--- 🔄 ATUALIZANDO CACHE (SQL + ETL) ---")
    start_time = datetime.now()
    
    resultados_brutos = []
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
        WHERE DATA >= '2025-07-01'
        ORDER BY DATA DESC
        '''
        cursor.execute(query)
        colunas = [c[0] for c in cursor.description]
        resultados_brutos = [dict(zip(colunas, row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"❌ Erro SQL: {e}")
        return False
    finally:
        if conn: conn.close()

    print(f"   > Processando {len(resultados_brutos)} linhas...")
    
    dados_agrupados = {} 
    
    # 1. Agrupamento
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
        
        produto = None
        tipo_equip = MAPA_GRUPOS.get(grupo, "INDEFINIDO")
        usar_cod = (tipo_equip != "VISCOSIMETRO")
        
        if lote_final:
            nome = MAPA_LOTES_PLANILHA.get(lote_final)
            if nome: produto = match_nome_inteligente(nome)
        
        if not produto:
            produto = match_nome_inteligente(amostra)
            if not produto and usar_cod: produto = match_nome_inteligente(row['CODIGO_REO'])
                
        if chave_unica not in dados_agrupados:
            dados_agrupados[chave_unica] = {
                'ids_ensaio': [], 'massa': produto, 'lote_visivel': chave_lote, 'batch': chave_batch,
                'data': row['DATA'], 'ts2': None, 't90': None, 'visc': None, 'temps': [], 'grupos': set()
            }
        
        reg = dados_agrupados[chave_unica]
        reg['ids_ensaio'].append(row['COD_ENSAIO'])
        reg['grupos'].add(grupo)
        if not reg['massa'] and produto: reg['massa'] = produto
            
        v_ts2 = safe_float(row['Ts2'])
        v_t90 = safe_float(row['T90'])
        v_visc = safe_float(row['Viscosidade'])
        v_temp = safe_float(row['TEMP_PLATO_INF'])
        
        if v_ts2: reg['ts2'] = v_ts2
        if v_t90: reg['t90'] = v_t90
        if v_visc: reg['visc'] = v_visc
        if v_temp and v_temp not in reg['temps']: reg['temps'].append(v_temp)

    # 2. Cálculo de Médias (Apenas onde existe dado real)
    medias_visc_por_lote = {}
    acumulador_lote = {}
    
    for dados in dados_agrupados.values():
        l = dados['lote_visivel']
        v = dados['visc']
        if v:
            if l not in acumulador_lote: acumulador_lote[l] = []
            acumulador_lote[l].append(v)
            
    for l, valores in acumulador_lote.items():
        medias_visc_por_lote[l] = sum(valores) / len(valores)

    # 3. Criação Final
    lista_final = []
    materiais_set = set()
    
    for _, dados in dados_agrupados.items():
        if not dados['massa']: continue
        
        materiais_set.add(dados['massa'])
        
        # Lógica Fina da Viscosidade
        valor_visc = dados['visc']
        origem_visc = "N/A"
        
        if valor_visc:
            origem_visc = "Real"
        elif dados['lote_visivel'] in medias_visc_por_lote:
            # Só aplica média se o lote tiver histórico
            valor_visc = medias_visc_por_lote[dados['lote_visivel']]
            origem_visc = "Média (Lote)"
        else:
            # Mantém None para indicar ausência real
            valor_visc = None
        
        # Monta dicionário de medidas (Só o que existe)
        medidas = {}
        if dados['ts2']: medidas['Ts2'] = dados['ts2']
        if dados['t90']: medidas['T90'] = dados['t90']
        if valor_visc: medidas['Viscosidade'] = valor_visc

        novo_ensaio = Ensaio(
            id_ensaio=dados['ids_ensaio'][0],
            massa_objeto=dados['massa'],
            valores_medidos=medidas,
            lote=dados['lote_visivel'],
            batch=dados['batch'],
            data_hora=dados['data'],
            origem_viscosidade=origem_visc,
            temp_plato=dados['temps'][0] if dados['temps'] else 0,
            temps_plato=list(dados['temps']),
            cod_grupo=list(dados['grupos'])[0],
            tempo_maximo=0,
            ids_agrupados=list(dados['ids_ensaio'])
        )
        novo_ensaio.calcular_score()
        lista_final.append(novo_ensaio)
    
    CACHE_GLOBAL['dados'] = lista_final
    CACHE_GLOBAL['materiais'] = sorted(list(materiais_set), key=lambda m: m.descricao)
    CACHE_GLOBAL['ultimo_update'] = datetime.now()
    CACHE_GLOBAL['total_registros_brutos'] = len(resultados_brutos)
    
    print(f"✅ CACHE ATUALIZADO: {len(lista_final)} ensaios em {(datetime.now() - start_time).total_seconds():.1f}s.")
    return True

# ==========================================
# 5. ROTAS
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

# Rota auxiliar para criar o primeiro ADMIN (execute uma vez e apague ou proteja)
@app.route('/criar_admin')
def criar_admin():
    if Usuario.query.filter_by(username='admin').first():
        return "Admin já existe."
    
    hashed_pw = bcrypt.generate_password_hash('senha123').decode('utf-8')
    novo_admin = Usuario(username='admin', password_hash=hashed_pw, role='admin')
    db.session.add(novo_admin)
    db.session.commit()
    return "Admin criado com sucesso! (User: admin / Pass: senha123)"


@app.route('/atualizar_dados')
def rota_atualizar():
    """Rota disparada pelo botão manual"""
    sucesso = atualizar_cache_do_banco()
    if sucesso:
        flash(f"Dados atualizados com sucesso! {len(CACHE_GLOBAL['dados'])} registros carregados.", "success")
    else:
        flash("Erro ao atualizar dados. Verifique a conexão.", "danger")
    return redirect(url_for('dashboard'))

@app.route('/')
@login_required
def dashboard():
    # Se cache vazio, força primeira carga
    if CACHE_GLOBAL['ultimo_update'] is None:
        atualizar_cache_do_banco()
    
    ensaios_filtrados = list(CACHE_GLOBAL['dados'])
    total_geral = len(ensaios_filtrados)
    
    # Filtros
    search = request.args.get('search', '').strip().upper()
    f_mat = request.args.get('material_filter', '')
    f_cod = request.args.get('codigo_filter', '').strip()
    f_acao = request.args.get('acao_filter', '')
    d_start = request.args.get('date_start', '')
    d_end = request.args.get('date_end', '')
    
    sort_by = request.args.get('sort', 'data')
    order = request.args.get('order', 'desc')
    page = request.args.get('page', 1, type=int)
    LIMIT = 20

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
    if d_start:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora >= datetime.strptime(d_start, '%Y-%m-%d')]
    if d_end:
        ensaios_filtrados = [e for e in ensaios_filtrados if e.data_hora <= datetime.strptime(d_end, '%Y-%m-%d').replace(hour=23, minute=59, second=59)]

    # KPI
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
        'search_term': search, 'material_filter': f_mat, 'codigo_filter': f_cod, 'acao_filter': f_acao,
        'date_start': d_start, 'date_end': d_end, 'sort_by': sort_by, 'order': order,
        'ultimo_update': CACHE_GLOBAL['ultimo_update']
    }
    
    if request.headers.get('HX-Request'): return render_template('tabela_dados.html', **context)
    return render_template('index.html', **context)

# ... (Rotas Config mantidas iguais) ...
@app.route('/config')
@login_required
def pagina_config():
    if current_user.role != 'admin':
        flash("Acesso negado. Apenas administradores podem alterar configurações.", "warning")
        return redirect(url_for('dashboard'))
    query = request.args.get('q', '').upper()
    produtos = []
    if not query: produtos = list(CATALOGO_POR_CODIGO.values())[:50]
    else:
        for p in CATALOGO_POR_CODIGO.values():
            if query in str(p.cod_sankhya) or query in p.descricao.upper():
                produtos.append(p)
                if len(produtos) > 50: break
    return render_template('config.html', produtos=produtos, query=query)

@app.route('/salvar_config', methods=['POST'])
def salvar_config():
    cod = request.form.get('cod_sankhya')
    def f(val): return float(val.replace(',', '.')) if val else 0.0
    def i(val): return int(val) if val else 0
    specs = {"temp_padrao": f(request.form.get('temp_padrao'))}
    for param in ["Ts2", "T90", "Viscosidade"]:
        min_v, alvo_v, max_v, peso_v = request.form.get(f"{param}_min"), request.form.get(f"{param}_alvo"), request.form.get(f"{param}_max"), request.form.get(f"{param}_peso")
        if min_v or alvo_v or max_v: specs[param] = {"min": f(min_v), "alvo": f(alvo_v), "max": f(max_v), "peso": i(peso_v)}
    novos_nomes = request.form.getlist('din_nome[]')
    novos_pesos = request.form.getlist('din_peso[]')
    novos_mins = request.form.getlist('din_min[]')
    novos_alvos = request.form.getlist('din_alvo[]')
    novos_maxs = request.form.getlist('din_max[]')
    for idx, nome in enumerate(novos_nomes):
        if nome.strip(): specs[nome] = {"min": f(novos_mins[idx]), "alvo": f(novos_alvos[idx]), "max": f(novos_maxs[idx]), "peso": i(novos_pesos[idx])}
    salvar_configuracao(cod, specs)
    aplicar_configuracoes_no_catalogo(CATALOGO_POR_CODIGO)
    # Não limpa cache, usuário atualiza manual
    return redirect(url_for('pagina_config', q=cod))

if __name__ == '__main__':
    app.run(debug=True)
