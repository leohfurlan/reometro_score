from pathlib import Path


def test_fit_result_template_uses_v2_kinetic_expression():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "reometria" / "fit_result.html"
    content = template_path.read_text(encoding="utf-8")

    assert "const lnX = lnK + (n * Math.log(Math.max(tNum, 1e-300)));" in content
    assert "n * (lnK + Math.log(tSafe))" not in content
