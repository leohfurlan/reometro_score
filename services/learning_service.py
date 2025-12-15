import json
import os

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

def ensinar_lote(string_original, lote_correto, nome_massa):
    dados = carregar_aprendizado()
    
    # Chave é sempre a string suja original NORMALIZADA
    # Isso deve bater com o .strip().upper() do ETL
    key = str(string_original).strip().upper()
    
    # Se o lote correto vier vazio, usa a chave (mas limpa)
    if not lote_correto:
        lote_correto = key
        
    dados[key] = {
        'lote_real': str(lote_correto).strip().upper(),
        'massa': str(nome_massa).strip().upper()
    }
    
    try:
        with open(ARQUIVO_APRENDIZADO, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        print(f"🧠 Aprendizado salvo: '{key}' -> Lote '{lote_correto}' / Massa '{nome_massa}'")
        return True
    except Exception as e:
        print(f"❌ Erro ao salvar aprendizado: {e}")
        return False