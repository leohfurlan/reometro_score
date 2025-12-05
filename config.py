import os

class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev")
    DATA_MINIMA_ENSAIOS = os.getenv("DATA_MINIMA_ENSAIOS", "2025-07-01")
    CAMINHO_REG403 = os.getenv("CAMINHO_REG403")
