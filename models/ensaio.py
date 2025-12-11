from models.massa import Massa, Parametro
from services.config_manager import carregar_regras_acao

class Ensaio:
    def __init__(self, id_ensaio, massa_objeto: Massa, valores_medidos, lote, batch,
                 data_hora=None, origem_viscosidade="N/A",
                 temp_plato=0, temps_plato=None, cod_grupo=0, tempo_maximo=0,
                 tempos_max=None, ids_agrupados=None,
                 equipamento_planilha=None):
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
        self.tempo_max_lista = tempos_max if tempos_max is not None else ([] if tempo_maximo == 0 else [tempo_maximo])
        self.tempo_configurado = None
        self.ids_agrupados = ids_agrupados if ids_agrupados is not None else [id_ensaio]
        self.equipamento_planilha = equipamento_planilha # 'CINZA', 'PRETO' ou None

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
        Avalia as regras carregadas do JSON em ordem decrescente de Score.
        A primeira regra que for satisfeita define a ação.
        """
        # Carrega regras (o config_manager já ordena pelo maior score primeiro)
        regras = carregar_regras_acao()
        
        # Dados do ensaio
        score = self.score_final
        tem_viscosidade = (self.origem_viscosidade != "N/A")
        eh_media = ("Média" in self.origem_viscosidade or "Media" in self.origem_viscosidade)
        viscosidade_real = (tem_viscosidade and not eh_media)

        self.acao_recomendada = "REPROVAR" # Fallback padrão

        for regra in regras:
            min_score = regra.get('min_score', 0)
            exige_real = regra.get('exige_visc_real', False)
            acao_texto = regra.get('acao', 'REPROVAR')

            # Verifica Score
            if score >= min_score:
                # Verifica condição de Viscosidade
                if exige_real:
                    if viscosidade_real:
                        self.acao_recomendada = acao_texto
                        return # Encontrou, para (regra mais alta ganha)
                    else:
                        # Score bateu, mas exige visc real e não tem.
                        # Pula para a próxima regra (provavelmente uma sem exigência de visc)
                        continue 
                else:
                    # Não exige viscosidade real, então passa
                    self.acao_recomendada = acao_texto
                    return

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
    def tempo_max_display(self):
        if not self.tempo_max_lista:
            return ""
        ordenado = sorted(self.tempo_max_lista, reverse=True)
        return " / ".join(f"{t:.0f}" for t in ordenado)

    @property
    def ids_display(self):
        return ", ".join(str(i) for i in self.ids_agrupados)
