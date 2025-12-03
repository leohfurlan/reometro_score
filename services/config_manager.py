import json
import os

CONFIG_FILE = "config_massas.json"

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
    specs ex: {'Ts2': {'min': 10, 'alvo': 12, 'max': 14}, ...}
    """
    dados = carregar_configuracoes()
    dados[str(cod_sankhya)] = specs # Chave sempre string
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(dados, f, indent=4)
    print(f"💾 Configuração salva para o produto {cod_sankhya}")

def aplicar_configuracoes_no_catalogo(catalogo_objetos):
    """
    Recebe o dicionário de objetos (do Sankhya) e injeta os parâmetros salvos no JSON.
    """
    configs = carregar_configuracoes()
    count = 0
    
    for cod_str, specs in configs.items():
        cod_int = int(cod_str)
        if cod_int in catalogo_objetos:
            produto = catalogo_objetos[cod_int]
            
            # Limpa parâmetros antigos e aplica os do JSON
            if hasattr(produto, 'parametros'):
                produto.parametros = {} # Reseta
                
                for param, valores in specs.items():
                    # Adiciona dinamicamente usando o método da classe Massa
                    produto.adicionar_parametro(
                        nome=param,
                        peso=valores.get('peso', 10),
                        alvo=valores.get('alvo', 0),
                        minimo=valores.get('min', 0),
                        maximo=valores.get('max', 0)
                    )
                count += 1
    
    print(f"✅ Configurações aplicadas em {count} produtos.")