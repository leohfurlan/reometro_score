from app import app, db
from sqlalchemy import text

def resetar_tabelas_versionamento():
    with app.app_context():
        print("Apagando tabelas de versionamento antigas...")
        
        # Apaga na ordem correta (primeiro a filha, depois a mãe)
        try:
            db.session.execute(text("DROP TABLE IF EXISTS ensaio_score_resultado"))
            db.session.execute(text("DROP TABLE IF EXISTS score_versao"))
            db.session.commit()
            print("✅ Tabelas apagadas com sucesso.")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Erro ao apagar tabelas: {e}")

if __name__ == "__main__":
    resetar_tabelas_versionamento()