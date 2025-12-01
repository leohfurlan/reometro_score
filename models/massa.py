class Parametro:
    def __init__(self, nome, peso, alvo, minimo, maximo):
        self.nome = nome
        self.peso = peso
        self.alvo = alvo
        self.minimo = minimo
        self.maximo = maximo

class Massa:
    def __init__(self, cod_sankhya, descricao):
        self.cod_sankhya = cod_sankhya
        self.descricao = descricao
        self.parametros = {} 

    def adicionar_parametro(self, nome, peso, alvo, minimo, maximo):
        novo_parametro = Parametro(nome, peso, alvo, minimo, maximo)
        self.parametros[nome] = novo_parametro