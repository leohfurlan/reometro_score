import os

from sqlalchemy import text

from config import Config
from models.consolidado import EnsaioConsolidado
from models.usuario import db

from .extensions import login_manager


def configure_app(app, config_overrides=None):
    app.config.from_object(Config)
    app.config.setdefault("SECRET_KEY", os.getenv("FLASK_SECRET_KEY", "dev"))
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///users_reoscore.db")
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SCORE_CONFIG_FILE", "config_massas.json")
    app.config.setdefault("SCORE_RULES_FILE", "config_regras.json")
    if config_overrides:
        app.config.update(config_overrides)
    app.secret_key = app.config["SECRET_KEY"]


def init_extensions(app):
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"


def bootstrap_database(app):
    from models import formula, formulation_v2, score_versioning  # noqa: F401

    with app.app_context():
        db.create_all()
        _migrate_consolidated_schema()


def register_blueprints(app):
    from .routes.admin import admin_bp
    from .routes.analysis import analysis_bp
    from .routes.auth import auth_bp
    from .routes.reometria import reometria_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(reometria_bp)


def _migrate_consolidated_schema():
    try:
        cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(ensaio_consolidado)")).all()]
        alter_needed = False

        expected_columns = {
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
            "ts2_alta": "REAL",
            "t90_alta": "REAL",
            "ts2_baixa": "REAL",
            "t90_baixa": "REAL",
            "reometro_alta": "TEXT",
            "reometro_baixa": "TEXT",
            "metodo_identificacao": "TEXT",
            "lote_original": "TEXT",
            "material_original": "TEXT",
        }

        for column_name, sql_type in expected_columns.items():
            if column_name in cols:
                continue
            db.session.execute(text(f"ALTER TABLE ensaio_consolidado ADD COLUMN {column_name} {sql_type}"))
            alter_needed = True

        if alter_needed:
            db.session.commit()
            print("Migracao aplicada: schema de ensaio_consolidado atualizado.")
    except Exception as exc:
        db.session.rollback()
        print(f"Aviso: falha na migracao de ensaio_consolidado: {exc}")


def consolidated_dataset_exists():
    return bool(EnsaioConsolidado.query.first())
