from flask import Blueprint, render_template
from flask_login import login_required

from reoscore.use_cases.score_analysis import build_curve_analysis_context

analysis_bp = Blueprint("analysis", __name__)


@analysis_bp.route("/analise/<int:id_ensaio>")
@login_required
def analise_curva(id_ensaio):
    context = build_curve_analysis_context(id_ensaio)
    return render_template("detalhe_curva.html", **context)
