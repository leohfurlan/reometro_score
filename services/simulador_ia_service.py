import pandas as pd
import numpy as np
import xgboost as xgb
import shap
import pickle
import os
from sqlalchemy import text
from models.usuario import db
from models.formula import Formula, FormulaItem
from models.consolidado import EnsaioConsolidado


class SimuladorIAService:
    """
    Service for model training and new formula simulation with SHAP explainability.
    """

    MODEL_PATH = "instance/modelo_dureza_v1.pkl"
    COLUMNS_PATH = "instance/modelo_colunas_v1.pkl"

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
        raiz_projeto = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        caminho_teorico = os.path.join(raiz_projeto, "conhecimento_teorico.csv")

        if os.path.exists(caminho_teorico):
            try:
                try:
                    df_teorico = pd.read_csv(caminho_teorico, sep=";", encoding="utf-8")
                except UnicodeDecodeError:
                    df_teorico = pd.read_csv(caminho_teorico, sep=";", encoding="latin1")

                df_teorico.columns = [str(col).strip() for col in df_teorico.columns]

                # Normalizar colunas de MP: 105 -> mp_105
                rename_cols = {}
                for col in df_teorico.columns:
                    if col.isdigit():
                        rename_cols[col] = f"mp_{col}"
                if rename_cols:
                    df_teorico = df_teorico.rename(columns=rename_cols)

                # Garantir dados numericos
                df_teorico = df_teorico.apply(pd.to_numeric, errors="coerce")

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
                print(
                    f"[SimuladorIAService] Aviso: falha ao injetar conhecimento teorico ({exc})."
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
        Retorna: {
            'previsao': float,
            'contribuicoes': { 'nome_mp': valor_shap }
        }
        """
        if not os.path.exists(cls.MODEL_PATH):
            return {"erro": "Modelo nao treinado. Execute treinar_modelo() primeiro."}

        # Carregar modelo e estrutura de colunas
        with open(cls.MODEL_PATH, "rb") as f:
            modelo = pickle.load(f)
        with open(cls.COLUMNS_PATH, "rb") as f:
            colunas_treino = pickle.load(f)

        # Preparar o input da simulacao (garantir colunas do treino)
        input_data = pd.DataFrame([0.0] * len(colunas_treino)).T
        input_data.columns = colunas_treino

        for mp, phr in dict_ingredientes.items():
            if mp in input_data.columns:
                input_data[mp] = float(phr)

        # 1. Predicao
        previsao = float(modelo.predict(input_data)[0])

        # 2. Explicabilidade com SHAP
        explainer = shap.TreeExplainer(modelo)
        shap_values = explainer.shap_values(input_data)

        # Mapear valores SHAP para nomes das materias-primas com impacto relevante
        contribuicoes = {}
        for i, col in enumerate(colunas_treino):
            val = float(shap_values[0][i])
            if abs(val) > 0.01:
                contribuicoes[col] = round(val, 3)

        return {
            "dureza_prevista": round(previsao, 2),
            "unidade": "Shore A",
            "impacto_ingredientes": contribuicoes,
            "base_value": float(explainer.expected_value),  # Dureza media da base
        }


# Exemplo de uso (pode ser chamado via Flask/Route)
if __name__ == "__main__":
    SimuladorIAService.treinar_modelo()
    res = SimuladorIAService.simular_nova_receita({"mp_10": 100, "mp_20": 5.5})
    print(f"Previsao: {res['dureza_prevista']} | Detalhes: {res['impacto_ingredientes']}")
