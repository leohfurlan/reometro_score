from app import app, db
from models.score_versioning import ScoreVersao
from services.config_manager import carregar_configuracoes, carregar_regras_acao
import json

def inicializar_versionamento():
    with app.app_context():
        # 1. Cria as tabelas novas
        db.create_all()
        print("✅ Tabelas de versionamento criadas/verificadas.")
        
        # 2. Verifica se já existe versão ativa
        if ScoreVersao.query.filter_by(status='ACTIVE').first():
            print("ℹ️ Já existe uma versão ativa. Nenhuma ação necessária.")
            return

        # 3. Carrega configurações atuais dos arquivos JSON
        print("📂 Lendo configurações atuais...")
        specs = carregar_configuracoes()
        regras = carregar_regras_acao()
        
        snapshot = {
            "specs": specs,
            "regras": regras,
            "meta": {"descricao": "Versão inicial migrada dos arquivos JSON"}
        }
        
        # 4. Cria a Versão v1.0
        v1 = ScoreVersao(
            nome="v1.0 - Produção (Legado)",
            status="ACTIVE",
            config_snapshot=snapshot
        )
        
        db.session.add(v1)
        db.session.commit()
        print(f"🚀 Versão '{v1.nome}' criada com ID {v1.id}!")

if __name__ == "__main__":
    inicializar_versionamento()