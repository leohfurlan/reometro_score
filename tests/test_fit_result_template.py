from pathlib import Path


def test_fit_result_template_supports_model_family_switch_in_js():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "reometria" / "fit_result.html"
    content = template_path.read_text(encoding="utf-8")

    assert "if (modelFamily === 'edo_order_n_v1')" in content
    assert "if (modelFamily === 'pinheiro_sigmoidal_v1')" in content
    assert "pinheiro_sigmoidal_teq_v1" in content
    assert "kamal_sourour_expanded_v1" in content
    assert "isLeroyModel" in content
    assert "saveLeroyParams" in content
    assert "const tEq = tNum * Math.exp(factorExponent);" in content
    assert "((1.0 / tempK) - (1.0 / tRef))" in content
    assert "((1.0 / tRef) - (1.0 / tempK))" not in content


def test_fit_result_template_exposes_latex_leroy_sliders_and_comparison_order():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "reometria" / "fit_result.html"
    content = template_path.read_text(encoding="utf-8")

    assert "Equacoes do modelo (LaTeX)" in content
    assert "mathjax@3/es5/tex-chtml.js" in content
    assert "id=\"leroy-Av1-slider\"" in content
    assert "id=\"leroy-Av2-slider\" min=\"-12\" max=\"4.61\"" in content
    assert "Av2: { min: 1e-12, max: 4e4, scale: 'log' }" in content
    assert "integrateLeroyStatesForCurve" in content
    assert "function medianNearestAlphaTime(" in content
    assert "return medianNearestAlphaTime(timeVec, alphaVec, alphaTarget, 3);" in content
    assert "return num.toFixed(2);" in content
    assert content.index("id=\"alpha_chart\"") < content.index("id=\"alphaComparisonWrap\"")
    assert content.index("id=\"alphaComparisonWrap\"") < content.index("id=\"studyObservacoes\"")
