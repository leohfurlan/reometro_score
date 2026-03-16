# Simulation Engines, Flags and Output Contract

## Engine status and policy

| Engine | Status | Default | Notes |
| --- | --- | --- | --- |
| `empirical_v1` | `stable` | Yes | Legacy production engine. |
| `thermo_kinetic_v2` | `experimental` | No | New thermo-kinetic engine. |
| `axisymmetric_fipy` | `prototype` | No | Axisymmetric prototype, not promoted to production flow. |

The default engine is protected by a guard rail: only `stable` engines can become default.  
If configuration points to an `experimental` or `prototype` engine, the system falls back to `empirical_v1`.

## Feature flags

Central configuration is implemented in `services/engine_registry.py`.

- `REOMETRIA_DEFAULT_ENGINE`  
  Requested default engine key. Only applied when the selected engine is `stable` and enabled.
- `REOMETRIA_ENABLE_THERMO_KINETIC_V2`  
  Enables/disables explicit execution of `thermo_kinetic_v2` (`true` by default).
- `REOMETRIA_ENABLE_AXISYMMETRIC_FIPY`  
  Enables/disables explicit selection of `axisymmetric_fipy` (`false` by default).

Selection rule:

1. No explicit engine selection -> run default engine (`empirical_v1`).
2. Explicit selection -> run selected engine only if enabled.
3. Disabled/invalid selection -> fallback to default engine.

## Unified output schema

Normalization is implemented in `services/simulation_schema.py` through `normalize_simulation_output(...)`.
This is an adapter layer: existing persisted files are still read as-is, then normalized in-memory.

### Required fields

- `schema_version`
- `engine`
- `engine_status`
- `geometry_type`
- `coordinates`
- `times`
- `temperature_snapshots`
- `alpha_c`
- `alpha_r`
- `alpha`
- `heat_source`
- `metrics`
- `demold_time`
- `process_state`
- `process_state_over_time`
- `process_state_final`

### Optional fields

- `sim_id`
- `fit_id`
- `mode`
- `dim`
- `shape`
- `full_shape`
- `dx`
- `dx_compute`
- `dt`
- `t_end`
- `geometry_metadata`
- `induction_confidence`
- `induction_source`
- `induction_fit_quality`
- `temperature_validity_range`
- `induction_extrapolation_warning`
- `induction_model_regime`
- `quality_status`
- `clip_events_count`
- `nan_recovery_events`
- `numerical_warnings`

Backward compatibility behavior:

- Legacy payloads without `alpha_c`/`alpha_r` are adapted (`alpha_c = alpha`, `alpha_r = 0`).
- Missing `heat_source` is adapted with zeros.
- Missing process state timeline is adapted to `HEATING`.

## Validation suite runner

Official local command (informative mode):

```bash
python -m services.run_v2_validation_suite --quick --gate-mode informative --out-dir data/out --report-stem v2_validation_report
```

Strict local command (fails gate when critical quality is found):

```bash
python -m services.run_v2_validation_suite --quick --gate-mode strict --out-dir data/out --report-stem v2_validation_report
```

Compatibility wrapper (legacy path still supported):

```bash
python scripts/run_v2_validation_suite.py --quick
```

Outputs:

- JSON report: `data/out/<report_stem>.json`
- CSV report: `data/out/<report_stem>.csv`

The report now includes:

- `quality_gate_mode` (`informative` or `strict`)
- `quality_status_counts`
- `has_critical_quality`
- `ci_gate_passed`

## Official quality thresholds (v2)

Thresholds are produced by the solver (`services/vulcanization_service_v2.py`) and reused by validation:

| Metric | Info | Warning | Critical |
| --- | --- | --- | --- |
| `clip_events_count` | `>= 1` | `>= 25` | `>= 120` |
| `nan_recovery_events` | `>= 1` | `>= 1` | `>= 6` |
| `max_source_to_diffusion_dT_ratio` | `>= 5.0` | `>= 9.0` | `>= 16.0` |
| `substeps_max_per_step` | `>= 4` | `>= 8` | `>= 16` |
| `stiffness_alert_count` | `>= 1` | `>= 2` | `>= 4` |
| Induction extrapolation | unknown range (`info`) | mild extrapolation | strong extrapolation |

Quality fields available in solver metrics:

- `quality_flags` (code, severity, value, thresholds, message)
- `quality_status` (`healthy`, `info`, `warning`, `critical`)
- `quality_severity_breakdown`

Critical-step diagnostics available in solver metrics:

- `critical_step_reason`
- `critical_step_context`
- `critical_step_metrics`
- `critical_steps_trace` (top-N critical steps)

## CI execution

Workflow: `.github/workflows/validation-suite.yml`

CI does:

1. Runs focused hardening tests (engines, schema, route dispatch, v2 suite).
2. Runs `python -m services.run_v2_validation_suite --quick --gate-mode <mode>`.
   - Mode is driven by env/variable `V2_VALIDATION_GATE_MODE`.
   - Default behavior is `informative` when the variable is empty.
3. Publishes artifacts:
   - `v2_validation_ci.json`
   - `v2_validation_ci.csv`
   - `summary.txt`
   - `quality_summary.json`
4. Fails pipeline only when mode is `strict` and `ci_gate_passed` is `false`.
