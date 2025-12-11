import json
import os

from models.massa import Parametro

CONFIG_FILE = "config_massas.json"
REGRAS_FILE = "config_regras.json"

def carregar_configuracoes():
    """Lê o arquivo JSON e retorna um dicionário com as specs."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Erro ao ler config: {e}")
        return {}

def salvar_configuracao(cod_sankhya, specs):
    """
    Salva/Atualiza as specs de um produto.
    """
    dados = carregar_configuracoes()
    dados[str(cod_sankhya)] = specs # Chave sempre string
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(dados, f, indent=4)
    print(f"💾 Configuração salva para o produto {cod_sankhya}")

def aplicar_configuracoes_no_catalogo(catalogo_objetos):
    configs = carregar_configuracoes()
    count = 0
    
    for cod_str, specs in configs.items():
        cod_int = int(cod_str)
        if cod_int in catalogo_objetos:
            produto = catalogo_objetos[cod_int]
            
            # Limpa configurações anteriores
            produto.perfis = {'alta_cinza': {}, 'alta_preto': {}, 'baixa': {}, 'alta': {}}
            produto.parametros = {} 
                
            for chave_param, valores in specs.items():
                
                # --- DETECÇÃO DE PERFIL ATUALIZADA ---
                perfil = None
                
                # Prioridade para os específicos
                if chave_param.startswith('alta_cinza_'): perfil = 'alta_cinza'
                elif chave_param.startswith('alta_preto_'): perfil = 'alta_preto'
                elif chave_param.startswith('alta_'): perfil = 'alta' # Legacy
                elif chave_param.startswith('baixa_'): perfil = 'baixa'
                
                if perfil:
                    # Remove o prefixo para obter o nome real (ex: "alta_cinza_Ts2" -> "Ts2")
                    nome_real = chave_param.replace(f"{perfil}_", "")
                    
                    # Caso Especial: Temperatura/Tempo Padrão
                    if nome_real in ["temp_padrao", "tempo_total"]:
                        produto.perfis[perfil][nome_real] = valores
                        continue

                    # Caso Padrão: Objeto Parametro
                    if isinstance(valores, dict):
                        produto.adicionar_parametro(
                            perfil_chave=perfil,
                            nome=nome_real,
                            peso=valores.get('peso', 10),
                            alvo=valores.get('alvo', 0),
                            minimo=valores.get('min', 0),
                            maximo=valores.get('max', 0)
                        )
            
            # Fallback Inteligente: Se tiver 'alta' (legacy) mas não 'alta_cinza', copia
            if produto.perfis.get('alta') and not produto.perfis.get('alta_cinza'):
                produto.perfis['alta_cinza'] = produto.perfis['alta'].copy()
            if produto.perfis.get('alta') and not produto.perfis.get('alta_preto'):
                produto.perfis['alta_preto'] = produto.perfis['alta'].copy()

            count += 1
    
    print(f"✅ Configurações aplicadas em {count} produtos.")


# --- NOVAS FUNÇÕES PARA REGRAS DE AÇÃO ---

def obter_regras_padrao():
    """Retorna as regras atuais do seu sistema hardcoded"""
    return [
        {"id": 1, "nome": "MASSA PRIME", "min_score": 85, "exige_visc_real": True, "acao": "LIBERAR - MASSA PRIME", "cor": "success"},
        {"id": 2, "nome": "LIBERAÇÃO PADRÃO", "min_score": 75, "exige_visc_real": False, "acao": "LIBERAR", "cor": "success"},
        {"id": 3, "nome": "RESSALVA TÉCNICA", "min_score": 70, "exige_visc_real": False, "acao": "LIBERAR COM RESSALVA", "cor": "warning"},
        {"id": 4, "nome": "RECUPERAÇÃO", "min_score": 68, "exige_visc_real": False, "acao": "CORTAR E MISTURAR", "cor": "dark"},
        {"id": 5, "nome": "REPROVAÇÃO", "min_score": 0, "exige_visc_real": False, "acao": "REPROVAR", "cor": "danger"}
    ]

def carregar_regras_acao():
    # Se o arquivo não existe, cria o padrão
    if not os.path.exists(REGRAS_FILE):
        print("ℹ️ Arquivo de regras não encontrado. Criando padrão...")
        padrao = obter_regras_padrao()
        salvar_regras_acao(padrao)
        return padrao
    
    try:
        with open(REGRAS_FILE, 'r', encoding='utf-8') as f:
            # Tenta ler o JSON
            regras = json.load(f)
            
            # Validação extra: Se for uma lista vazia ou não for lista, força o padrão
            if not regras or not isinstance(regras, list):
                raise ValueError("JSON vazio ou inválido")

            # Ordena decrescente pelo score para garantir a lógica
            regras.sort(key=lambda x: x.get('min_score', 0), reverse=True)
            return regras

    except (json.JSONDecodeError, ValueError) as e:
        # --- CORREÇÃO DO SEU ERRO AQUI ---
        # Se o arquivo existe mas está vazio/corrompido (JSONDecodeError), 
        # nós forçamos a reescrita com os valores padrão para consertar.
        print(f"⚠️ Arquivo de regras corrompido ou vazio ({e}). Restaurando padrões...")
        padrao = obter_regras_padrao()
        salvar_regras_acao(padrao)
        return padrao
        
    except Exception as e:
        print(f"⚠️ Erro inesperado ao ler regras: {e}")
        return obter_regras_padrao()

def salvar_regras_acao(lista_regras):
    # Ordena antes de salvar para garantir a prioridade
    lista_regras.sort(key=lambda x: float(x['min_score']), reverse=True)
    
    with open(REGRAS_FILE, 'w', encoding='utf-8') as f:
        json.dump(lista_regras, f, indent=4)
    print("💾 Regras de Ação atualizadas.")