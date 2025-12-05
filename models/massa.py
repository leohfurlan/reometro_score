from models.produto import Produto

class Parametro:
    def __init__(self, nome, peso, alvo, minimo, maximo):
        self.nome = nome
        self.peso = peso
        self.alvo = alvo
        self.minimo = minimo
        self.maximo = maximo

class Massa(Produto):
    def __init__(self, cod_sankhya, descricao):
        super().__init__(cod_sankhya, descricao, tipo="MASSA")
        # Agora temos dois perfis de parâmetros
        self.perfis = {
            'alta': {},   # Para 185-195°C
            'baixa': {}   # Para 128-170°C
        }
        # Mantemos um 'padrão' vazio para compatibilidade, se necessário
        self.parametros = {} 

    def adicionar_parametro(self, perfil_chave, nome, peso, alvo, minimo, maximo):
        """
        perfil_chave: 'alta' ou 'baixa'
        """
        novo = Parametro(nome, peso, alvo, minimo, maximo)
        if perfil_chave in self.perfis:
            self.perfis[perfil_chave][nome] = novo
        else:
            # Fallback (comportamento antigo)
            self.parametros[nome] = novo