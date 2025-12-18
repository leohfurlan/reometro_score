from models.produto import Produto
from models.massa import Parametro

class Dissolucao(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="DISSOLUCAO")
        self.parametros_dissolucao = {}
        
        # Estrutura de perfis
        self.perfis = {
            'alta': {},
            'baixa': {},
            'alta_cinza': {},
            'alta_preto': {}
        }
        
        self.parametros = {}

    def adicionar_parametro(self, perfil_chave, nome, peso, alvo, minimo, maximo):
        """
        Adiciona parâmetros vindos do arquivo de configuração.
        """
        novo = Parametro(nome, peso, alvo, minimo, maximo)
        
        if perfil_chave not in self.perfis:
            self.perfis[perfil_chave] = {}
            
        self.perfis[perfil_chave][nome] = novo