import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from services.theory_engine import hardness_prior


LOGGER = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_mp_code(raw_code: Any) -> Optional[str]:
    if raw_code is None:
        return None

    code = str(raw_code).strip()
    if not code:
        return None

    if code.lower().startswith("mp_"):
        code = code[3:]

    if re.fullmatch(r"\d+(\.0+)?", code):
        try:
            return str(int(float(code)))
        except ValueError:
            return None

    return code


def _normalize_formula(
    formula_base: Mapping[Any, Any],
) -> Tuple[Dict[str, float], Dict[str, Any], List[str]]:
    normalized: Dict[str, float] = {}
    original_key_by_code: Dict[str, Any] = {}
    ordered_codes: List[str] = []

    for raw_code, raw_phr in (formula_base or {}).items():
        code = _normalize_mp_code(raw_code)
        phr = _to_float(raw_phr)
        if code is None or phr is None:
            continue

        phr = max(0.0, float(phr))
        if code not in normalized:
            normalized[code] = phr
            original_key_by_code[code] = raw_code
            ordered_codes.append(code)
        else:
            normalized[code] += phr

    return normalized, original_key_by_code, ordered_codes


def _objective(
    x: np.ndarray,
    variable_codes: Sequence[str],
    formula_ref: Mapping[str, float],
    base_vector: np.ndarray,
    alvo_dureza: float,
    penalidade_mudanca: float,
) -> float:
    trial = dict(formula_ref)
    for idx, code in enumerate(variable_codes):
        trial[code] = max(0.0, float(x[idx]))

    hardness = float(hardness_prior(trial))
    erro_quadratico = (hardness - alvo_dureza) ** 2
    penalidade = penalidade_mudanca * float(np.abs(x - base_vector).sum())
    return float(erro_quadratico + penalidade)


def _random_search(
    x0: np.ndarray,
    variable_codes: Sequence[str],
    formula_ref: Mapping[str, float],
    base_vector: np.ndarray,
    alvo_dureza: float,
    penalidade_mudanca: float,
    maxiter: int,
    random_seed: Optional[int],
) -> Tuple[np.ndarray, float]:
    if x0.size == 0:
        cost = _objective(
            x0,
            variable_codes,
            formula_ref,
            base_vector,
            alvo_dureza,
            penalidade_mudanca,
        )
        return x0, cost

    rng = np.random.default_rng(random_seed)
    best_x = np.maximum(x0.astype(float), 0.0)
    best_cost = _objective(
        best_x,
        variable_codes,
        formula_ref,
        base_vector,
        alvo_dureza,
        penalidade_mudanca,
    )

    step = np.maximum(1.0, np.abs(x0) * 0.2)
    num_iter = max(200, int(maxiter) * 20)

    for _ in range(num_iter):
        candidate = best_x + rng.normal(loc=0.0, scale=step, size=best_x.shape)
        candidate = np.maximum(candidate, 0.0)

        candidate_cost = _objective(
            candidate,
            variable_codes,
            formula_ref,
            base_vector,
            alvo_dureza,
            penalidade_mudanca,
        )

        if candidate_cost < best_cost:
            best_x = candidate
            best_cost = candidate_cost
            step = np.maximum(step * 0.97, 0.05)
        else:
            step = np.maximum(step * 0.999, 0.05)

    return best_x, float(best_cost)


def otimizar_formula_dureza(
    formula_base: Mapping[Any, Any],
    alvo_dureza: float,
    restricoes: Sequence[Any],
    penalidade_mudanca: float = 0.25,
    maxiter: int = 300,
    random_seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """
    Sugere uma formula com PHRs ajustados para aproximar a dureza alvo.

    A funcao respeita:
    - MPs em `restricoes` (nao mudam)
    - PHR >= 0 para todas as MPs
    """
    base_formula, original_key_by_code, ordered_codes = _normalize_formula(formula_base)
    if not base_formula:
        raise ValueError("formula_base vazia ou sem valores de PHR validos.")

    alvo = _to_float(alvo_dureza)
    if alvo is None:
        raise ValueError("alvo_dureza invalido.")

    penalty = _to_float(penalidade_mudanca)
    if penalty is None:
        penalty = 0.25
    penalty = max(0.0, float(penalty))

    fixed_codes = {
        code
        for code in (_normalize_mp_code(item) for item in (restricoes or []))
        if code is not None
    }
    variable_codes = [code for code in ordered_codes if code not in fixed_codes]

    x0 = np.array([base_formula[code] for code in variable_codes], dtype=float)
    formula_ref = dict(base_formula)

    best_x = x0.copy()
    best_cost = _objective(best_x, variable_codes, formula_ref, x0, alvo, penalty)
    method_used = "none"
    success = True

    if variable_codes:
        used_scipy = False
        try:
            from scipy.optimize import minimize

            bounds = [(0.0, None)] * len(variable_codes)
            result = minimize(
                fun=_objective,
                x0=x0,
                args=(variable_codes, formula_ref, x0, alvo, penalty),
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": int(maxiter)},
            )
            used_scipy = True
            candidate = np.maximum(np.array(result.x, dtype=float), 0.0)
            candidate_cost = _objective(
                candidate, variable_codes, formula_ref, x0, alvo, penalty
            )

            if np.isfinite(candidate_cost) and candidate_cost <= best_cost:
                best_x = candidate
                best_cost = candidate_cost

            success = bool(result.success)
            method_used = "scipy.minimize(L-BFGS-B)"
        except Exception as exc:
            LOGGER.warning(
                "Falha no scipy.optimize.minimize para otimizar dureza: %s", exc
            )
            success = False

        if (not used_scipy) or (not success):
            random_x, random_cost = _random_search(
                x0=x0,
                variable_codes=variable_codes,
                formula_ref=formula_ref,
                base_vector=x0,
                alvo_dureza=alvo,
                penalidade_mudanca=penalty,
                maxiter=maxiter,
                random_seed=random_seed,
            )
            if random_cost <= best_cost:
                best_x = random_x
                best_cost = random_cost
            method_used = "random_search_fallback"
            success = True

    suggested = dict(base_formula)
    for idx, code in enumerate(variable_codes):
        suggested[code] = max(0.0, float(best_x[idx]))

    hardness_prevista = float(hardness_prior(suggested))
    delta_total = float(
        sum(abs(suggested.get(code, 0.0) - base_formula.get(code, 0.0)) for code in ordered_codes)
    )

    formula_sugerida: Dict[Any, float] = {}
    for code in ordered_codes:
        output_key = original_key_by_code.get(code, code)
        formula_sugerida[output_key] = float(suggested.get(code, 0.0))

    return {
        "formula_sugerida": formula_sugerida,
        "dureza_prevista": hardness_prevista,
        "alvo_dureza": float(alvo),
        "custo_final": float(best_cost),
        "delta_total_phr": delta_total,
        "penalidade_mudanca": float(penalty),
        "restricoes_aplicadas": sorted(fixed_codes),
        "variaveis_otimizadas": list(variable_codes),
        "metodo": method_used,
        "sucesso": bool(success),
    }

