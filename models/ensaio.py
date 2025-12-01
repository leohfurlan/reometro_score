from models.massa import Massa

class Ensaio:
    def __init__(self, id_ensaio, massa_objeto: Massa, valores_medidos, lote, batch, data_hora=None, origem_viscosidade="N/A"):
        """
        Inicializa um objeto Ensaio.
        
        :param id_ensaio: ID único do ensaio (do banco de dados)
        :param massa_objeto: Instância da classe Massa contendo os parâmetros alvo
        :param valores_medidos: Dicionário com os valores reais (ex: {'Ts2': 60, ...})
        :param lote: Número do lote (para rastreabilidade)
        :param batch: Número do batch (para rastreabilidade)
        :param origem_viscosidade: Indica se o dado veio de 'Real (Batch)', 'Média (Lote)' ou 'N/A'
        """
        self.id_ensaio = id_ensaio
        self.massa = massa_objeto
        self.valores_medidos = valores_medidos
        self.lote = lote
        self.batch = batch
        self.data_hora = data_hora  # <--- Novo campo
        self.origem_viscosidade = origem_viscosidade
        
        self.score_final = 0
        self.detalhes_score = []
        self.acao_recomendada = ""
        
        # Flags para o frontend
        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False

    def calcular_score(self):
        soma_pesos = 0
        soma_score_ponderado = 0
        self.detalhes_score = []

        # Inicializa flags como False (Verde/Ok)
        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False

        # Itera sobre cada parâmetro definido na Massa
        for nome_param, param in self.massa.parametros.items():
            
            if nome_param in self.valores_medidos:
                valor_medido = self.valores_medidos[nome_param]
                
                # --- 1. LÓGICA DE COR (Fora dos Limites) ---
                # Se for menor que o mínimo OU maior que o máximo -> VERMELHO
                estourou_limite = (valor_medido < param.minimo) or (valor_medido > param.maximo)
                
                if nome_param == "Ts2":
                    self.ts2_fora = estourou_limite
                elif nome_param == "T90":
                    self.t90_fora = estourou_limite
                elif nome_param == "Viscosidade":
                    self.viscosidade_fora = estourou_limite

                # --- 2. CÁLCULO DO SCORE (Mantido igual) ---
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
                
                # Detalhes para o modal (formatação de string)
                self.detalhes_score.append(f"{nome_param}: Medido={valor_medido} | Alvo={param.alvo} | Score={score_item:.2f}")

        if soma_pesos > 0:
            self.score_final = soma_score_ponderado / soma_pesos
        else:
            self.score_final = 0
            
        self.determinar_acao()
        return self.score_final


    def determinar_acao(self):
        """
        Implementa a regra de decisão baseada no Score e na presença de Viscosidade.
        Regra baseada na planilha Excel:
        - >= 85 com Visc: PRIME
        - >= 85 sem Visc: RESSALVA (SEM DADO)
        - >= 75: LIBERAR
        - >= 70: RESSALVA (ENGENHARIA)
        - > 68: CORTAR E MISTURAR
        - Resto: REPROVAR
        """
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
            
        elif score > 68: # Regra do Excel é Maior que 68 (exclui o 68 exato)
            self.acao_recomendada = "CORTAR E MISTURAR"
            
        else:
            self.acao_recomendada = "REPROVAR"

    @property
    def batch_int(self):
        """Retorna o batch como valor inteiro, se possível."""
        return int(self.batch)
