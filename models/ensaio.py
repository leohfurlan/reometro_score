from models.massa import Massa

class Ensaio:
    def __init__(self, id_ensaio, massa_objeto: Massa, valores_medidos, lote, batch, origem_viscosidade="N/A"):
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
        self.origem_viscosidade = origem_viscosidade
        
        self.score_final = 0
        self.detalhes_score = []
        self.acao_recomendada = "" 

    def calcular_score(self):
        """
        Calcula o score ponderado baseado nos parâmetros da Massa.
        Define flags (ts2_fora, t90_fora) para o frontend usar cores.
        Define a ação recomendada ao final.
        """
        soma_pesos = 0
        soma_score_ponderado = 0
        
        # Limpa detalhes anteriores caso recalcule
        self.detalhes_score = []

        # --- INICIALIZAÇÃO DAS FLAGS DE STATUS ---
        # Definimos como False (não está fora/está ok) por padrão
        # Isso evita erro no HTML caso o parâmetro não exista no teste
        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False # Caso queira usar para viscosidade também

        print(f"--- Calculando Score para Ensaio {self.id_ensaio} ({self.massa.descricao}) ---")

        # Itera sobre cada parâmetro definido na Massa (Receita)
        for nome_param, param in self.massa.parametros.items():
            
            # Verifica se temos o valor medido para este parâmetro
            if nome_param in self.valores_medidos:
                valor_medido = self.valores_medidos[nome_param]
                
                # --- LÓGICA MATEMÁTICA DO SCORE ---
                score_item = 0
                
                # Define a diferença e o intervalo baseados se o valor está acima ou abaixo do alvo
                if valor_medido >= param.alvo:
                    diferenca = valor_medido - param.alvo
                    intervalo = param.maximo - param.alvo
                else:
                    diferenca = param.alvo - valor_medido
                    intervalo = param.alvo - param.minimo
                
                # Cálculo do percentual de desvio e penalização
                if intervalo > 0:
                    percentual_desvio = diferenca / intervalo
                    # A fórmula penaliza 30 pontos se atingir o limite (Min ou Max)
                    score_item = 100 - (percentual_desvio * 30)
                else:
                    score_item = 0 # Evita divisão por zero
                
                # Garante limites entre 0 e 100
                score_item = max(0, min(100, score_item))
                
                # --- DEFINIÇÃO DAS FLAGS PARA O FRONTEND (NOVO) ---
                # Se o score for menor que 100 (ou seja, tem desvio), marcamos como "fora"
                # O HTML vai ler isso: se True = 'danger' (Vermelho), se False = 'success' (Verde)
                if nome_param == "Ts2":
                    self.ts2_fora = score_item < 100
                elif nome_param == "T90":
                    self.t90_fora = score_item < 100
                elif nome_param == "Viscosidade":
                    self.viscosidade_fora = score_item < 100

                # Acumula para média ponderada
                soma_score_ponderado += (score_item * param.peso)
                soma_pesos += param.peso
                
                # Guarda o detalhe para o relatório
                self.detalhes_score.append(f"{nome_param}: Medido={valor_medido} | Alvo={param.alvo} | Score={score_item:.2f}")

        # Cálculo final da média ponderada
        if soma_pesos > 0:
            self.score_final = soma_score_ponderado / soma_pesos
        else:
            self.score_final = 0
            
        # --- APÓS O CÁLCULO, DETERMINAR A AÇÃO AUTOMATICAMENTE ---
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
