from datetime import datetime

from models.usuario import db


class CorrecaoAprendizado(db.Model):
    __tablename__ = "correcao_aprendizado"

    id = db.Column(db.Integer, primary_key=True)
    lote_original = db.Column(db.String(120), nullable=False, unique=True, index=True)
    lote_correto = db.Column(db.String(120), nullable=False, index=True)
    massa_id = db.Column(db.Integer, db.ForeignKey("tb_formula.cd_produto"), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True, index=True)
    data_correcao = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    diferenca_dureza = db.Column(db.Float, nullable=True)

    def __repr__(self):
        return (
            f"<CorrecaoAprendizado lote_original={self.lote_original} "
            f"lote_correto={self.lote_correto} massa_id={self.massa_id}>"
        )
