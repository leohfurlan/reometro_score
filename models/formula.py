from models.usuario import db
from datetime import datetime

class Formula(db.Model):
    __tablename__ = 'tb_formula'

    # Identificação da Fórmula
    cd_produto = db.Column(db.Integer, primary_key=True, autoincrement=False) 
    ds_composto = db.Column(db.String(255)) # Ex: "MASSA CAMELBACK AGRICOLA"
    dt_revisao = db.Column(db.Date)
    ativo = db.Column(db.Boolean, default=True)

    # Relacionamento
    itens = db.relationship('FormulaItem', backref='formula', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Formula {self.cd_produto} - {self.ds_composto}>"

class FormulaItem(db.Model):
    __tablename__ = 'tb_formula_item'

    id_item = db.Column(db.Integer, primary_key=True)
    
    cd_produto = db.Column(db.Integer, db.ForeignKey('tb_formula.cd_produto'), nullable=False)
    cd_materia_prima = db.Column(db.Integer, nullable=False)
    
    # NOVO CAMPO: Para armazenar o nome do ingrediente (Ex: "BORRACHA NATURAL")
    ds_materia_prima = db.Column(db.String(255), nullable=True)
    
    qt_phr = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f"<Item {self.cd_materia_prima} ({self.qt_phr} phr)>"