class Produto:
    def __init__(self, cod_sankhya, descricao, tipo="Geral"):
        self.cod_sankhya = cod_sankhya
        self.descricao = descricao
        self.tipo = tipo # 'MASSA', 'MATERIA_PRIMA', etc.
        
    def __repr__(self):
        return f"[{self.cod_sankhya}] {self.descricao}"