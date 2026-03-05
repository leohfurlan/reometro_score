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
        perfil_usado, nome_perfil_log, perfil_chave = self._selecionar_perfil(spec_material, ensaio)
        
        detalhes_log = {
            "meta_perfil": nome_perfil_log,
            "params": {}
        }
        
        soma_pontos = 0
        soma_pesos = 0
        
        # Se nÃ£o achou perfil (material sem config), retorna 0
        if not perfil_usado:
            return ScoreResultado(
                id_ensaio=ensaio.id_ensaio,
                id_versao=self.versao.id,
                score=0,
                acao="SEM CONFIG",
                is_aprovado=False,
                detalhes_log=detalhes_log
            )

        # 2. Iterar parÃ¢metros e calcular notas parciais
        for nome_param, config in perfil_usado.items():
            # Ignora configuraÃ§Ãµes de cabeÃ§alho (temp_padrao, tempo_total)
            if not isinstance(config, dict):
                continue

            # Pega o valor real do ensaio de forma dinÃ¢mica
            # Tenta pegar 'Ts2', 'T90'. Se for 'Viscosidade', pega especial.
            key_lookup = nome_param
            fonte_valor = key_lookup.lower()
            if key_lookup == 'Viscosidade':
                valor_real = ensaio.viscosidade
            elif key_lookup in ('Ts2', 'T90'):
                valor_real, fonte_valor = self._obter_valor_reometria(ensaio, key_lookup, perfil_chave)
            else:
                valor_real = getattr(ensaio, key_lookup.lower(), None)

            # Calcula nota individual
            nota_param, status = self._calcular_nota_parametro(valor_real, config)
            
            peso = config.get('peso', 10)
            
            # Se o valor for N/A (nÃ£o medido), a nota Ã© 0 mas o peso conta (penaliza)
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
                'status': status,
                'fonte_valor': fonte_valor,
            }

        # 3. Fechar Score Final
        score_final = soma_pontos / soma_pesos if soma_pesos > 0 else 0
        
        # 4. Determinar AÃ§Ã£o
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
        Logica robusta para extrair o perfil correto do JSON achatado (Legacy).
        Ex: Transforma {'alta_cinza_Ts2': {...}} em {'Ts2': {...}}
        """
        if not specs_gerais:
            return {}, "N/A", None

        temp = ensaio.temp_plato or 0
        prefixo_alvo = None
        nome_perfil = "Indefinido"
        perfil_chave = None

        if temp >= 170:
            keys = specs_gerais.keys()
            tem_cinza = any(k.startswith("alta_cinza_") for k in keys)
            tem_preto = any(k.startswith("alta_preto_") for k in keys)
            cor_reometro = self._inferir_cor_reometro(getattr(ensaio, "reometro_alta", None))

            if tem_cinza and tem_preto:
                if cor_reometro == "PRETO":
                    prefixo_alvo = "alta_preto_"
                    nome_perfil = "Alta (Preto)"
                    perfil_chave = "alta_preto"
                else:
                    prefixo_alvo = "alta_cinza_"
                    nome_perfil = "Alta (Cinza)"
                    perfil_chave = "alta_cinza"
            elif tem_cinza:
                prefixo_alvo = "alta_cinza_"
                nome_perfil = "Alta (Cinza)"
                perfil_chave = "alta_cinza"
            elif tem_preto:
                prefixo_alvo = "alta_preto_"
                nome_perfil = "Alta (Preto)"
                perfil_chave = "alta_preto"
            else:
                prefixo_alvo = "alta_"
                nome_perfil = "Alta (Generico)"
                perfil_chave = "alta"
        elif temp >= 100:
            prefixo_alvo = "baixa_"
            nome_perfil = "Baixa"
            perfil_chave = "baixa"
        else:
            prefixo_alvo = "baixa_"
            nome_perfil = "Viscosidade/Baixa"
            perfil_chave = "baixa"

        perfil_montado = {}
        for key, valor in specs_gerais.items():
            if key.startswith(prefixo_alvo):
                nome_limpo = key.replace(prefixo_alvo, "")
                perfil_montado[nome_limpo] = valor
            elif key in ["Ts2", "T90", "Viscosidade"] and key not in perfil_montado:
                perfil_montado[key] = valor

        return perfil_montado, nome_perfil, perfil_chave

    def _inferir_cor_reometro(self, rotulo):
        txt = str(rotulo or "").upper()
        if not txt:
            return None
        if "PRETO" in txt:
            return "PRETO"
        if "BRANCO" in txt or "CINZA" in txt:
            return "BRANCO"
        return None

    def _obter_valor_reometria(self, ensaio, nome_param, perfil_chave):
        base = str(nome_param or "").strip().lower()
        if not base:
            return None, None

        if perfil_chave == "baixa":
            candidatos = [f"{base}_baixa", f"{base}_alta", base]
        elif perfil_chave in ("alta_cinza", "alta_preto", "alta"):
            candidatos = [f"{base}_alta", f"{base}_baixa", base]
        else:
            candidatos = [base, f"{base}_alta", f"{base}_baixa"]

        for attr in candidatos:
            val = getattr(ensaio, attr, None)
            if val is not None:
                return val, attr
        return None, candidatos[0]

    def _calcular_nota_parametro(self, valor, config):
        if valor is None:
            return 0, "N/A"

        # Garante float
        try:
            val = float(valor)
        except Exception:
            return 0, "ERR"

        alvo = float(config.get('alvo', 0))
        minimo = float(config.get('min', 0))
        maximo = float(config.get('max', 0))

        # Sem limites configurados = sem especificacao valida.
        if minimo == 0 and maximo == 0:
            return 0, "NO_SPECS"

        fora_limite = (val < minimo or val > maximo)

        # Calculo linear de proximidade ao alvo (mesma curva dentro e fora da faixa).
        if val >= alvo:
            distancia = val - alvo
            range_total = maximo - alvo
        else:
            distancia = alvo - val
            range_total = alvo - minimo

        if range_total <= 0:
            # Faixa util degenerada: somente o alvo pontua.
            if distancia == 0:
                return 100, "OK"
            return 0, ("OUT" if fora_limite else "NO_SPECS")

        percentual_desvio = distancia / range_total
        nota = 100 - (percentual_desvio * 30)
        status = "OUT" if fora_limite else "OK"
        return max(0, min(100, nota)), status

    def _determinar_acao(self, score, ensaio):
        # Itera sobre as regras da versao.
        origem_visc = str(getattr(ensaio, 'origem_viscosidade', '') or '').strip().lower()
        visc_real = origem_visc not in {'media', 'média', 'n/a', 'na', ''}
        
        for regra in self.regras_acao:
            if score >= regra['min_score']:
                # Verifica condiÃ§Ã£o extra (viscosidade)
                if regra.get('exige_visc_real', False) and not visc_real:
                    continue # Pula regra se exigir visc real e nÃ£o tiver
                
                # Regra aceita!
                is_aprovado = regra.get('cor') == 'success'
                return regra['acao'], is_aprovado
                
        return "REPROVAR", False
