from models.score_versioning import ScoreVersao, ScoreResultado
from models.consolidado import EnsaioConsolidado

class ScoringEngine:
    def __init__(self, versao: ScoreVersao):
        self.versao = versao
        self.specs = versao.config_snapshot.get('specs', {})
        self.regras_acao = versao.config_snapshot.get('regras', [])
        
        # Ordena regras por prioridade (maior score primeiro)
        self.regras_acao.sort(key=lambda x: x.get('min_score', 0), reverse=True)

    def calcular(self, ensaio: EnsaioConsolidado) -> ScoreResultado:
        """
        Recebe um EnsaioConsolidado (dado puro) e retorna um ScoreResultado (nota).
        """
        cod_sankhya = str(ensaio.cod_sankhya)
        spec_material = self.specs.get(cod_sankhya, {})

        # 1. Identificar Perfil e Hidratar dados do formato Flat para Dict
        perfil_usado, nome_perfil_log = self._selecionar_perfil(spec_material, ensaio)
        
        detalhes_log = {
            "meta_perfil": nome_perfil_log,
            "params": {}
        }
        
        soma_pontos = 0
        soma_pesos = 0
        
        # Se não achou perfil (material sem config), retorna 0
        if not perfil_usado:
            return ScoreResultado(
                id_ensaio=ensaio.id_ensaio,
                id_versao=self.versao.id,
                score=0,
                acao="SEM CONFIG",
                is_aprovado=False,
                detalhes_log=detalhes_log
            )

        # 2. Iterar parâmetros e calcular notas parciais
        for nome_param, config in perfil_usado.items():
            # Ignora configurações de cabeçalho (temp_padrao, tempo_total)
            if not isinstance(config, dict):
                continue

            # Pega o valor real do ensaio de forma dinâmica
            # Tenta pegar 'Ts2', 'T90'. Se for 'Viscosidade', pega especial.
            key_lookup = nome_param
            if key_lookup == 'Viscosidade':
                valor_real = ensaio.viscosidade
            else:
                valor_real = getattr(ensaio, key_lookup.lower(), None)

            # Calcula nota individual
            nota_param, status = self._calcular_nota_parametro(valor_real, config)
            
            peso = config.get('peso', 10)
            
            # Se o valor for N/A (não medido), a nota é 0 mas o peso conta (penaliza)
            # A menos que o peso seja 0.
            soma_pontos += (nota_param * peso)
            soma_pesos += peso
            
            detalhes_log["params"][nome_param] = {
                'valor_real': valor_real,
                'alvo': config.get('alvo'),
                'min': config.get('min'),
                'max': config.get('max'),
                'peso': peso,
                'nota': nota_param,
                'status': status
            }

        # 3. Fechar Score Final
        score_final = soma_pontos / soma_pesos if soma_pesos > 0 else 0
        
        # 4. Determinar Ação
        acao_final, aprovado = self._determinar_acao(score_final, ensaio)

        return ScoreResultado(
            id_ensaio=ensaio.id_ensaio,
            id_versao=self.versao.id,
            score=score_final,
            acao=acao_final,
            is_aprovado=aprovado,
            detalhes_log=detalhes_log
        )

    def _selecionar_perfil(self, specs_gerais, ensaio):
        """
        Lógica robusta para extrair o perfil correto do JSON achatado (Legacy).
        Ex: Transforma {'alta_cinza_Ts2': {...}} em {'Ts2': {...}}
        """
        if not specs_gerais:
            return {}, "N/A"

        temp = ensaio.temp_plato or 0
        
        # Definição dos prefixos baseada na temperatura e equipamento
        prefixo_alvo = None
        nome_perfil = "Indefinido"

        if temp >= 170:
            # Lógica para Alta Temperatura
            # Tenta detectar se é Cinza ou Preto (se tiver essa info no ensaio, 
            # mas o ensaio consolidado não guarda 'equipamento' explicitamente, 
            # então tentamos a sorte ou padrão)
            
            # Ordem de preferência: 
            # 1. Se tiver configs explícitas de 'alta_cinza' (Padrão atual)
            # 2. Se tiver 'alta_preto'
            # 3. Se tiver 'alta' (Legacy puro)
            
            keys = specs_gerais.keys()
            tem_cinza = any(k.startswith('alta_cinza_') for k in keys)
            tem_preto = any(k.startswith('alta_preto_') for k in keys)
            tem_alta_legacy = any(k.startswith('alta_') and not '_' in k.replace('alta_', '') for k in keys)

            if tem_cinza:
                prefixo_alvo = 'alta_cinza_'
                nome_perfil = "Alta (Cinza)"
            elif tem_preto:
                prefixo_alvo = 'alta_preto_'
                nome_perfil = "Alta (Preto)"
            else:
                prefixo_alvo = 'alta_'
                nome_perfil = "Alta (Genérico)"
        
        elif temp >= 100:
            # Baixa Temperatura
            prefixo_alvo = 'baixa_'
            nome_perfil = "Baixa"
        
        else:
            # Viscosidade pura ou temperatura muito baixa
            # Geralmente usa perfil de baixa ou viscosidade isolada
            prefixo_alvo = 'baixa_'
            nome_perfil = "Viscosidade/Baixa"

        # Monta o dicionário limpo
        perfil_montado = {}
        
        for key, valor in specs_gerais.items():
            # Verifica se a chave começa com o prefixo
            if key.startswith(prefixo_alvo):
                # Remove o prefixo para ficar só o nome do parametro (Ex: "Ts2")
                nome_limpo = key.replace(prefixo_alvo, "")
                perfil_montado[nome_limpo] = valor
            
            # Também aceita parâmetros globais sem prefixo se não colidirem
            elif key in ['Ts2', 'T90', 'Viscosidade'] and key not in perfil_montado:
                perfil_montado[key] = valor

        return perfil_montado, nome_perfil

    def _calcular_nota_parametro(self, valor, config):
        if valor is None: return 0, "N/A"
        
        # Garante float
        try:
            val = float(valor)
        except:
            return 0, "ERR"

        alvo = float(config.get('alvo', 0))
        minimo = float(config.get('min', 0))
        maximo = float(config.get('max', 0))
        
        # Se min e max forem 0, provavelmente não está configurado corretamente
        if minimo == 0 and maximo == 0:
            return 0, "NO_SPECS"

        # Fora dos limites = Nota 0
        if val < minimo or val > maximo:
            return 0, "OUT"
            
        # Cálculo linear de proximidade do alvo
        if val >= alvo:
            distancia = val - alvo
            range_total = maximo - alvo
        else:
            distancia = alvo - val
            range_total = alvo - minimo
            
        if range_total <= 0: 
            # Se alvo == min ou alvo == max, e valor está dentro, é 100
            return 100, "OK"
        
        # Penalidade: Quanto mais longe do alvo, menor a nota
        # Regra: Se estiver no limite (min ou max), nota é 0? 
        # Ou regra proporcional?
        # Usando regra proporcional padrão:
        # Se desvio = range_total (está no limite), perde 30% a 100% dependendo da rigidez.
        # Vamos usar a lógica linear simples: No limite = Nota 70 (Aceitável) ou Nota 0?
        # O código original usava: score_item = 100 - (percentual_desvio * 30) -> No limite dava 70.
        
        percentual_desvio = distancia / range_total
        nota = 100 - (percentual_desvio * 30) # Mantendo lógica do legado (aprovado no limite)
        
        return max(0, min(100, nota)), "OK"

    def _determinar_acao(self, score, ensaio):
        # Itera sobre as regras da versão
        visc_real = (ensaio.origem_viscosidade != 'Média' and ensaio.origem_viscosidade != 'N/A')
        
        for regra in self.regras_acao:
            if score >= regra['min_score']:
                # Verifica condição extra (viscosidade)
                if regra.get('exige_visc_real', False) and not visc_real:
                    continue # Pula regra se exigir visc real e não tiver
                
                # Regra aceita!
                is_aprovado = regra.get('cor') == 'success'
                return regra['acao'], is_aprovado
                
        return "REPROVAR", False