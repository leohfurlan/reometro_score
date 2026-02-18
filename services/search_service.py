import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from models.usuario import db
from sqlalchemy import text
from app import app

class SearchService:
    _model = None
    _features = None
    _ids = None
    
    @classmethod
    def _fit(cls):
        """
        Fits the NearestNeighbors model on current data.
        """
        with app.app_context():
            # 1. Fetch Formula Items
            query_items = text("SELECT cd_produto, cd_materia_prima, qt_phr FROM tb_formula_item")
            df_items = pd.read_sql(query_items, db.engine)
            
            if df_items.empty:
                return

            # Pivot ingredients
            df_pivot = df_items.pivot(
                index="cd_produto",
                columns="cd_materia_prima",
                values="qt_phr"
            ).fillna(0)
            
            # Normalize column names
            df_pivot.columns = [f"mp_{col}" for col in df_pivot.columns]
            
            cls._features = df_pivot.columns.tolist()
            cls._ids = df_pivot.index.tolist()
            
            cls._model = NearestNeighbors(n_neighbors=5, metric='euclidean')
            cls._model.fit(df_pivot.values)

    @classmethod
    def find_similar(cls, ingredients: dict, k=5):
        """
        Finds k similar formulations.
        ingredients: {'mp_105': 50.0, ...}
        """
        if cls._model is None:
            cls._fit()
            
        if cls._model is None:
            return []

        # Prepare input vector
        input_vector = np.zeros(len(cls._features))
        for i, feature in enumerate(cls._features):
            input_vector[i] = ingredients.get(feature, 0.0)
            
        # Search
        distances, indices = cls._model.kneighbors([input_vector], n_neighbors=k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            prod_id = cls._ids[idx]
            results.append({
                "id": prod_id,
                "distance": round(dist, 4)
            })
            
        return results
