from models.produto import Produto

class MateriaPrima(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="MATERIA_PRIMA")
        self.parametros_mp = {}
        
        # --- CORREÇÃO DE COMPATIBILIDADE ---
        self.perfis = {
            'alta': {},
            'baixa': {}
        }
        
        self.parametros = {}