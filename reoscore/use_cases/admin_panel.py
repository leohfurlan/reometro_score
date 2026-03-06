import math
from datetime import datetime

from models.usuario import Usuario, db
from reoscore.use_cases.learning_corrections import save_manual_learning_correction
from reoscore.use_cases.score_admin import save_action_rules_from_form, save_material_specs_from_form
from services.config_manager import carregar_configuracoes, carregar_regras_acao
from services.etl_service import get_catalogo_codigo


def _normalizar_lista_materiais(itens):
    nomes = []
    vistos = set()

    for item in itens or []:
        nome = None
        if isinstance(item, str):
            nome = item
        elif isinstance(item, dict):
            nome = item.get("descricao") or item.get("massa_descricao") or item.get("nome")
        else:
            nome = (
                getattr(item, "descricao", None)
                or getattr(item, "massa_descricao", None)
                or getattr(item, "nome", None)
            )

        nome = str(nome or "").strip()
        if not nome:
            continue

        chave = nome.upper()
        if chave in vistos:
            continue

        vistos.add(chave)
        nomes.append(nome)

    nomes.sort()
    return nomes


def build_config_page_context(args, cache_service, current_user):
    regras_acao = carregar_regras_acao()
    configs_massas = carregar_configuracoes()

    query = args.get("q", "").strip().upper()
    filtro_tipo = args.get("tipo", "")
    filtro_status = args.get("status", "")
    page_mat = int(args.get("page_mat", 1) or 1)
    sort_by = args.get("sort", "descricao")
    order = args.get("order", "asc")
    limit = 20

    catalogo_atual = get_catalogo_codigo()
    produtos_filtrados = []

    for produto in catalogo_atual.values():
        if query and (query not in str(produto.cod_sankhya) and query not in produto.descricao.upper()):
            continue
        if filtro_tipo and produto.tipo != filtro_tipo:
            continue
        if filtro_status:
            tem_conteudo = (
                (produto.perfis and (produto.perfis.get("alta") or produto.perfis.get("baixa")))
                or (produto.parametros and len(produto.parametros) > 0)
            )
            if filtro_status == "OK" and not tem_conteudo:
                continue
            if filtro_status == "PENDENTE" and tem_conteudo:
                continue
        produtos_filtrados.append(produto)

    reverse = order == "desc"
    if sort_by == "cod":
        produtos_filtrados.sort(key=lambda item: item.cod_sankhya, reverse=reverse)
    elif sort_by == "status":
        def _status_key(produto):
            return (produto.perfis and (produto.perfis.get("alta") or produto.perfis.get("baixa"))) or (
                produto.parametros and len(produto.parametros) > 0
            )

        produtos_filtrados.sort(key=_status_key, reverse=reverse)
    else:
        produtos_filtrados.sort(key=lambda item: item.descricao, reverse=reverse)

    total_itens = len(produtos_filtrados)
    total_paginas_mat = math.ceil(total_itens / limit) if total_itens else 0
    page_mat = max(1, min(page_mat, total_paginas_mat)) if total_paginas_mat > 0 else 1
    start = (page_mat - 1) * limit
    end = start + limit
    produtos_paginados = produtos_filtrados[start:end]

    dados_cache = cache_service.get()
    ensaios_audit = []
    materiais_audit = []
    if dados_cache:
        ensaios_raw = dados_cache["dados"]
        materiais_audit = _normalizar_lista_materiais(dados_cache.get("materiais"))

        f_data = args.get("audit_data", "")
        f_status = args.get("audit_status", "")
        f_busca = args.get("audit_busca", "").upper().strip()
        page_audit = int(args.get("page_audit", 1) or 1)
        per_page_audit = 50

        for ensaio in ensaios_raw:
            if f_data and ensaio.data_hora.strftime("%Y-%m-%d") != f_data:
                continue
            if f_status and f_status != "ALL" and ensaio.metodo_identificacao != f_status:
                continue
            if f_busca and f_busca not in str(ensaio.lote).upper():
                continue
            if not f_status and not f_busca and not f_data and ensaio.metodo_identificacao == "LOTE":
                continue
            ensaios_audit.append(ensaio)

        peso = {"FANTASMA": 100, "TEXTO": 90, "MANUAL": 10, "LOTE": 0}

        def _data_segura(item):
            return item.data_hora if item.data_hora else datetime.min

        ensaios_audit.sort(key=lambda item: (peso.get(item.metodo_identificacao, 0), _data_segura(item)), reverse=True)
        total_registros_audit = len(ensaios_audit)
        total_paginas_audit = math.ceil(total_registros_audit / per_page_audit) if total_registros_audit else 0
        page_audit = max(1, min(page_audit, total_paginas_audit)) if total_paginas_audit > 0 else 1
        start_a = (page_audit - 1) * per_page_audit
        end_a = start_a + per_page_audit
        ensaios_audit_paginados = ensaios_audit[start_a:end_a]
    else:
        ensaios_audit_paginados = []
        total_paginas_audit = 1
        total_registros_audit = 0
        page_audit = 1

    usuarios = Usuario.query.all() if current_user.role == "admin" else []

    return {
        "produtos": produtos_paginados,
        "configs_massas": configs_massas,
        "regras_acao": regras_acao,
        "query": query,
        "filtro_tipo": filtro_tipo,
        "filtro_status": filtro_status,
        "pagina_atual_mat": page_mat,
        "total_paginas_mat": total_paginas_mat,
        "total_itens_mat": total_itens,
        "sort_by": sort_by,
        "order": order,
        "ensaios_audit": ensaios_audit_paginados,
        "materiais_audit": materiais_audit,
        "pagina_atual_audit": page_audit,
        "total_paginas_audit": total_paginas_audit,
        "total_registros_audit": total_registros_audit,
        "audit_busca": args.get("audit_busca", ""),
        "audit_status": args.get("audit_status", ""),
        "audit_data": args.get("audit_data", ""),
        "usuarios": usuarios,
    }


