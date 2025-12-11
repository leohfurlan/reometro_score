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
        
        # Estrutura expandida para suportar especificidades
        self.perfis = {
            'alta_cinza': {},  # Reômetro Cinza (Antigo Padrão)
            'alta_preto': {},  # Reômetro Preto (Novo)
            'baixa': {},       # Viscosidade (Geralmente único)
            
            # Mantemos 'alta' genérico para fallback/legacy se não tiver equip definido
            'alta': {}         
        }
        
        self.parametros = {} 

    def adicionar_parametro(self, perfil_chave, nome, peso, alvo, minimo, maximo):
        novo = Parametro(nome, peso, alvo, minimo, maximo)
        
        # Garante que a chave existe
        if perfil_chave not in self.perfis:
            self.perfis[perfil_chave] = {}
            
        self.perfis[perfil_chave][nome] = novo