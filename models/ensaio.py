from models.massa import Massa


class Ensaio:
    def __init__(self, id_ensaio, massa_objeto: Massa, valores_medidos, lote, batch,
                 data_hora=None, origem_viscosidade="N/A",
                 temp_plato=0, temps_plato=None, cod_grupo=0, tempo_maximo=0,
                 ids_agrupados=None):
        self.id_ensaio = id_ensaio
        self.massa = massa_objeto
        self.valores_medidos = valores_medidos
        self.lote = lote
        self.batch = batch
        self.data_hora = data_hora
        self.origem_viscosidade = origem_viscosidade

        # Novas informacoes agrupadas
        temp_lista = temps_plato if temps_plato is not None else ([] if temp_plato == 0 else [temp_plato])
        if temp_lista:
            try:
                temp_lista = sorted(temp_lista, reverse=True)
            except Exception:
                pass
        self.temp_plato_lista = temp_lista
        self.temp_plato = self.temp_plato_lista[0] if self.temp_plato_lista else 0
        self.cod_grupo = cod_grupo
        self.tempo_maximo = tempo_maximo
        self.ids_agrupados = ids_agrupados if ids_agrupados is not None else [id_ensaio]

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

        # Itera sobre os parametros esperados (receita)
        for nome_param, param in self.massa.parametros.items():
            if nome_param in self.valores_medidos:
                valor_medido = self.valores_medidos[nome_param]

                estourou_limite = (valor_medido < param.minimo) or (valor_medido > param.maximo)

                if nome_param == "Ts2":
                    self.ts2_fora = estourou_limite
                elif nome_param == "T90":
                    self.t90_fora = estourou_limite
                elif nome_param == "Viscosidade":
                    self.viscosidade_fora = estourou_limite

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

                self.detalhes_score.append(f"{nome_param}: {valor_medido} (Alvo {param.alvo}) -> Nota {score_item:.0f}")
            else:
                # Dado faltante recebe nota 0 ponderada
                soma_score_ponderado += 0
                soma_pesos += param.peso

                self.detalhes_score.append(f"{nome_param}: NAO MEDIDO (Nota 0)")

                if nome_param == "Ts2":
                    self.ts2_fora = True
                elif nome_param == "T90":
                    self.t90_fora = True
                elif nome_param == "Viscosidade":
                    self.viscosidade_fora = True

        if soma_pesos > 0:
            self.score_final = soma_score_ponderado / soma_pesos
        else:
            self.score_final = 0

        self.determinar_acao()
        return self.score_final

    def determinar_acao(self):
        """
        Regras de decisao:
        - PRIME: Score >= 85 e viscosidade real.
        - LIBERAR: Score >= 75 ou (Score >= 85 com viscosidade media).
        - RESSALVA: Score >= 70 ou falta de dados.
        """
        tem_viscosidade = self.origem_viscosidade != "N/A"
        eh_media_lote = ("MÇ¸dia" in self.origem_viscosidade) or ("Media" in self.origem_viscosidade)
        score = self.score_final

        if score >= 85 and tem_viscosidade and not eh_media_lote:
            self.acao_recomendada = "LIBERAR - MASSA PRIME"
        elif score >= 85 and tem_viscosidade and eh_media_lote:
            self.acao_recomendada = "LIBERAR"
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

    @property
    def temp_plato_display(self):
        if not self.temp_plato_lista:
            return ""
        ordenado = sorted(self.temp_plato_lista, reverse=True)
        return " / ".join(f"{t:.0f}" for t in ordenado)

    @property
    def ids_display(self):
        return ", ".join(str(i) for i in self.ids_agrupados)
