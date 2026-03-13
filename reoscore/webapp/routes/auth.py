from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user

from models.usuario import Usuario, db

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = Usuario.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("dashboard_home"))

        flash("Login ou senha invalidos.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Voce saiu do sistema.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/criar_admin")
def criar_admin():
    if Usuario.query.filter_by(username="admin").first():
        return "Admin ja existe."

    new_admin = Usuario(username="admin", role="admin")
    new_admin.set_password("senha123")
    db.session.add(new_admin)
    db.session.commit()
    return "Admin criado com sucesso! (User: admin / Pass: senha123)"


@auth_bp.route("/criar_operador")
def criar_operador():
    if Usuario.query.filter_by(username="operador").first():
        return "Usuario 'operador' ja existe."

    new_user = Usuario(username="operador", role="operador")
    new_user.set_password("vulca123")
    db.session.add(new_user)
    db.session.commit()
    return "Usuario 'operador' criado com sucesso! (User: operador / Pass: vulca123)"
