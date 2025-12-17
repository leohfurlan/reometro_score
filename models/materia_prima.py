from models.produto import Produto
from models.massa import Parametro  # Importar Parametro para usar na função

class MateriaPrima(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="MATERIA_PRIMA")
        self.parametros_mp = {}
        
        # Estrutura de perfis compatível com o Config Manager
        self.perfis = {
            'alta': {},
            'baixa': {},
            'alta_cinza': {},
            'alta_preto': {}
        }
        
        self.parametros = {}

    def adicionar_parametro(self, perfil_chave, nome, peso, alvo, minimo, maximo):
        """
        Adiciona parâmetros vindos do arquivo de configuração (config_massas.json).
        """
        novo = Parametro(nome, peso, alvo, minimo, maximo)
        
        # Garante que a chave do perfil existe
        if perfil_chave not in self.perfis:
            self.perfis[perfil_chave] = {}
            
        self.perfis[perfil_chave][nome] = novo