import json
import os

from services.local_learning_repo import upsert

ARQUIVO_APRENDIZADO = "aprendizado_lotes.json"

def carregar_aprendizado():
    """
    Retorna um dicionário onde a chave é o LOTE ORIGINAL (sujo)
    e o valor é um dict: { 'lote_real': '...', 'massa': '...' }
    """
    if not os.path.exists(ARQUIVO_APRENDIZADO):
        return {}
    
    try:
        with open(ARQUIVO_APRENDIZADO, 'r', encoding='utf-8') as f:
            dados = json.load(f)
            # Migração silenciosa de versão antiga (se existir)
            novos_dados = {}
            for k, v in dados.items():
                if isinstance(v, str): # Formato antigo
                    novos_dados[k] = {'lote_real': k, 'massa': v}
                else:
                    novos_dados[k] = v
            return novos_dados
    except Exception as e:
        print(f"⚠️ Erro ao ler aprendizado: {e}")
        return {}

def ensinar_lote(string_original, lote_correto, nome_massa, usuario=None):
    key = str(string_original).strip().upper()
    lote = (str(lote_correto).strip().upper() if lote_correto else key)
    massa = str(nome_massa).strip().upper()
    upsert(key, lote, massa, usuario=usuario)
    return True
