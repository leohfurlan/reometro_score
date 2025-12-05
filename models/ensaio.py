from models.massa import Massa, Parametro


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
        self.tempo_configurado = None
        self.ids_agrupados = ids_agrupados if ids_agrupados is not None else [id_ensaio]

        self.score_final = 0
        self.detalhes_score = []
        self.acao_recomendada = ""

        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False
        self.tempo_configurado = None


    def identificar_perfil(self):
        """
        Seleciona o perfil de parâmetros com base na temperatura medida.
        Retorna o dicionário bruto (pode conter temp_padrao) e o nome do perfil escolhido.
        """
        temp = self.temp_plato or 0

        # Se não houver perfis, mantém compatibilidade com o comportamento antigo
        if not hasattr(self.massa, 'perfis'):
            return self.massa.parametros or {}, "Padrão (Legacy)"

        perfil_escolhido = None
        if temp >= 175:
            perfil_escolhido = 'alta'
        elif 120 <= temp < 175:
            perfil_escolhido = 'baixa'

        if perfil_escolhido and self.massa.perfis.get(perfil_escolhido):
            nome = "Alta Temperatura" if perfil_escolhido == 'alta' else "Baixa Temperatura"
            return self.massa.perfis[perfil_escolhido], nome

        # Fallback: usa primeiro perfil configurado ou o legado genérico
        for chave, dados in self.massa.perfis.items():
            if dados:
                nome = "Alta Temperatura" if chave == 'alta' else "Baixa Temperatura"
                return dados, nome

        return self.massa.parametros or {}, "Indefinido (Fallback)"

    def calcular_score(self):
        soma_pesos = 0
        soma_score_ponderado = 0
        self.detalhes_score = []

        self.ts2_fora = False
        self.t90_fora = False
        self.viscosidade_fora = False

        # 1. Seleciona os parâmetros corretos dinamicamente
        perfil_dict, nome_perfil = self.identificar_perfil()

        temp_padrao = None
        parametros_ativos = {}
        if isinstance(perfil_dict, dict):
            temp_padrao = perfil_dict.get('temp_padrao')
            self.tempo_configurado = perfil_dict.get('tempo_total')
            parametros_ativos = {
                k: v for k, v in perfil_dict.items()
                if isinstance(v, Parametro)
            }

        # Inclui parâmetros legados que não estão no perfil selecionado
        if hasattr(self.massa, 'parametros'):
            for nome, param in self.massa.parametros.items():
                if isinstance(param, Parametro) and nome not in parametros_ativos:
                    parametros_ativos[nome] = param

        self.parametros_usados = parametros_ativos
        self.nome_perfil_usado = nome_perfil
        self.temp_padrao_usado = temp_padrao
        
        # Adiciona info visual sobre qual perfil foi usado
        self.detalhes_score.append(f"[INFO] Perfil aplicado: {nome_perfil} ({self.temp_plato:.0f} C)")

        if not parametros_ativos:
            self.score_final = 0
            self.detalhes_score.append("Sem parâmetros configurados para esta temperatura.")
            self.determinar_acao()
            return 0

        # 2. Itera sobre os parametros ativos do perfil
        for nome_param, param in parametros_ativos.items():
            valor_medido = self.valores_medidos.get(nome_param)

            if valor_medido is None:
                soma_pesos += param.peso
                self.detalhes_score.append(f"{nome_param}: NAO MEDIDO (Nota 0)")
                if nome_param == "Ts2":
                    self.ts2_fora = True
                elif nome_param == "T90":
                    self.t90_fora = True
                elif nome_param == "Viscosidade":
                    self.viscosidade_fora = True
                continue

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
