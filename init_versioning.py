from models.usuario import db
from reoscore.webapp import create_app
from services.score_configuration_service import get_active_score_version


def inicializar_versionamento():
    app = create_app()

    with app.app_context():
        db.create_all()
        print("Tabelas de versionamento criadas/verificadas.")

        versao = get_active_score_version(create_from_legacy=True)
        if versao:
            print(f"Versao ativa pronta: {versao.nome} (ID {versao.id})")
        else:
            print("Nenhuma versao ativa foi criada.")


if __name__ == "__main__":
    inicializar_versionamento()
