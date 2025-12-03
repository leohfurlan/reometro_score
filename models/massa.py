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
        self.parametros = {} 
        self.temp_padrao = 0 # <--- NOVO: Temperatura esperada de análise (ex: 180ºC)

    def adicionar_parametro(self, nome, peso, alvo, minimo, maximo):
        novo_parametro = Parametro(nome, peso, alvo, minimo, maximo)
        self.parametros[nome] = novo_parametro