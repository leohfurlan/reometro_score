import logging
import os
import pickle
import numpy as np
import pandas as pd
import xgboost as xgb
import shap
from typing import Dict, Any, List, Optional
from services.theory_engine import hardness_prior_details

LOGGER = logging.getLogger(__name__)

class MultiTargetSimulator:
    """
    Service for multi-target simulation using XGBoost models.
    Supports: Hardness, Density, Abrasion, Resilience, TS2, T90, Viscosity, etc.
    """
    
    MODELS_DIR = "instance/models"
    PROPERTIES = [
        "dureza", "densidade", "abrasao", "resiliencia", 
        "ts2", "t90", "viscosidade", 
        "tensao_ruptura", "alongamento", "rasgo",
        "modulo_100", "modulo_300"
    ]
    
    _models = {}
    _columns = {}
    
    @classmethod
    def _load_models(cls):
        """Loads all available models from disk."""
        if cls._models:
            return

        if not os.path.exists(cls.MODELS_DIR):
            os.makedirs(cls.MODELS_DIR)
            return

        for prop in cls.PROPERTIES:
            model_path = os.path.join(cls.MODELS_DIR, f"model_{prop}.pkl")
            cols_path = os.path.join(cls.MODELS_DIR, f"cols_{prop}.pkl")
            
            if os.path.exists(model_path) and os.path.exists(cols_path):
                try:
                    with open(model_path, "rb") as f:
                        cls._models[prop] = pickle.load(f)
                    with open(cols_path, "rb") as f:
                        cls._columns[prop] = pickle.load(f)
                    LOGGER.info(f"Loaded model for {prop}")
                except Exception as e:
                    LOGGER.error(f"Failed to load model for {prop}: {e}")

    @classmethod
    def predict(cls, ingredients: Dict[str, float]) -> Dict[str, Any]:
        """
        Predicts properties for a given list of ingredients.
        Returns a dictionary with predictions for all available models.
        """
        cls._load_models()
        
        results = {}
        
        # 1. Physical Rule Engine (Hardness only)
        try:
            prior = hardness_prior_details(ingredients)
            results["physical_hardness"] = prior.get("hardness_rule_final")
            results["prior_diagnostics"] = prior
        except Exception as e:
            LOGGER.warning(f"Physical engine failed: {e}")
            results["physical_hardness"] = None

        # 2. ML Models
        for prop in cls.PROPERTIES:
            if prop not in cls._models:
                results[prop] = {"value": None, "shap": {}}
                continue
                
            model = cls._models[prop]
            cols = cls._columns[prop]
            
            # Prepare input vector
            input_df = pd.DataFrame(np.zeros((1, len(cols))), columns=cols)
            for mp, phr in ingredients.items():
                if mp in input_df.columns:
                    input_df[mp] = float(phr)
            
            # Predict
            try:
                pred_val = float(model.predict(input_df)[0])
                
                # SHAP Explanation
                shap_contribs = {}
                try:
                    explainer = shap.TreeExplainer(model)
                    shap_values = explainer.shap_values(input_df)
                    
                    for i, col in enumerate(cols):
                        val = float(shap_values[0][i])
                        if abs(val) > 0.01:
                            shap_contribs[col] = round(val, 3)
                except Exception as e:
                    LOGGER.warning(f"SHAP failed for {prop}: {e}")

                results[prop] = {
                    "value": round(pred_val, 2),
                    "shap": shap_contribs
                }
            except Exception as e:
                LOGGER.error(f"Prediction failed for {prop}: {e}")
                results[prop] = {"value": None, "shap": {}}

        return results

    @classmethod
    def train_all(cls):
        """Helper to trigger training of all models via script."""
        # This is a placeholder. Actual training logic will be in train_multitarget.py
        pass
