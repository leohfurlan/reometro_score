from models.usuario import db
from datetime import datetime
import json

class ScoreVersao(db.Model):
    __tablename__ = 'score_versao'
    
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)  # Ex: "v1.0 - Produção 2024"
    status = db.Column(db.String(20), default='DRAFT') # ACTIVE, SHADOW, ARCHIVED, DRAFT
    
    # O Snapshot é um JSON gigante que guarda TUDO que é necessário para calcular:
    # { "specs": { ...config_massas... }, "regras": [ ...config_regras... ] }
    config_snapshot = db.Column(db.JSON, nullable=False)
    
    criado_em = db.Column(db.DateTime, default=datetime.now)
    ativado_em = db.Column(db.DateTime)

    def __repr__(self):
        return f"<ScoreVersao {self.nome} ({self.status})>"

class ScoreResultado(db.Model):
    __tablename__ = 'ensaio_score_resultado'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Relacionamentos
    id_ensaio = db.Column(db.Integer, db.ForeignKey('ensaio_consolidado.id_ensaio'), nullable=False)
    id_versao = db.Column(db.Integer, db.ForeignKey('score_versao.id'), nullable=False)
    
    # O Resultado Matemático
    score = db.Column(db.Float, nullable=False)
    acao = db.Column(db.String(50))     # APROVADO, REPROVADO...
    is_aprovado = db.Column(db.Boolean) # Flag rápida para KPIs
    
    # Auditoria Total: Guarda EXATAMENTE como a conta foi feita
    # Ex: { "Ts2": { "valor": 1.2, "alvo": 1.0, "nota": 90, "peso": 10 } }
    detalhes_log = db.Column(db.JSON)
    
    # Metadata
    calculado_em = db.Column(db.DateTime, default=datetime.now)

    # Vínculo para facilitar queries
    versao = db.relationship('ScoreVersao')