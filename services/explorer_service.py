import logging
from threading import RLock
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors

from models.formula import Formula, FormulaItem
from models.usuario import db


LOGGER = logging.getLogger(__name__)


class FormulaExplorer:
    """
    Busca formulacoes similares com base em vetores PHR por materia-prima.
    """

    _VALID_METRICS = {"cosine", "euclidean"}

    def __init__(self, metric: str = "cosine"):
        metric_norm = str(metric or "").strip().lower()
        if metric_norm not in self._VALID_METRICS:
            raise ValueError(f"metric invalida: {metric}. Use 'cosine' ou 'euclidean'.")

        self.metric = metric_norm

        self._lock = RLock()
        self._nn_model: Optional[NearestNeighbors] = None
        self._feature_columns: List[int] = []
        self._formula_ids: List[int] = []
        self._vectors_df = pd.DataFrame()
        self.last_unknown_materials: List[int] = []

    @staticmethod
    def _normalize_material_code(raw_code) -> Optional[int]:
        if raw_code is None:
            return None

        token = str(raw_code).strip().lower()
        if not token:
            return None

        if token.startswith("mp_"):
            token = token[3:]

        try:
            value = float(token)
        except (TypeError, ValueError):
            return None

        if not value.is_integer():
            return None

        return int(value)

    @staticmethod
    def _to_float(raw_value) -> Optional[float]:
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    def _normalize_target(self, target_phr_dict: dict) -> Dict[int, float]:
        normalized: Dict[int, float] = {}
        for raw_code, raw_phr in (target_phr_dict or {}).items():
            code = self._normalize_material_code(raw_code)
            phr = self._to_float(raw_phr)
            if code is None or phr is None:
                continue
            normalized[code] = phr
        return normalized

    def _load_active_formula_ids(self) -> List[int]:
        rows = db.session.query(Formula.cd_produto).filter(Formula.ativo.is_(True)).all()
        return sorted(int(row[0]) for row in rows)

    def _load_active_items_dataframe(self) -> pd.DataFrame:
        rows = (
            db.session.query(
                FormulaItem.cd_produto.label("cd_produto"),
                FormulaItem.cd_materia_prima.label("cd_materia_prima"),
                FormulaItem.qt_phr.label("qt_phr"),
            )
            .join(Formula, Formula.cd_produto == FormulaItem.cd_produto)
            .filter(Formula.ativo.is_(True))
            .all()
        )

        if not rows:
            return pd.DataFrame(columns=["cd_produto", "cd_materia_prima", "qt_phr"])

        return pd.DataFrame(
            {
                "cd_produto": [int(row.cd_produto) for row in rows],
                "cd_materia_prima": [int(row.cd_materia_prima) for row in rows],
                "qt_phr": [float(row.qt_phr) for row in rows],
            }
        )

    def _active_material_codes(self) -> Set[int]:
        rows = (
            db.session.query(FormulaItem.cd_materia_prima)
            .join(Formula, Formula.cd_produto == FormulaItem.cd_produto)
            .filter(Formula.ativo.is_(True))
            .distinct()
            .all()
        )
        return {int(row[0]) for row in rows}

    def _refresh_vector_space_if_stale(self):
        active_formula_ids = self._load_active_formula_ids()
        cached_feature_set = set(self._feature_columns)
        active_materials = self._active_material_codes() if active_formula_ids else set()

        if active_formula_ids != self._formula_ids or active_materials != cached_feature_set:
            self._build_vector_space()

    def _build_vector_space(self):
        """
        Carrega formulas ativas, pivota por materia-prima (qt_phr) e treina o
        modelo de vizinhos mais proximos.
        """
        with self._lock:
            formula_ids = self._load_active_formula_ids()
            self._formula_ids = formula_ids
            self.last_unknown_materials = []

            if not formula_ids:
                self._vectors_df = pd.DataFrame()
                self._feature_columns = []
                self._nn_model = None
                return

            df_items = self._load_active_items_dataframe()
            if df_items.empty:
                vectors_df = pd.DataFrame(index=formula_ids, dtype=float)
            else:
                vectors_df = (
                    df_items.pivot_table(
                        index="cd_produto",
                        columns="cd_materia_prima",
                        values="qt_phr",
                        aggfunc="sum",
                    )
                    .fillna(0.0)
                )

            vectors_df = vectors_df.reindex(formula_ids, fill_value=0.0)
            vectors_df = vectors_df.sort_index(axis=1)

            self._vectors_df = vectors_df
            self._feature_columns = [int(col) for col in vectors_df.columns.tolist()]

            if not self._feature_columns:
                self._nn_model = None
                LOGGER.warning(
                    "FormulaExplorer: formulas ativas sem materias-primas; busca por similaridade retornara distancia 0."
                )
                return

            model = NearestNeighbors(metric=self.metric)
            model.fit(vectors_df.to_numpy(dtype=float))
            self._nn_model = model

    def find_similar(self, target_phr_dict: dict, n: int = 5):
        """
        Recebe {cod_mp: phr} e retorna top N formulas similares no formato:
        [
          {"formula_id": 1234, "distance": 0.12},
          ...
        ]
        """
        with self._lock:
            if self._vectors_df.empty and not self._formula_ids:
                self._build_vector_space()
            else:
                self._refresh_vector_space_if_stale()

            if not self._formula_ids:
                return []

            target_norm = self._normalize_target(target_phr_dict)
            if not target_norm:
                return []

            try:
                n_int = int(n)
            except (TypeError, ValueError):
                n_int = 5
            if n_int <= 0:
                return []

            feature_set = set(self._feature_columns)
            unknown_materials = sorted(
                code for code in target_norm.keys() if code not in feature_set
            )

            # Tratamento para materias-primas novas apos o cache ter sido montado:
            # se essas MPs ja existem nas formulas ativas, reconstrui o espaco vetorial.
            if unknown_materials:
                active_materials = self._active_material_codes()
                if any(code in active_materials for code in unknown_materials):
                    self._build_vector_space()
                    feature_set = set(self._feature_columns)
                    unknown_materials = sorted(
                        code for code in target_norm.keys() if code not in feature_set
                    )

            self.last_unknown_materials = unknown_materials
            if unknown_materials:
                LOGGER.info(
                    "FormulaExplorer: MPs fora do treino atual foram ignoradas na similaridade: %s",
                    unknown_materials,
                )

            n_neighbors = min(n_int, len(self._formula_ids))

            # Se nao houver colunas de MP no espaco vetorial, todas as formulas sao equivalentes.
            if self._nn_model is None or not self._feature_columns:
                return [
                    {"formula_id": int(formula_id), "distance": 0.0}
                    for formula_id in self._formula_ids[:n_neighbors]
                ]

            target_vector = pd.DataFrame(
                np.zeros((1, len(self._feature_columns)), dtype=float),
                columns=self._feature_columns,
            )

            for code, phr in target_norm.items():
                if code in target_vector.columns:
                    target_vector.at[0, code] = float(phr)

            distances, indices = self._nn_model.kneighbors(
                target_vector.to_numpy(dtype=float),
                n_neighbors=n_neighbors,
            )

            results = []
            for distance, index in zip(distances[0], indices[0]):
                results.append(
                    {
                        "formula_id": int(self._formula_ids[int(index)]),
                        "distance": float(distance),
                    }
                )

            return results
