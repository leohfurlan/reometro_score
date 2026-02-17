import csv
import logging
import os
import re
from typing import Any, Dict, List, Optional


LOGGER = logging.getLogger(__name__)

_DEFAULT_DATASET_CANDIDATES = (
    "/mnt/data/conhecimento_teorico.csv",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "conhecimento_teorico.csv")),
)

_TARGET_HARDNESS_COLUMNS = {"dureza", "hardness", "hardness_target", "shore_a"}


def _coerce_float(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    txt = str(raw_value).strip().replace(",", ".")
    if txt == "":
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _header_to_code(header_name: str) -> Optional[int]:
    token = str(header_name or "").strip().strip('"').replace(",", ".")
    if not token:
        return None
    if re.fullmatch(r"\d+(\.0+)?", token):
        try:
            return int(float(token))
        except ValueError:
            return None
    return None


def _split_semicolon_row(row: List[str]) -> List[str]:
    if not row:
        return []

    # Some source files wrap the full line in quotes:
    # "100;0;0;...". In that case csv.reader returns a single item.
    if len(row) == 1 and ";" in row[0]:
        row = row[0].split(";")

    return [str(item).strip().strip('"') for item in row]


def _resolve_dataset_path(csv_path: Optional[str]) -> Optional[str]:
    if csv_path:
        return csv_path if os.path.exists(csv_path) else None

    for candidate in _DEFAULT_DATASET_CANDIDATES:
        if os.path.exists(candidate):
            return candidate

    return None


def load_theoretical_formulations(csv_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads theoretical formulations from CSV and returns:
    [
      {
        "formulation_phr": {2276: 100.0, 528: 40.0, ...},
        "target_hardness": 61.0
      },
      ...
    ]
    """
    resolved_path = _resolve_dataset_path(csv_path)
    if not resolved_path:
        LOGGER.warning("Dataset teorico nao encontrado: %s", csv_path or _DEFAULT_DATASET_CANDIDATES)
        return []

    header = None
    rows: List[List[str]] = []

    last_error = None
    for encoding in ("utf-8", "latin1"):
        try:
            with open(resolved_path, "r", encoding=encoding, newline="") as fh:
                reader = csv.reader(fh, delimiter=";", quotechar='"')
                for row in reader:
                    parsed = _split_semicolon_row(row)
                    if not parsed or all(not cell for cell in parsed):
                        continue
                    if header is None:
                        header = parsed
                    else:
                        rows.append(parsed)
            last_error = None
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            header = None
            rows = []
            continue
        except Exception as exc:
            LOGGER.warning("Falha ao ler dataset teorico em %s: %s", resolved_path, exc)
            return []

    if last_error is not None:
        LOGGER.warning("Falha de encoding ao ler dataset teorico em %s: %s", resolved_path, last_error)
        return []

    if not header:
        return []

    code_columns: Dict[int, int] = {}
    target_col_index = None

    for col_idx, raw_name in enumerate(header):
        col_name = str(raw_name or "").strip().strip('"')
        col_name_norm = col_name.lower()

        code = _header_to_code(col_name)
        if code is not None:
            code_columns[col_idx] = code
            continue

        if col_name_norm in _TARGET_HARDNESS_COLUMNS:
            target_col_index = col_idx

    formulations: List[Dict[str, Any]] = []
    for raw_row in rows:
        formulation_phr: Dict[int, float] = {}

        for col_idx, code in code_columns.items():
            value = _coerce_float(raw_row[col_idx] if col_idx < len(raw_row) else None)
            if value is None or value == 0.0:
                continue
            formulation_phr[code] = float(value)

        target_hardness = None
        if target_col_index is not None:
            target_hardness = _coerce_float(
                raw_row[target_col_index] if target_col_index < len(raw_row) else None
            )

        formulations.append(
            {
                "formulation_phr": formulation_phr,
                "target_hardness": target_hardness,
            }
        )

    LOGGER.info(
        "Dataset teorico carregado: %s cenarios de simulacao (arquivo: %s)",
        len(formulations),
        resolved_path,
    )

    return formulations
