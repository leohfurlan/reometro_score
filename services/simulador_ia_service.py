import logging
import os
import pickle
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
import shap
import xgboost as xgb

from models.usuario import db
from services.theoretical_dataset_loader import load_theoretical_formulations
from services.theory_engine import hardness_prior_details


LOGGER = logging.getLogger(__name__)


class SimuladorIAService:
    """
    Service for model training and new formula simulation with SHAP explainability.
    """

    MODEL_PATH = "instance/modelo_dureza_v1.pkl"
    COLUMNS_PATH = "instance/modelo_colunas_v1.pkl"
    MIN_FILLER_PHR_FOR_MODEL = 1.0

    @staticmethod
    def extrair_dados_treinamento():
        """
        Realiza o JOIN entre tabelas de formulas e ensaios consolidados.
        Pivota ingredientes (PHR) para colunas de features.
        """
        # 1. Buscar itens de formula (PHR por ingrediente)
        query_itens = """
            SELECT cd_produto, cd_materia_prima, qt_phr
            FROM tb_formula_item
        """
        df_itens = pd.read_sql(query_itens, db.engine)

        # Pivotar materias-primas em colunas: mp_<codigo>
        df_pivot = df_itens.pivot(
            index="cd_produto",
            columns="cd_materia_prima",
            values="qt_phr",
        ).fillna(0)
        df_pivot.columns = [f"mp_{col}" for col in df_pivot.columns]

        # 2. Buscar resultados reais de laboratorio (target)
        query_ensaios = """
            SELECT cod_sankhya as cd_produto, dureza
            FROM ensaio_consolidado
            WHERE dureza IS NOT NULL
        """
        df_alvos = pd.read_sql(query_ensaios, db.engine)

        # Media da dureza por produto (caso existam multiplos ensaios)
        df_alvos_avg = df_alvos.groupby("cd_produto").mean().reset_index()

        # 3. Join final (features + target)
        df_final = pd.merge(
            df_pivot,
            df_alvos_avg,
            left_index=True,
            right_on="cd_produto",
            how="inner",
        )
        df_final = df_final.drop(columns=["cd_produto"])

        # 4. Synthetic Data Injection (conhecimento teorico)
        try:
            cenarios_teoricos = load_theoretical_formulations()
            linhas_teoricas = []
            for cenario in cenarios_teoricos:
                target_hardness = cenario.get("target_hardness")
                if target_hardness is None:
                    continue

                row = {"dureza": float(target_hardness)}
                for code, phr in (cenario.get("formulation_phr") or {}).items():
                    row[f"mp_{int(code)}"] = float(phr)
                linhas_teoricas.append(row)

            if linhas_teoricas:
                df_teorico = pd.DataFrame(linhas_teoricas).fillna(0.0)

                # Alinhar colunas entre base real e teorica (preencher faltantes com 0.0)
                colunas_unificadas = list(df_final.columns)
                colunas_unificadas.extend(
                    col for col in df_teorico.columns if col not in colunas_unificadas
                )

                df_final = df_final.reindex(columns=colunas_unificadas, fill_value=0.0)
                df_teorico = df_teorico.reindex(columns=colunas_unificadas, fill_value=0.0)

                # Peso dos dados teoricos: duplicar 5x
                df_teorico_pesado = pd.concat([df_teorico] * 5, ignore_index=True)

                # Fusao final: dados reais + dados sinteticos ponderados
                df_final = pd.concat([df_final, df_teorico_pesado], ignore_index=True)
        except Exception as exc:
            LOGGER.warning(
                "[SimuladorIAService] Aviso: falha ao injetar conhecimento teorico (%s).",
                exc,
            )

        # Limpeza final
        df_final = df_final.fillna(0.0)

        return df_final

    @classmethod
    def treinar_modelo(cls):
        """
        Treina o XGBRegressor para prever a Dureza e salva o modelo e as colunas.
        """
        df = cls.extrair_dados_treinamento()

        if df.empty or len(df) < 5:
            return {"status": "erro", "mensagem": "Dados insuficientes para treinamento."}

        X = df.drop(columns=["dureza"])
        y = df["dureza"]

        # Configuracao do modelo
        modelo = xgb.XGBRegressor(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=5,
            random_state=42,
        )

        modelo.fit(X, y)

        # Salvar o modelo e a lista de colunas (ordem usada no predict)
        with open(cls.MODEL_PATH, "wb") as f:
            pickle.dump(modelo, f)
        with open(cls.COLUMNS_PATH, "wb") as f:
            pickle.dump(X.columns.tolist(), f)

        return {"status": "sucesso", "r2": modelo.score(X, y)}

    @classmethod
    def simular_nova_receita(cls, dict_ingredientes):
        """
        dict_ingredientes: Ex: {'mp_105': 50.0, 'mp_204': 2.5}
        Retorna dureza por regras + (quando disponivel) dureza do modelo e SHAP.
        """
        prior = hardness_prior_details(dict_ingredientes or {})
        hardness_rule = float(prior.get("hardness_rule_final") or prior.get("hardness_rule") or 0.0)

        resposta = {
            "hardness_rule": round(hardness_rule, 2),
            "hardness_rule_final": round(hardness_rule, 2),
            "hardness_model": None,
            "hardness_final": round(hardness_rule, 2),
            # Compatibilidade com payload legado do simulador.
            "dureza_prevista": round(hardness_rule, 2),
            "unidade": "Shore A",
            "impacto_ingredientes": {},
            "base_value": None,
            "base_blend_shore": prior.get("base_blend_shore"),
            "elastomer_blend_breakdown": prior.get("elastomer_blend_breakdown") or [],
            "prior_diagnostics": {
                "dominant_elastomer_key": prior.get("dominant_elastomer_key"),
                "dominant_elastomer_code": prior.get("dominant_elastomer_code"),
                "base_shore": prior.get("base_shore"),
                "base_blend_shore": prior.get("base_blend_shore"),
                "elastomer_blend_breakdown": prior.get("elastomer_blend_breakdown") or [],
                "total_filler_phr": prior.get("total_filler_phr"),
                "total_oil_phr": prior.get("total_oil_phr"),
                "delta_filler": prior.get("delta_filler"),
                "delta_oil": prior.get("delta_oil"),
                "hardness_rule_final": prior.get("hardness_rule_final"),
            },
        }

        # Guardrail: sem carga relevante, nao aplica qualquer ajuste por modelo.
        if float(prior.get("total_filler_phr") or 0.0) < cls.MIN_FILLER_PHR_FOR_MODEL:
            resposta["guardrail"] = "total_filler_phr_below_1"
            return resposta

        hardness_model, contribuicoes, base_value = cls._predict_with_model(dict_ingredientes)
        if hardness_model is None:
            return resposta

        # Mantemos hardness_final como prior fisico (modelo ainda nao e residual).
        resposta["hardness_model"] = round(hardness_model, 2)
        resposta["impacto_ingredientes"] = contribuicoes
        resposta["base_value"] = base_value
        return resposta

    @classmethod
    def _predict_with_model(
        cls, dict_ingredientes: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, float], Any]:
        if not os.path.exists(cls.MODEL_PATH) or not os.path.exists(cls.COLUMNS_PATH):
            return None, {}, None

        # Carregar modelo e estrutura de colunas
        with open(cls.MODEL_PATH, "rb") as f:
            modelo = pickle.load(f)
        with open(cls.COLUMNS_PATH, "rb") as f:
            colunas_treino = pickle.load(f)

        # Preparar o input da simulacao (garantir colunas do treino)
        input_data = pd.DataFrame(
            np.zeros((1, len(colunas_treino)), dtype=float),
            columns=colunas_treino,
        )

        for mp, phr in (dict_ingredientes or {}).items():
            if mp in input_data.columns:
                input_data[mp] = float(phr)

        # 1. Predicao
        previsao = float(modelo.predict(input_data)[0])

        # 2. Explicabilidade com SHAP
        contribs = {}
        base_value = None
        try:
            explainer = shap.TreeExplainer(modelo)
            shap_values = explainer.shap_values(input_data)

            # Mapear valores SHAP para nomes das materias-primas com impacto relevante
            for i, col in enumerate(colunas_treino):
                val = float(shap_values[0][i])
                if abs(val) > 0.01:
                    contribs[col] = round(val, 3)

            expected = getattr(explainer, "expected_value", None)
            if expected is not None:
                base_value = float(np.ravel(expected)[0])
        except Exception as exc:
            LOGGER.warning("Falha ao calcular SHAP na simulacao: %s", exc)

        return previsao, contribs, base_value


# Exemplo de uso (pode ser chamado via Flask/Route)
if __name__ == "__main__":
    SimuladorIAService.treinar_modelo()
    res = SimuladorIAService.simular_nova_receita({"mp_10": 100, "mp_20": 5.5})
    print(f"Previsao final: {res['hardness_final']} | Detalhes: {res['impacto_ingredientes']}")
