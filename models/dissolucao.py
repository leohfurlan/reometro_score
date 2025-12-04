from models.produto import Produto

class Dissolucao(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="DISSOLUCAO")
        self.parametros_dissolucao = {}