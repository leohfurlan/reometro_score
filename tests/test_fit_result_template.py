from pathlib import Path


def test_fit_result_template_supports_model_family_switch_in_js():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "reometria" / "fit_result.html"
    content = template_path.read_text(encoding="utf-8")

    assert "if (modelFamily === 'edo_order_n_v1')" in content
    assert "if (modelFamily === 'pinheiro_sigmoidal_v1')" in content
    assert "pinheiro_sigmoidal_teq_v1" in content
    assert "const tEq = tNum * Math.exp(factorExponent);" in content
    assert "((1.0 / tempK) - (1.0 / tRef))" in content
    assert "((1.0 / tRef) - (1.0 / tempK))" not in content
