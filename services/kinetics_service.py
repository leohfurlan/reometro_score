import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from connection import connect_to_database
from services.kinetics_solver import fit_kinetics, parametrize_curve, alpha_model, first_crossing_time

OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
ALPHA_COMPARE_TARGETS_DEFAULT = [0.10, 0.20, 0.50, 0.90]


def list_ensaios_for_fit(date_start=None, date_end=None, text_query=None, limit=300):
    conn = connect_to_database()
    cur = conn.cursor()
    lote_batch_expr = "COALESCE(CAST(e.NUMERO_LOTE AS varchar(100)), CAST(e.BATCH AS varchar(100)), '')"

    sql = f"""
    SELECT TOP (?)
        e.COD_ENSAIO,
        e.DATA,
        e.TEMP_PLATO_INF,
        e.AMOSTRA,
        e.CODIGO,
        {lote_batch_expr} AS LOTE_BATCH,
        e.TMINTEMPO,
        e.TMINTORQUE,
        e.TMAXTORQUE
    FROM dbo.ENSAIO e
    WHERE 1=1
    """
    params = [int(limit)]

    if date_start:
        sql += " AND CAST(e.DATA AS date) >= ?"
        params.append(date_start)
    if date_end:
        sql += " AND CAST(e.DATA AS date) <= ?"
        params.append(date_end)
    if text_query:
        sql += f" AND (CAST(e.COD_ENSAIO AS varchar(30)) LIKE ? OR e.AMOSTRA LIKE ? OR e.CODIGO LIKE ? OR {lote_batch_expr} LIKE ?)"
        like = f"%{text_query}%"
        params.extend([like, like, like, like])

    sql += " ORDER BY e.DATA DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def get_curve_points(cod_ensaios):
    if not cod_ensaios:
        return []

    conn = connect_to_database()
    cur = conn.cursor()
    marks = ",".join("?" for _ in cod_ensaios)
    sql = f"""
    SELECT
        e.COD_ENSAIO,
        e.TEMP_PLATO_INF,
        e.TMINTEMPO,
        e.TMINTORQUE,
        e.TMAXTORQUE,
        v.TEMPO,
        v.TORQUE
    FROM dbo.ENSAIO e
    JOIN dbo.ENSAIO_VALORES v ON v.COD_ENSAIO = e.COD_ENSAIO
    WHERE e.COD_ENSAIO IN ({marks})
    ORDER BY e.COD_ENSAIO, v.TEMPO
    """
    cur.execute(sql, list(cod_ensaios))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def _group_curves(rows):
    grouped = {}
    for r in rows:
        key = int(r["COD_ENSAIO"])
        grouped.setdefault(
            key,
            {
                "COD_ENSAIO": key,
                "T_C": float(r["TEMP_PLATO_INF"] or 0),
                "T_K": float(r["TEMP_PLATO_INF"] or 0) + 273.15,
                "ML": float(r["TMINTORQUE"] or 0),
                "MH": float(r["TMAXTORQUE"] or 0),
                "TMINTEMPO": float(r["TMINTEMPO"] or 0),
                "tempo": [],
                "torque": [],
            },
        )
        grouped[key]["tempo"].append(float(r["TEMPO"] or 0))
        grouped[key]["torque"].append(float(r["TORQUE"] or 0))

    curves = []
    for c in grouped.values():
        tempo = np.array(c["tempo"], dtype=float)
        torque = np.array(c["torque"], dtype=float)
        t_rel, alpha = parametrize_curve(tempo, torque, c["ML"], c["MH"], c["TMINTEMPO"])
        c["t_rel"] = t_rel
        c["alpha"] = alpha
        curves.append(c)
    return curves


def run_fit(cod_ensaios):
    rows = get_curve_points(cod_ensaios)
    curves = _group_curves(rows)
    result = fit_kinetics(curves)

    payload = {
        "fit_id": datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8],
        "created_at": datetime.utcnow().isoformat(),
        **result,
        "ensaios": [
            {"COD_ENSAIO": c["COD_ENSAIO"], "T_C": c["T_C"], "T_K": c["T_K"]}
            for c in curves
        ],
        "curves": [
            {
                "COD_ENSAIO": c["COD_ENSAIO"],
                "T_C": c["T_C"],
                "tempo": c["tempo"],
                "torque": c["torque"],
                "t_rel": c["t_rel"].tolist(),
                "alpha": c["alpha"].tolist(),
            }
            for c in curves
        ],
    }

    if payload.get("success"):
        for c in payload["curves"]:
            t = np.array(c["t_rel"], dtype=float)
            tk = np.full_like(t, fill_value=float(c["T_C"]) + 273.15)
            pred = alpha_model(t, tk, payload["k0"], payload["Ea"], payload["n"])
            c["alpha_model"] = pred.tolist()

    save_fit_payload(payload)
    return payload


def save_fit_payload(payload):
    file_path = OUT_DIR / f"fit_{payload['fit_id']}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(file_path)


def load_fit_payload(fit_id):
    file_path = OUT_DIR / f"fit_{fit_id}.json"
    if not file_path.exists():
        return None
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_preview_curve(cod_ensaio):
    rows = get_curve_points([int(cod_ensaio)])
    curves = _group_curves(rows)
    if not curves:
        return None
    c = curves[0]
    return {
        "COD_ENSAIO": c["COD_ENSAIO"],
        "tempo": c["tempo"],
        "torque": c["torque"],
        "t_rel": c["t_rel"].tolist(),
        "alpha": c["alpha"].tolist(),
    }


def build_alpha_time_comparison(payload, alpha_targets=None):
    alpha_targets = [float(a) for a in (alpha_targets or ALPHA_COMPARE_TARGETS_DEFAULT)]
    rows = []
    curves = payload.get("curves") or []

    for c in curves:
        t_rel = np.array(c.get("t_rel") or [], dtype=float)
        alpha_real = np.array(c.get("alpha") or [], dtype=float)
        alpha_sim = np.array(c.get("alpha_model") or [], dtype=float) if c.get("alpha_model") is not None else None

        can_eval_real = len(t_rel) >= 2 and len(t_rel) == len(alpha_real)
        can_eval_sim = alpha_sim is not None and len(t_rel) >= 2 and len(t_rel) == len(alpha_sim)

        comparisons = []
        for alpha_target in alpha_targets:
            t_real = first_crossing_time(t_rel, alpha_real, alpha_target) if can_eval_real else None
            t_sim = first_crossing_time(t_rel, alpha_sim, alpha_target) if can_eval_sim else None
            comparisons.append(
                {
                    "alpha": alpha_target,
                    "real_s": float(t_real) if t_real is not None else None,
                    "sim_s": float(t_sim) if t_sim is not None else None,
                    "diff_s": float(t_sim - t_real) if (t_real is not None and t_sim is not None) else None,
                }
            )

        rows.append(
            {
                "COD_ENSAIO": c.get("COD_ENSAIO"),
                "T_C": c.get("T_C"),
                "comparisons": comparisons,
            }
        )

    return rows
