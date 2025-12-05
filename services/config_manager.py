import json
import os

from models.massa import Parametro

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
            if hasattr(produto, 'perfis'):
                produto.perfis = {'alta': {}, 'baixa': {}}
            if hasattr(produto, 'parametros'):
                produto.parametros = {} 
                
            for chave_param, valores in specs.items():
                
                # Detecta se pertence a um perfil (alta_ ou baixa_)
                perfil = 'alta' if chave_param.startswith('alta_') else 'baixa' if chave_param.startswith('baixa_') else None
                
                if perfil:
                    # Remove o prefixo para obter o nome real (ex: "alta_Ts2" -> "Ts2")
                    nome_real = chave_param.replace(f"{perfil}_", "")
                    
                    # Caso Especial: É a temperatura padrão do perfil (valor float)
                    if nome_real == "temp_padrao":
                        produto.perfis[perfil]['temp_padrao'] = valores
                        continue
                    if nome_real == "tempo_total":
                        produto.perfis[perfil]['tempo_total'] = valores
                        continue

                    # Caso Padrão: É um objeto Parametro (dict)
                    if isinstance(valores, dict):
                        produto.adicionar_parametro(
                            perfil_chave=perfil,
                            nome=nome_real,
                            peso=valores.get('peso', 10),
                            alvo=valores.get('alvo', 0),
                            minimo=valores.get('min', 0),
                            maximo=valores.get('max', 0)
                        )
                else:
                    # Compatibilidade Legacy (sem prefixo vai para 'parametros' genérico)
                    if isinstance(valores, dict) and hasattr(produto, 'parametros'):
                        produto.parametros[chave_param] = Parametro(
                            nome=chave_param,
                            peso=valores.get('peso', 10),
                            alvo=valores.get('alvo', 0),
                            minimo=valores.get('min', 0),
                            maximo=valores.get('max', 0)
                        )
            
            count += 1
    
    print(f"✅ Configurações aplicadas em {count} produtos.")
