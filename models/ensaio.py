from models.massa import Massa

class Ensaio:
    def __init__(self, id_ensaio, massa_objeto: Massa, valores_medidos, lote, batch, 
                 data_hora=None, origem_viscosidade="N/A",
                 # --- NOVOS CAMPOS ---
                 temp_plato=0, cod_grupo=0, tempo_maximo=0):
        
        self.id_ensaio = id_ensaio
        self.massa = massa_objeto
        self.valores_medidos = valores_medidos
        self.lote = lote
        self.batch = batch
        self.data_hora = data_hora
        self.origem_viscosidade = origem_viscosidade
        
        # Guardando as novas informações
        self.temp_plato = temp_plato      # Temperatura real do teste
        self.cod_grupo = cod_grupo        # Grupo de Ensaio
        self.tempo_maximo = tempo_maximo  # Tempo total configurado
        
        self.score_final = 0
        self.detalhes_score = []
        self.acao_recomendada = ""
        
        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False

    def calcular_score(self):
        soma_pesos = 0
        soma_score_ponderado = 0
        self.detalhes_score = []

        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False

        for nome_param, param in self.massa.parametros.items():
            if nome_param in self.valores_medidos:
                valor_medido = self.valores_medidos[nome_param]
                
                estourou_limite = (valor_medido < param.minimo) or (valor_medido > param.maximo)
                
                if nome_param == "Ts2": self.ts2_fora = estourou_limite
                elif nome_param == "T90": self.t90_fora = estourou_limite
                elif nome_param == "Viscosidade": self.viscosidade_fora = estourou_limite

                if valor_medido >= param.alvo:
                    diferenca = valor_medido - param.alvo
                    intervalo = param.maximo - param.alvo
                else:
                    diferenca = param.alvo - valor_medido
                    intervalo = param.alvo - param.minimo
                
                score_item = 0
                if intervalo > 0:
                    percentual_desvio = diferenca / intervalo
                    score_item = 100 - (percentual_desvio * 30)
                
                score_item = max(0, min(100, score_item))
                
                soma_score_ponderado += (score_item * param.peso)
                soma_pesos += param.peso
                
                self.detalhes_score.append(f"{nome_param}: {valor_medido} (Alvo {param.alvo})")

        if soma_pesos > 0:
            self.score_final = soma_score_ponderado / soma_pesos
        else:
            self.score_final = 0
            
        self.determinar_acao()
        return self.score_final

    def determinar_acao(self):
        tem_viscosidade = self.origem_viscosidade != "N/A"
        score = self.score_final

        if score >= 85 and tem_viscosidade:
            self.acao_recomendada = "LIBERAR - MASSA PRIME"
        elif score >= 85 and not tem_viscosidade:
            self.acao_recomendada = "LIBERAR COM RESSALVA (SEM DADO DE VISCOSIDADE)"
        elif score >= 75:
            self.acao_recomendada = "LIBERAR"
        elif score >= 70:
            self.acao_recomendada = "LIBERAR COM RESSALVA - AVALIAR COM ENGENHARIA"
        elif score > 68: 
            self.acao_recomendada = "CORTAR E MISTURAR"
        else:
            self.acao_recomendada = "REPROVAR"

    @property
    def batch_int(self):
        return int(self.batch)