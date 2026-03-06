from services.score_configuration_service import (
    get_score_rules,
    get_score_specs,
    update_action_rules,
    update_material_specs,
)


def carregar_configuracoes():
    """Retorna as specs a partir da versao ativa no banco."""
    return get_score_specs(create_from_legacy=True)


def salvar_configuracao(cod_sankhya, specs):
    """
    Salva/atualiza as specs de um produto criando uma nova versao ativa.
    """
    update_material_specs(cod_sankhya, specs)
    print(f"Configuracao versionada para o produto {cod_sankhya}")


def aplicar_configuracoes_no_catalogo(catalogo_objetos):
    configs = carregar_configuracoes()
    count = 0

    for cod_str, specs in configs.items():
        try:
            cod_int = int(cod_str)
        except Exception:
            continue

        if cod_int not in catalogo_objetos:
            continue

        produto = catalogo_objetos[cod_int]
        produto.perfis = {"alta_cinza": {}, "alta_preto": {}, "baixa": {}, "alta": {}}
        produto.parametros = {}

        for chave_param, valores in specs.items():
            perfil = None
            if chave_param.startswith("alta_cinza_"):
                perfil = "alta_cinza"
            elif chave_param.startswith("alta_preto_"):
                perfil = "alta_preto"
            elif chave_param.startswith("alta_"):
                perfil = "alta"
            elif chave_param.startswith("baixa_"):
                perfil = "baixa"

            if not perfil:
                continue

            nome_real = chave_param.replace(f"{perfil}_", "", 1)
            if nome_real in ["temp_padrao", "tempo_total"]:
                produto.perfis[perfil][nome_real] = valores
                continue

            if isinstance(valores, dict):
                produto.adicionar_parametro(
                    perfil_chave=perfil,
                    nome=nome_real,
                    peso=valores.get("peso", 10),
                    alvo=valores.get("alvo", 0),
                    minimo=valores.get("min", 0),
                    maximo=valores.get("max", 0),
                )

        if produto.perfis.get("alta") and not produto.perfis.get("alta_cinza"):
            produto.perfis["alta_cinza"] = produto.perfis["alta"].copy()
        if produto.perfis.get("alta") and not produto.perfis.get("alta_preto"):
            produto.perfis["alta_preto"] = produto.perfis["alta"].copy()

        count += 1

    print(f"Configuracoes aplicadas em {count} produtos.")


def obter_regras_padrao():
    """Mantem compatibilidade com chamadas legadas."""
    return get_score_rules(create_from_legacy=True)


def carregar_regras_acao():
    return get_score_rules(create_from_legacy=True)


def salvar_regras_acao(lista_regras):
    update_action_rules(lista_regras)
    print("Regras de acao versionadas.")