def build_auditoria_page_context(args, cache_service):
    filtros_para_template = dict(args)
    filtros_para_template.pop("page", None)

    dados_cache = cache_service.get()
    ensaios = dados_cache["dados"] if dados_cache else []
    materiais = _normalizar_lista_materiais(dados_cache.get("materiais")) if dados_cache else []

    page = int(args.get("page", 1) or 1)
    per_page = 50
    total_registros = len(ensaios)
    total_paginas = math.ceil(total_registros / per_page) if total_registros else 0
    ensaios_paginados = ensaios[(page - 1) * per_page : page * per_page]

    return {
        "ensaios": ensaios_paginados,
        "materiais": materiais,
        "pagina_atual": page,
        "total_paginas": total_paginas,
        "total_registros": total_registros,
        "request_args": filtros_para_template,
    }


def create_user(username, password, role):
    if not username or not password or not role:
        return "warning", "Preencha usuario, senha e perfil."

    if Usuario.query.filter_by(username=username).first():
        return "warning", f"Usuario '{username}' ja existe."

    novo_user = Usuario(username=username, role=role)
    novo_user.set_password(password)
    db.session.add(novo_user)
    db.session.commit()
    return "success", f"Usuario '{username}' criado com sucesso!"


def update_user(user_id, username, password, role):
    user = Usuario.query.get(user_id)
    if not user:
        return "danger", "Usuario nao encontrado."

    if username != user.username:
        existente = Usuario.query.filter_by(username=username).first()
        if existente:
            return "warning", f"O nome de usuario '{username}' ja esta em uso."

    user.username = username
    user.role = role
    if password and password.strip():
        user.set_password(password)

    try:
        db.session.commit()
        return "success", f"Usuario '{user.username}' atualizado com sucesso!"
    except Exception as exc:
        db.session.rollback()
        return "danger", f"Erro ao atualizar usuario: {exc}"


def delete_user(user_id, current_user_id):
    user = Usuario.query.get(user_id)
    if not user:
        return None, None
    if user.id == current_user_id:
        return "danger", "Voce nao pode remover a si mesmo."

    db.session.delete(user)
    db.session.commit()
    return "success", f"Usuario '{user.username}' removido."


def save_action_rules(form_data):
    return save_action_rules_from_form(form_data)


def save_material_configuration(form_data, refresh_references_fn):
    cod, version = save_material_specs_from_form(form_data)
    refresh_references_fn()
    return cod, version


def save_learning_correction(form_data, username, refresh_learning_fn, refresh_cache_fn):
    return save_manual_learning_correction(
        original_text=form_data.get("lote_original_key") or form_data.get("texto_original"),
        corrected_lot=form_data.get("novo_lote") or form_data.get("lote_correto"),
        corrected_mass=form_data.get("massa") or form_data.get("massa_correta"),
        username=username,
        refresh_learning_fn=refresh_learning_fn,
        refresh_cache_fn=refresh_cache_fn,
    )
