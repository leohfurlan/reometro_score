from models.consolidado import EnsaioConsolidado
from models.score_versioning import ScoreResultado, ScoreVersao
from services.score_configuration_service import get_active_score_version
from services.scoring_engine import ScoringEngine


def build_curve_analysis_context(id_ensaio):
    ensaio = EnsaioConsolidado.query.get_or_404(id_ensaio)

    resultado = (
        ScoreResultado.query.filter_by(id_ensaio=id_ensaio).order_by(ScoreResultado.id.desc()).first()
    )
    detalhes_score = resultado.detalhes_log if resultado else {}

    if isinstance(detalhes_score, dict) and "params" in detalhes_score:
        detalhes_score = detalhes_score.get("params") or {}

    def _has_value(value):
        return value is not None

    def _to_params_dict(log):
        if not isinstance(log, dict):
            return {}
        if isinstance(log.get("params"), dict):
            return log.get("params") or {}
        return log

    tem_alta = _has_value(getattr(ensaio, "ts2_alta", None)) or _has_value(getattr(ensaio, "t90_alta", None))
    tem_baixa = _has_value(getattr(ensaio, "ts2_baixa", None)) or _has_value(
        getattr(ensaio, "t90_baixa", None)
    )
    detalhes_por_perfil = []

    versao_engine = None
    if resultado and getattr(resultado, "id_versao", None):
        versao_engine = ScoreVersao.query.get(resultado.id_versao)
    if versao_engine is None:
        versao_engine = get_active_score_version(create_from_legacy=True)

    if versao_engine:
        engine = ScoringEngine(versao_engine)
        payload_base = {
            "id_ensaio": ensaio.id_ensaio,
            "cod_sankhya": ensaio.cod_sankhya,
            "viscosidade": ensaio.viscosidade,
            "origem_viscosidade": ensaio.origem_viscosidade,
            "temp_plato": ensaio.temp_plato,
            "ts2": ensaio.ts2,
            "t90": ensaio.t90,
            "ts2_alta": getattr(ensaio, "ts2_alta", None),
            "t90_alta": getattr(ensaio, "t90_alta", None),
            "ts2_baixa": getattr(ensaio, "ts2_baixa", None),
            "t90_baixa": getattr(ensaio, "t90_baixa", None),
            "reometro_alta": getattr(ensaio, "reometro_alta", None),
            "reometro_baixa": getattr(ensaio, "reometro_baixa", None),
        }

        perfis = []
        if tem_alta:
            perfis.append("alta")
        if tem_baixa:
            perfis.append("baixa")
        if not perfis:
            perfis.append("auto")

        for perfil in perfis:
            dados = dict(payload_base)
            if perfil == "alta":
                dados["temp_plato"] = max(float(dados.get("temp_plato") or 0), 175.0)
                titulo = "Reometria Alta"
            elif perfil == "baixa":
                dados["temp_plato"] = 120.0
                titulo = "Reometria Baixa"
            else:
                titulo = "Reometria"

            ensaio_tmp = type("EnsaioTmp", (), dados)()
            res_tmp = engine.calcular(ensaio_tmp)
            detalhes_por_perfil.append(
                {
                    "perfil": perfil,
                    "titulo": titulo,
                    "score": float(res_tmp.score or 0),
                    "acao": res_tmp.acao,
                    "params": _to_params_dict(res_tmp.detalhes_log or {}),
                }
            )

    if not detalhes_por_perfil and isinstance(detalhes_score, dict) and detalhes_score:
        detalhes_por_perfil.append(
            {
                "perfil": "auto",
                "titulo": "Reometria",
                "score": float(getattr(ensaio, "score_final", 0) or 0),
                "acao": getattr(ensaio, "acao_recomendada", ""),
                "params": detalhes_score,
            }
        )

    return {
        "ensaio": ensaio,
        "detalhes": detalhes_score,
        "detalhes_por_perfil": detalhes_por_perfil,
    }
