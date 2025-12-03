from models.produto import Produto

class MateriaPrima(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="MATERIA_PRIMA")
        # Futuro: Adicionar parâmetros específicos de MP (ex: Densidade, Ponto de Fusão)
        self.parametros_mp = {}