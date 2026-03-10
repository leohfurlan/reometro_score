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
KINETIC_MODEL_VERSION = 2
KINETIC_MODEL_EXPRESSION = "alpha = k(T) * t^n / (1 + k(T) * t^n)"


def _nearest_alpha_time(time_vec, alpha_vec, alpha_target):
    size = min(len(time_vec), len(alpha_vec))
    if size == 0:
        return None

    t = np.asarray(time_vec[:size], dtype=float)
    a = np.asarray(alpha_vec[:size], dtype=float)
    idx = int(np.argmin(np.abs(a - float(alpha_target))))
    return float(t[idx])


def _crossing_or_nearest_time(time_vec, alpha_vec, alpha_target):
    t_cross = first_crossing_time(time_vec, alpha_vec, alpha_target)
    if t_cross is not None:
        return float(t_cross)
    return _nearest_alpha_time(time_vec, alpha_vec, alpha_target)


def _normalize_fit_payload_model(payload):
    model_version = int(payload.get("kinetic_model_version") or 1)
    if model_version >= KINETIC_MODEL_VERSION:
        payload["kinetic_model_version"] = KINETIC_MODEL_VERSION
        payload["kinetic_model_expression"] = KINETIC_MODEL_EXPRESSION
        return payload, False

    # Legacy model (v1): alpha = (k(T)*t)^n / (1 + (k(T)*t)^n)
    # Current model (v2): alpha = k(T)*t^n / (1 + k(T)*t^n)
    # Mapping that preserves isothermal behavior:
    # k0_v2 = k0_v1^n ; Ea_v2 = Ea_v1 * n ; n_v2 = n_v1
    n_safe = float(np.clip(float(payload.get("n", 1.0)), 0.5, 12.0))
    k0_legacy = max(float(payload.get("k0", 1e-8)), 1e-300)
    ea_legacy = float(payload.get("Ea", 80000.0))

    ln_k0_new = np.clip(n_safe * np.log(k0_legacy), -700.0, 700.0)
    payload["k0"] = float(np.exp(ln_k0_new))
    payload["Ea"] = float(ea_legacy * n_safe)
    payload["n"] = n_safe
    payload["kinetic_model_version"] = KINETIC_MODEL_VERSION
    payload["kinetic_model_expression"] = KINETIC_MODEL_EXPRESSION
    payload["kinetic_model_migrated_from"] = model_version
    return payload, True


def _refresh_alpha_model_curves(payload):
    curves = payload.get("curves") or []
    for c in curves:
        t = np.array(c.get("t_rel") or [], dtype=float)
        tk = np.full_like(t, fill_value=float(c.get("T_C") or 0) + 273.15)
        pred = alpha_model(t, tk, payload["k0"], payload["Ea"], payload["n"])
        c["alpha_model"] = pred.tolist()


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
        "kinetic_model_version": KINETIC_MODEL_VERSION,
        "kinetic_model_expression": KINETIC_MODEL_EXPRESSION,
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
        _refresh_alpha_model_curves(payload)

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
        payload = json.load(f)

    payload, migrated = _normalize_fit_payload_model(payload)
    if payload.get("success"):
        _refresh_alpha_model_curves(payload)
    if migrated:
        save_fit_payload(payload)

    return payload


def update_fit_parameters(fit_id, k0, ea, n):
    payload = load_fit_payload(fit_id)
    if not payload:
        return None

    k0_val = float(np.clip(float(k0), 1e-8, 1e15))
    ea_val = float(np.clip(float(ea), 50000.0, 250000.0))
    n_val = float(np.clip(float(n), 0.5, 12.0))

    payload["k0"] = k0_val
    payload["Ea"] = ea_val
    payload["n"] = n_val
    payload["kinetic_model_version"] = KINETIC_MODEL_VERSION
    payload["kinetic_model_expression"] = KINETIC_MODEL_EXPRESSION

    _refresh_alpha_model_curves(payload)

    save_fit_payload(payload)
    return payload


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

        can_eval_real = len(t_rel) >= 1 and len(t_rel) == len(alpha_real)
        can_eval_sim = alpha_sim is not None and len(t_rel) >= 1 and len(t_rel) == len(alpha_sim)

        comparisons = []
        for alpha_target in alpha_targets:
            t_real = _crossing_or_nearest_time(t_rel, alpha_real, alpha_target) if can_eval_real else None
            t_sim = _crossing_or_nearest_time(t_rel, alpha_sim, alpha_target) if can_eval_sim else None
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
