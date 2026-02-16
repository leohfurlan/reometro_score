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
    Serviço para treinamento de modelos preditivos e simulação de novas receitas 
    de borracha com explicabilidade via SHAP.
    """
    
    MODEL_PATH = "instance/modelo_dureza_v1.pkl"
    COLUMNS_PATH = "instance/modelo_colunas_v1.pkl"

    @staticmethod
    def extrair_dados_treinamento():
        """
        Realiza o JOIN entre as tabelas de fórmulas e ensaios consolidados.
        Pivota os ingredientes (PHR) em colunas para servirem de features (X).
        """
        # 1. Buscar itens de fórmula (PHR por ingrediente)
        # Selecionamos cd_produto (da fórmula), cd_materia_prima e qt_phr
        query_itens = """
            SELECT cd_produto, cd_materia_prima, qt_phr 
            FROM tb_formula_item
        """
        df_itens = pd.read_sql(query_itens, db.engine)
        
        # Pivotar: Transformar cada matéria-prima em uma coluna
        # As linhas serão os produtos (fórmulas) e as colunas o PHR de cada MP
        df_pivot = df_itens.pivot(index='cd_produto', columns='cd_materia_prima', values='qt_phr').fillna(0)
        df_pivot.columns = [f"mp_{col}" for col in df_pivot.columns]
        
        # 2. Buscar resultados reais de laboratório (Target)
        # Filtramos ensaios que possuem dureza preenchida
        query_ensaios = """
            SELECT cod_sankhya as cd_produto, dureza 
            FROM ensaio_consolidado 
            WHERE dureza IS NOT NULL
        """
        df_alvos = pd.read_sql(query_ensaios, db.engine)
        
        # Agrupar alvos por produto (caso haja múltiplos ensaios para a mesma fórmula, tiramos a média)
        df_alvos_avg = df_alvos.groupby('cd_produto').mean().reset_index()
        
        # 3. Join Final (Features + Target)
        df_final = pd.merge(df_pivot, df_alvos_avg, left_index=True, right_on='cd_produto', how='inner')
        
        return df_final.drop(columns=['cd_produto'])

    @classmethod
    def treinar_modelo(cls):
        """
        Treina o XGBRegressor para prever a Dureza e salva o modelo e as colunas.
        """
        df = cls.extrair_dados_treinamento()
        
        if df.empty or len(df) < 5:
            return {"status": "erro", "mensagem": "Dados insuficientes para treinamento."}

        X = df.drop(columns=['dureza'])
        y = df['dureza']

        # Configuração do Modelo
        modelo = xgb.XGBRegressor(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=5,
            random_state=42
        )
        
        modelo.fit(X, y)

        # Salvar o modelo e a lista de colunas (importante para manter a ordem no Predict)
        with open(cls.MODEL_PATH, 'wb') as f:
            pickle.dump(modelo, f)
        with open(cls.COLUMNS_PATH, 'wb') as f:
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
            return {"erro": "Modelo não treinado. Execute treinar_modelo() primeiro."}

        # Carregar modelo e estrutura de colunas
        with open(cls.MODEL_PATH, 'rb') as f:
            modelo = pickle.load(f)
        with open(cls.COLUMNS_PATH, 'rb') as f:
            colunas_treino = pickle.load(f)

        # Preparar o input da simulação (Garantir que todas as colunas do treino existam)
        input_data = pd.DataFrame([0.0] * len(colunas_treino)).T
        input_data.columns = colunas_treino
        
        for mp, phr in dict_ingredientes.items():
            if mp in input_data.columns:
                input_data[mp] = float(phr)

        # 1. Predição
        previsao = float(modelo.predict(input_data)[0])

        # 2. Explicabilidade com SHAP
        explainer = shap.TreeExplainer(modelo)
        shap_values = explainer.shap_values(input_data)
        
        # Mapear valores SHAP para os nomes das matérias-primas
        # Filtrar apenas ingredientes que tiveram impacto relevante (!= 0)
        contribuicoes = {}
        for i, col in enumerate(colunas_treino):
            val = float(shap_values[0][i])
            if abs(val) > 0.01:
                contribuicoes[col] = round(val, 3)

        return {
            "dureza_prevista": round(previsao, 2),
            "unidade": "Shore A",
            "impacto_ingredientes": contribuicoes,
            "base_value": float(explainer.expected_value) # Dureza média da base
        }

# Exemplo de uso (Pode ser chamado via Flask/Route):
if __name__ == "__main__":
    SimuladorIAService.treinar_modelo()
    res = SimuladorIAService.simular_nova_receita({'mp_10': 100, 'mp_20': 5.5})
    print(f"Previsão: {res['dureza_prevista']} | Detalhes: {res['impacto_ingredientes']}")