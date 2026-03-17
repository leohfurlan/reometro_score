import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np


def _safe_float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return float(out)


def _to_float_array(values: Any) -> np.ndarray:
    if values is None:
        return np.asarray([], dtype=float)
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        arr = np.ravel(arr)
    arr = arr[np.isfinite(arr)]
    return arr.astype(float, copy=False)


def _temperature_from_curve(raw: Dict[str, Any]):
    temp_k = _safe_float(
        raw.get("temperature_K", raw.get("temperature_k", raw.get("T_K"))),
        default=None,
    )
    temp_c = _safe_float(
        raw.get("temperature_C", raw.get("temperature_c", raw.get("T_C"))),
        default=None,
    )
    if temp_k is None and temp_c is not None:
        temp_k = temp_c + 273.15
    if temp_c is None and temp_k is not None:
        temp_c = temp_k - 273.15
    if temp_k is None:
        return None, None
    return float(temp_c), float(temp_k)


def _normalize_curve(raw_curve: Dict[str, Any], idx: int):
    if not isinstance(raw_curve, dict):
        raise ValueError(f"Curva {idx}: formato invalido (esperado objeto).")

    temp_c, temp_k = _temperature_from_curve(raw_curve)
    if temp_k is None:
        raise ValueError(f"Curva {idx}: temperatura ausente.")

    time_vec = _to_float_array(
        raw_curve.get(
            "time",
            raw_curve.get("time_vector", raw_curve.get("tempo", raw_curve.get("t_rel"))),
        )
    )
    torque_vec = _to_float_array(raw_curve.get("torque", raw_curve.get("torque_vector")))

    size = min(time_vec.size, torque_vec.size)
    if size < 3:
        raise ValueError(f"Curva {idx}: pontos insuficientes (minimo=3).")

    time_vec = np.maximum.accumulate(np.maximum(time_vec[:size], 0.0))
    torque_vec = torque_vec[:size]

    # Remove duplicidades em tempo preservando estabilidade numerica.
    keep = np.concatenate(([True], np.diff(time_vec) > 1e-12))
    time_vec = time_vec[keep]
    torque_vec = torque_vec[keep]
    if time_vec.size < 3:
        raise ValueError(f"Curva {idx}: tempos duplicados apos limpeza.")

    return {
        "curve_id": raw_curve.get("curve_id", raw_curve.get("id", f"curve_{idx:03d}")),
        "temperature_C": float(temp_c),
        "temperature_K": float(temp_k),
        "time": time_vec.tolist(),
        "torque": torque_vec.tolist(),
    }


def _load_json_dataset(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for key in ("dataset", "curves", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        raise ValueError("JSON invalido: esperado lista de curvas ou chave 'dataset'.")
    return raw


def _load_csv_dataset(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        rows = list(reader)

    if not rows:
        raise ValueError("CSV vazio.")

    groups = {}
    for row in rows:
        temp_k = _safe_float(row.get("temperature_K", row.get("temperature_k", row.get("T_K"))), default=None)
        temp_c = _safe_float(row.get("temperature_C", row.get("temperature_c", row.get("T_C"))), default=None)
        if temp_k is None and temp_c is not None:
            temp_k = temp_c + 273.15
        if temp_k is None:
            raise ValueError("CSV sem coluna de temperatura reconhecida.")

        time_val = _safe_float(row.get("time", row.get("time_vector", row.get("tempo"))), default=None)
        torque_val = _safe_float(row.get("torque", row.get("torque_vector")), default=None)
        if time_val is None or torque_val is None:
            continue

        key = float(temp_k)
        groups.setdefault(
            key,
            {
                "temperature_K": float(temp_k),
                "temperature_C": float(temp_k - 273.15),
                "time": [],
                "torque": [],
            },
        )
        groups[key]["time"].append(float(time_val))
        groups[key]["torque"].append(float(torque_val))

    if not groups:
        raise ValueError("CSV sem linhas validas de tempo/torque.")
    return list(groups.values())


def _load_npz_dataset(path: Path):
    blob = np.load(path, allow_pickle=True)
    files = set(blob.files)

    if {"temperature_K", "time", "torque"}.issubset(files):
        temp = np.asarray(blob["temperature_K"], dtype=float)
        time = np.asarray(blob["time"], dtype=object)
        torque = np.asarray(blob["torque"], dtype=object)
        size = min(len(temp), len(time), len(torque))
        out = []
        for i in range(size):
            out.append(
                {
                    "curve_id": f"curve_{i:03d}",
                    "temperature_K": float(temp[i]),
                    "temperature_C": float(temp[i] - 273.15),
                    "time": np.asarray(time[i], dtype=float).tolist(),
                    "torque": np.asarray(torque[i], dtype=float).tolist(),
                }
            )
        return out

    raise ValueError("NPZ invalido: esperado arrays 'temperature_K', 'time' e 'torque'.")


def _ensure_multitemperature(dataset: Iterable[Dict[str, Any]]):
    temps = [round(float(c["temperature_K"]), 6) for c in dataset]
    if len(set(temps)) < 2:
        raise ValueError("Dataset deve conter multiplas temperaturas (>=2).")


def load_reometry_dataset(path) -> List[Dict[str, Any]]:
    """
    Carrega dataset reometrico real e normaliza em formato unico.

    Formato de saida:
    [
      {
        "temperature_C": 150.0,
        "temperature_K": 423.15,
        "time": [...],
        "torque": [...]
      },
      ...
    ]
    """
    dataset_path = Path(path).expanduser().resolve()
    if not dataset_path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {dataset_path}")

    suffix = dataset_path.suffix.lower()
    if suffix == ".json":
        raw_curves = _load_json_dataset(dataset_path)
    elif suffix == ".csv":
        raw_curves = _load_csv_dataset(dataset_path)
    elif suffix == ".npz":
        raw_curves = _load_npz_dataset(dataset_path)
    else:
        raise ValueError("Formato nao suportado. Use .json, .csv ou .npz")

    normalized = [_normalize_curve(curve, idx=i + 1) for i, curve in enumerate(raw_curves)]
    _ensure_multitemperature(normalized)
    return normalized


__all__ = ["load_reometry_dataset"]
