from models.produto import Produto

class Dissolucao(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="DISSOLUCAO")
        self.parametros_dissolucao = {}
        
        # --- CORREÇÃO DE COMPATIBILIDADE ---
        # Adiciona a estrutura de perfis para não quebrar o Ensaio
        self.perfis = {
            'alta': {},
            'baixa': {}
        }
        
        # Mantém o atributo antigo para compatibilidade legacy, se necessário
        self.parametros = {}