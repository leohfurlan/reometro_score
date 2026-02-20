# Motor de Formulacao Preditivo (v2)

## Escopo

O motor de formulacao v2 adiciona:

- modelagem de dados dedicada (`Formulation`, `FormulationIngredient`, `ProcessParameters`, `MeasuredProperties`);
- pipeline de features fisico-quimico + processo;
- previsao multioutput (`hardness`, `tensile`, `elongation`, `abrasion`, `ts2`, `t90`);
- otimizacao multiobjetivo com fronteira de Pareto;
- explainability com SHAP;
- active learning para sugerir proximos experimentos.

## Persistencia e versionamento

Artefatos em `instance/formulation_ml/`:

- `models/formulation_model_<versao>.pkl` (modelo serializado + pipeline + perfis de residuo);
- `metrics_<versao>.json` (metricas globais e por alvo);
- `feature_importance_<versao>.json` (importancias globais e por alvo);
- `model_manifest_<versao>.json` (metadados do treino);
- `model_registry.json` (versao ativa e historico);
- `events/*.jsonl` (audit trail de treino/predict/optimize/explain/active learning).

## Endpoints

### `POST /api/formulation/train`

Treina e ativa um novo modelo.

Body (opcional):

```json
{
  "min_samples": 20,
  "include_legacy": true
}
```

### `POST /predict-formulation`

Entrada:

```json
{
  "formulation": {
    "ingredients": [
      {"material_code": "491", "phr": 70.0},
      {"material_code": "528", "phr": 45.0},
      {"material_code": "542", "phr": 12.0}
    ]
  },
  "process_parameters": {
    "mixing_temp_c": 110,
    "curing_temp_c": 172,
    "curing_time_min": 12
  }
}
```

Saida: propriedades previstas com intervalo de confianca e sinais de objetivo (`cost`, `risk`, `resistance`).

### `POST /optimize-formulation`

Entrada:

```json
{
  "formulation": {"ingredients": [{"material_code": "491", "phr": 70.0}]},
  "process_parameters": {"curing_temp_c": 170, "curing_time_min": 12},
  "technical_constraints": {"hardness": {"min": 55, "max": 70}},
  "search_config": {"population_size": 900, "mutation_scale": 0.18},
  "weights": {"cost": 0.4, "risk": 0.3, "resistance": 0.3}
}
```

Saida:

- `top_candidates` (top 10);
- `pareto_frontier` (fronteira nao-dominada);
- resumo de factibilidade.

### `POST /explain-formulation`

Mesmo payload base de `predict-formulation`, com `top_n` opcional.

Saida:

- explicacao SHAP por alvo;
- ranking global de features mais influentes.

### `GET /suggest-next-experiments`

Query params:

- `top_n` (default 10)
- `candidate_pool_size` (default 260)
- `auto_train_if_missing` (default true)

Saida:

- lista de experimentos candidatos com `acquisition_score` (incerteza + novidade).
