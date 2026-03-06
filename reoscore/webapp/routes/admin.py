from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from reoscore.use_cases.admin_panel import (
    build_auditoria_page_context,
    build_config_page_context,
    create_user,
    delete_user,
    save_action_rules,
    save_learning_correction,
    save_material_configuration,
    update_user,
)
from reoscore.webapp.runtime import recarregar_cache_memoria
from reoscore.webapp.extensions import cache_service
from services.etl_service import carregar_referencias_estaticas, recarregar_aprendizado_memoria

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/config")
@login_required
def pagina_config():
    context = build_config_page_context(request.args, cache_service, current_user)
    return render_template("config.html", **context)


@admin_bp.route("/auditoria")
@login_required
def pagina_auditoria():
    context = build_auditoria_page_context(request.args, cache_service)
    return render_template("auditoria.html", **context)


@admin_bp.route("/salvar_regras", methods=["POST"])
@login_required
def salvar_regras():
    if current_user.role != "admin":
        flash("Acesso negado.", "danger")
        return redirect(url_for("dashboard_home"))

    save_action_rules(request.form)
    flash("Regras de acao globais atualizadas!", "success")
    return redirect(url_for("admin.pagina_config"))


@admin_bp.route("/salvar_config", methods=["POST"])
@login_required
def salvar_config():
    if current_user.role != "admin":
        flash("Acesso negado.", "danger")
        return redirect(url_for("dashboard_home"))

    cod, _ = save_material_configuration(request.form, carregar_referencias_estaticas)
    flash(f"Configuracao do produto {cod} salva (Sincronizada Cinza/Preto)!", "success")
    return redirect(url_for("admin.pagina_config", q=cod))


@admin_bp.route("/salvar_correcao", methods=["POST"])
@login_required
def salvar_correcao():
    category, message = save_learning_correction(
        request.form,
        current_user.username,
        recarregar_aprendizado_memoria,
        recarregar_cache_memoria,
    )
    if message:
        flash(message, category)
    return redirect(url_for("admin.pagina_config", _anchor="ensinar"))


@admin_bp.route("/adicionar_usuario", methods=["POST"])
@login_required
def adicionar_usuario():
    if current_user.role != "admin":
        flash("Acesso negado.", "danger")
        return redirect(url_for("dashboard_home"))

    category, message = create_user(
        username=request.form.get("username"),
        password=request.form.get("password"),
        role=request.form.get("role"),
    )
    if message:
        flash(message, category)
    return redirect(url_for("admin.pagina_config", _anchor="usuarios"))


@admin_bp.route("/editar_usuario", methods=["POST"])
@login_required
def editar_usuario():
    if current_user.role != "admin":
        flash("Acesso negado.", "danger")
        return redirect(url_for("dashboard_home"))

    category, message = update_user(
        user_id=request.form.get("user_id"),
        username=request.form.get("username"),
        password=request.form.get("password"),
        role=request.form.get("role"),
    )
    if message:
        flash(message, category)
    return redirect(url_for("admin.pagina_config", _anchor="usuarios"))


@admin_bp.route("/remover_usuario/<int:user_id>")
@login_required
def remover_usuario(user_id):
    if current_user.role != "admin":
        flash("Acesso negado.", "danger")
        return redirect(url_for("dashboard_home"))

    category, message = delete_user(user_id, current_user.id)
    if message:
        flash(message, category)
    return redirect(url_for("admin.pagina_config", _anchor="usuarios"))
