from services.score_configuration_service import get_score_specs, update_action_rules, update_material_specs

from reoscore.webapp.utils import parse_float_locale, parse_int_locale


def build_action_rules_from_form(form_data):
    nomes = form_data.getlist("nome[]")
    scores = form_data.getlist("min_score[]")
    acoes = form_data.getlist("acao[]")
    cores = form_data.getlist("cor[]")
    marcados = set(form_data.getlist("exige_visc_real"))

    regras = []
    for index, nome in enumerate(nomes):
        regras.append(
            {
                "id": index + 1,
                "nome": nome,
                "min_score": parse_float_locale(scores[index], default=0) or 0,
                "exige_visc_real": str(index) in marcados,
                "acao": acoes[index],
                "cor": cores[index],
            }
        )

    return regras


def save_action_rules_from_form(form_data):
    regras = build_action_rules_from_form(form_data)
    return update_action_rules(regras, descricao="Atualizacao de regras via interface administrativa")


def build_material_specs_from_form(form_data):
    cod = form_data.get("cod_sankhya")
    specs = {}

    current_specs = (get_score_specs(create_from_legacy=True).get(str(cod), {}) or {})
    if isinstance(current_specs, dict):
        for custom_key in (
            "regra_abrasao_5n",
            "abrasao_5n_dureza_min",
            "abrasao_5n_dureza_max",
            "abrasao_metodologia_5n_dureza_min",
            "abrasao_metodologia_5n_dureza_max",
        ):
            if custom_key in current_specs:
                specs[custom_key] = current_specs[custom_key]

    t_cinza = parse_float_locale(form_data.get("alta_cinza_temp_padrao"))
    tempo_cinza = parse_float_locale(form_data.get("alta_cinza_tempo_total"))
    t_preto = parse_float_locale(form_data.get("alta_preto_temp_padrao"))
    tempo_preto = parse_float_locale(form_data.get("alta_preto_tempo_total"))

    if t_cinza and not t_preto:
        t_preto = t_cinza
    if tempo_cinza and not tempo_preto:
        tempo_preto = tempo_cinza
    if t_preto and not t_cinza:
        t_cinza = t_preto
    if tempo_preto and not tempo_cinza:
        tempo_cinza = tempo_preto

    if t_cinza:
        specs["alta_cinza_temp_padrao"] = t_cinza
    if tempo_cinza:
        specs["alta_cinza_tempo_total"] = tempo_cinza
    if t_preto:
        specs["alta_preto_temp_padrao"] = t_preto
    if tempo_preto:
        specs["alta_preto_tempo_total"] = tempo_preto

    t_baixa = parse_float_locale(form_data.get("baixa_temp_padrao"))
    tempo_baixa = parse_float_locale(form_data.get("baixa_tempo_total"))
    if t_baixa:
        specs["baixa_temp_padrao"] = t_baixa
    if tempo_baixa:
        specs["baixa_tempo_total"] = tempo_baixa

    ab5n_min = parse_float_locale(form_data.get("abrasao_5n_dureza_min"))
    ab5n_max = parse_float_locale(form_data.get("abrasao_5n_dureza_max"))
    if ab5n_min is not None or ab5n_max is not None:
        existing_rule = specs.get("regra_abrasao_5n", {}) if isinstance(specs.get("regra_abrasao_5n"), dict) else {}
        specs["regra_abrasao_5n"] = {
            "dureza_min": ab5n_min if ab5n_min is not None else existing_rule.get("dureza_min", 40.0),
            "dureza_max": ab5n_max if ab5n_max is not None else existing_rule.get("dureza_max", 50.0),
        }

    for param_name in ["Ts2", "T90", "Viscosidade"]:
        peso_alta = parse_int_locale(form_data.get(f"alta_{param_name}_peso"), default=0) or 0

        min_c = parse_float_locale(form_data.get(f"alta_cinza_{param_name}_min"))
        alvo_c = parse_float_locale(form_data.get(f"alta_cinza_{param_name}_alvo"))
        max_c = parse_float_locale(form_data.get(f"alta_cinza_{param_name}_max"))

        min_p = parse_float_locale(form_data.get(f"alta_preto_{param_name}_min"))
        alvo_p = parse_float_locale(form_data.get(f"alta_preto_{param_name}_alvo"))
        max_p = parse_float_locale(form_data.get(f"alta_preto_{param_name}_max"))

        if (min_c or alvo_c or max_c) and not (min_p or alvo_p or max_p):
            min_p, alvo_p, max_p = min_c, alvo_c, max_c
        elif (min_p or alvo_p or max_p) and not (min_c or alvo_c or max_c):
            min_c, alvo_c, max_c = min_p, alvo_p, max_p

        if min_c is not None or alvo_c is not None or max_c is not None:
            specs[f"alta_cinza_{param_name}"] = {
                "min": min_c if min_c is not None else 0,
                "alvo": alvo_c if alvo_c is not None else 0,
                "max": max_c if max_c is not None else 0,
                "peso": peso_alta,
            }
        if min_p is not None or alvo_p is not None or max_p is not None:
            specs[f"alta_preto_{param_name}"] = {
                "min": min_p if min_p is not None else 0,
                "alvo": alvo_p if alvo_p is not None else 0,
                "max": max_p if max_p is not None else 0,
                "peso": peso_alta,
            }

        min_b = parse_float_locale(form_data.get(f"baixa_{param_name}_min"))
        alvo_b = parse_float_locale(form_data.get(f"baixa_{param_name}_alvo"))
        max_b = parse_float_locale(form_data.get(f"baixa_{param_name}_max"))
        peso_b = parse_int_locale(form_data.get(f"baixa_{param_name}_peso"), default=0) or 0

        if min_b is not None or alvo_b is not None or max_b is not None:
            specs[f"baixa_{param_name}"] = {
                "min": min_b if min_b is not None else 0,
                "alvo": alvo_b if alvo_b is not None else 0,
                "max": max_b if max_b is not None else 0,
                "peso": peso_b,
            }

    def add_physical_spec(name, min_value=None, max_value=None):
        if min_value is None and max_value is None:
            return
        specs[f"baixa_{name}"] = {"min": min_value, "alvo": None, "max": max_value, "peso": 0}

    add_physical_spec(
        "Dureza",
        parse_float_locale(form_data.get("fisica_Dureza_min")),
        parse_float_locale(form_data.get("fisica_Dureza_max")),
    )
    add_physical_spec(
        "Densidade",
        parse_float_locale(form_data.get("fisica_Densidade_min")),
        parse_float_locale(form_data.get("fisica_Densidade_max")),
    )
    add_physical_spec(
        "Resiliencia",
        parse_float_locale(form_data.get("fisica_Resiliencia_min")),
        parse_float_locale(form_data.get("fisica_Resiliencia_max")),
    )
    add_physical_spec(
        "Abrasao",
        None,
        parse_float_locale(form_data.get("fisica_Abrasao_max")),
    )
    add_physical_spec(
        "TensaoRuptura",
        parse_float_locale(form_data.get("fisica_TensaoRuptura_min")),
        None,
    )
    add_physical_spec(
        "Alongamento",
        parse_float_locale(form_data.get("fisica_Alongamento_min")),
        None,
    )
    add_physical_spec(
        "Rasgo",
        parse_float_locale(form_data.get("fisica_Rasgo_min")),
        None,
    )

    return cod, specs


def save_material_specs_from_form(form_data):
    cod, specs = build_material_specs_from_form(form_data)
    versao = update_material_specs(
        cod,
        specs,
        descricao=f"Atualizacao das specs do produto {cod} via interface administrativa",
    )
    return cod, versao
