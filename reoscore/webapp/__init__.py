import os

from flask import Flask

from .bootstrap import bootstrap_database, configure_app, init_extensions, register_blueprints


def create_app(config_overrides=None):
    package_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(package_dir, "..", ".."))
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
    )
    configure_app(app, config_overrides=config_overrides)
    init_extensions(app)
    bootstrap_database(app)
    register_blueprints(app)
    return app
