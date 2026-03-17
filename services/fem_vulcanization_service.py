import math
from dataclasses import asdict

import numpy as np

from services.mechanistic_kinetics import (
    MechanisticKinetics,
    MechanisticKineticsParams,
)
from services.process_state_machine import (
    ProcessState,
    ProcessThermalConfig,
    VulcanizationProcessStateMachine,
)
from services.thermo_solver import (
    CP_RUBBER,
    LAMBDA_RUBBER,
    QV_VULCANIZATION,
    RHO_RUBBER,
    reaction_heat_source,
)
from services.kinetic_model_service import (
    MODEL_FAMILY_LEROY2013_CONTINUOUS_V1,
    extract_model_parameters,
    normalize_model_family,
)

try:
    from fipy import CellVariable, CylindricalGrid2D, DiffusionTerm, TransientTerm
except Exception:  # pragma: no cover - handled explicitly in run_fem_simulation()
    CellVariable = None
    CylindricalGrid2D = None
    DiffusionTerm = None
    TransientTerm = None


def _temperature_profile(t_s, mold_temperature_c, ramp_rate_c_per_s):
    return float(mold_temperature_c) + (float(ramp_rate_c_per_s) * float(t_s))


def _apply_convective_boundary_cells(
    temp_k,
    boundary_cell_ids,
    dt,
    rho,
    cp,
    h_conv,
    ambient_k,
    char_length,
):
    if boundary_cell_ids.size == 0:
        return

    beta = (2.0 * float(h_conv) * float(dt)) / (
        max(float(rho), 1e-9) * max(float(cp), 1e-9) * max(float(char_length), 1e-12)
    )
    relax = float(beta / (1.0 + max(beta, 0.0)))
    values = np.asarray(temp_k.value, dtype=float).copy()
    values[boundary_cell_ids] = values[boundary_cell_ids] + (relax * (float(ambient_k) - values[boundary_cell_ids]))
    temp_k.setValue(values)


def _boundary_cell_ids(mesh):
    exterior_faces = np.asarray(mesh.exteriorFaces.value, dtype=bool)
    face_cell_ids = np.asarray(mesh.faceCellIDs[0], dtype=int)
    ids = face_cell_ids[exterior_faces]
    ids = ids[ids >= 0]
    return np.unique(ids.astype(int))


def _build_kinetics(config):
    defaults = MechanisticKineticsParams()
    merged = asdict(defaults)

    kinetics_params = dict(config.get("kinetics_params") or {})
    fit_payload = dict(config.get("fit_payload") or {})

    family = normalize_model_family(fit_payload.get("model_family"))
    fit_params = extract_model_parameters(fit_payload, model_family=family)

    if family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1:
        av1 = float(max(fit_params.get("Av1", merged["Ac"]), 0.0))
        av2 = float(max(fit_params.get("Av2", 0.0), 0.0))
        merged["Ac"] = av1
        merged["Eac"] = float(max(fit_params.get("Ev", merged["Eac"]), 0.0))
        merged["Kn"] = float(np.clip(av2 / max(av1, 1e-12), 0.0, 1000.0))
        merged["Nn"] = 2.0
        merged["Ar"] = float(max(fit_params.get("Ar", merged["Ar"]), 0.0))
        merged["Ear"] = float(max(fit_params.get("Er", merged["Ear"]), 0.0))
        merged["Kx"] = 1.0
        merged["Nx"] = 1.0
    else:
        if fit_params.get("k0") is not None:
            merged["Ac"] = float(fit_params["k0"])
        elif fit_params.get("k_ref") is not None:
            merged["Ac"] = float(fit_params["k_ref"])
        if fit_params.get("Ea") is not None:
            merged["Eac"] = float(fit_params["Ea"])
        if fit_params.get("n") is not None:
            merged["Nn"] = float(fit_params["n"])

    for key in merged:
        if key in kinetics_params and kinetics_params[key] is not None:
            merged[key] = kinetics_params[key]

    return MechanisticKinetics(MechanisticKineticsParams(**merged))


def _validated_config(config):
    cfg = {
        "nr": 24,
        "nz": 24,
        "radius_m": 0.01,
        "height_m": 0.01,
        "dt": 0.25,
        "t_end": 180.0,
        "snapshot_every": 5,
        "mold_temp_c": 170.0,
        "init_temp_c": 25.0,
        "ramp_rate_c_per_s": 0.0,
        "ambient_temperature_c": 25.0,
        "h_mold": 10000.0,
        "h_air": 11.4,
        "demold_alpha_mean_threshold": 0.9,
        "rho": RHO_RUBBER,
        "cp": CP_RUBBER,
        "lambda_rubber": LAMBDA_RUBBER,
        "qv": QV_VULCANIZATION,
        "exotherm_enabled": True,
        "fit_payload": {},
        "kinetics_params": {},
    }
    if config:
        cfg.update(dict(config))

    cfg["nr"] = max(int(cfg["nr"]), 2)
    cfg["nz"] = max(int(cfg["nz"]), 2)
    cfg["radius_m"] = float(max(cfg["radius_m"], 1e-6))
    cfg["height_m"] = float(max(cfg["height_m"], 1e-6))
    cfg["dt"] = float(max(cfg["dt"], 1e-6))
    cfg["t_end"] = float(max(cfg["t_end"], cfg["dt"]))
    cfg["snapshot_every"] = max(int(cfg["snapshot_every"]), 1)
    cfg["rho"] = float(max(cfg["rho"], 1e-9))
    cfg["cp"] = float(max(cfg["cp"], 1e-9))
    cfg["lambda_rubber"] = float(max(cfg["lambda_rubber"], 1e-12))
    cfg["qv"] = float(max(cfg["qv"], 0.0))
    return cfg


def run_fem_simulation(config):
    """
    Minimal 2D axisymmetric prototype using FiPy (r-z cylindrical section).

    Solves:
      rho*cp*dT/dt = lambda*laplacian(T) + q_source
      d(alpha_c)/dt, d(alpha_r)/dt from mechanistic kinetics
      q_source = rho*qv*max(d(alpha_c)/dt, 0)

    Returns snapshots and metadata in-memory (no UI/persistence integration yet).
    """
    if CylindricalGrid2D is None:
        raise ImportError(
            "FiPy is required for run_fem_simulation(). "
            "Add/Install dependency 'fipy>=3.4'."
        )

    cfg = _validated_config(config)
    kinetics = _build_kinetics(cfg)
    fit_payload = dict(cfg.get("fit_payload") or {})
    model_family = normalize_model_family(fit_payload.get("model_family"))
    fit_params = extract_model_parameters(fit_payload, model_family=model_family)
    use_leroy = model_family == MODEL_FAMILY_LEROY2013_CONTINUOUS_V1
    leroy_x = float(np.clip(fit_params.get("X", 0.7), 1e-4, 1.0 - 1e-4))
    process_cfg = ProcessThermalConfig(
        mold_temperature_c=float(cfg["mold_temp_c"]),
        ambient_temperature_c=float(cfg["ambient_temperature_c"]),
        h_mold=float(cfg["h_mold"]),
        h_air=float(cfg["h_air"]),
        demold_alpha_mean_threshold=float(cfg["demold_alpha_mean_threshold"]),
    )
    process_sm = VulcanizationProcessStateMachine(process_cfg)

    nr = int(cfg["nr"])
    nz = int(cfg["nz"])
    dr = float(cfg["radius_m"]) / float(nr)
    dz = float(cfg["height_m"]) / float(nz)
    char_length = min(dr, dz)
    dt = float(cfg["dt"])
    steps = int(max(math.ceil(float(cfg["t_end"]) / dt), 1))

    mesh = CylindricalGrid2D(dr=dr, dz=dz, nr=nr, nz=nz)
    n_cells = int(mesh.numberOfCells)

    temp_k = CellVariable(mesh=mesh, name="temperature", value=float(cfg["init_temp_c"]) + 273.15)
    q_source = CellVariable(mesh=mesh, name="q_source", value=0.0)
    equation = (
        TransientTerm(coeff=float(cfg["rho"]) * float(cfg["cp"]), var=temp_k)
        == DiffusionTerm(coeff=float(cfg["lambda_rubber"]), var=temp_k) + q_source
    )

    boundary_ids = _boundary_cell_ids(mesh)
    cell_volumes = np.asarray(mesh.cellVolumes, dtype=float)
    if cell_volumes.size != n_cells:
        cell_volumes = np.ones((n_cells,), dtype=float)

    alpha_c0 = float(np.clip(kinetics.params.alpha_c0, 0.0, 1.0))
    alpha_r0 = float(np.clip(kinetics.params.alpha_r0, 0.0, alpha_c0))
    alpha_c = np.full((n_cells,), alpha_c0, dtype=float)
    alpha_r = np.full((n_cells,), alpha_r0, dtype=float)
    alpha = np.clip(alpha_c - alpha_r, 0.0, 1.0)
    q_source_vals = np.zeros((n_cells,), dtype=float)

    times = []
    t_snaps = []
    alpha_c_snaps = []
    alpha_r_snaps = []
    alpha_unstable_snaps = []
    alpha_snaps = []
    q_source_snaps = []
    process_state_over_time = []

    store_stride = int(cfg["snapshot_every"])
    for step in range(steps + 1):
        t_now = min(step * dt, float(cfg["t_end"]))

        if step > 0:
            alpha_c_next, alpha_r_next, _ = kinetics.step_explicit(
                T=np.asarray(temp_k.value, dtype=float),
                alpha_c=alpha_c,
                alpha_r=alpha_r,
                dt=dt,
            )
            alpha_c_rate = np.maximum((alpha_c_next - alpha_c) / max(dt, 1e-12), 0.0)
            if bool(cfg["exotherm_enabled"]) and float(cfg["qv"]) > 0.0:
                q_source_vals = reaction_heat_source(alpha_c_rate, rho=float(cfg["rho"]), qv=float(cfg["qv"]))
            else:
                q_source_vals.fill(0.0)
            q_source.setValue(q_source_vals)

            equation.solve(var=temp_k, dt=dt)

            if process_sm.state == ProcessState.HEATING:
                h_conv = float(cfg["h_mold"])
                ambient_k = _temperature_profile(
                    t_s=t_now,
                    mold_temperature_c=float(cfg["mold_temp_c"]),
                    ramp_rate_c_per_s=float(cfg["ramp_rate_c_per_s"]),
                ) + 273.15
            else:
                h_conv = float(cfg["h_air"])
                ambient_k = float(cfg["ambient_temperature_c"]) + 273.15

            _apply_convective_boundary_cells(
                temp_k=temp_k,
                boundary_cell_ids=boundary_ids,
                dt=dt,
                rho=float(cfg["rho"]),
                cp=float(cfg["cp"]),
                h_conv=h_conv,
                ambient_k=ambient_k,
                char_length=char_length,
            )

            temp_k.setValue(np.nan_to_num(temp_k.value, nan=0.0, posinf=1e6, neginf=0.0))
            alpha_c = np.clip(alpha_c_next, 0.0, 1.0)
            alpha_r = np.clip(alpha_r_next, 0.0, 1.0)
            alpha_r = np.minimum(alpha_r, alpha_c)
            alpha = np.clip(alpha_c - alpha_r, 0.0, 1.0)
            mean_alpha = float(np.sum(alpha * cell_volumes) / max(np.sum(cell_volumes), 1e-12))
            process_sm.update(mean_alpha=mean_alpha, t_now=t_now)

        should_store = (step == 0) or (step == steps) or (step % store_stride == 0)
        if should_store:
            times.append(float(t_now))
            t_snaps.append(np.asarray(temp_k.value, dtype=np.float32).copy())
            alpha_c_snaps.append(np.asarray(alpha_c, dtype=np.float32).copy())
            alpha_r_snaps.append(np.asarray(alpha_r, dtype=np.float32).copy())
            if use_leroy:
                alpha_unstable = np.clip(((1.0 - leroy_x) * alpha_c) - alpha_r, 0.0, 1.0)
            else:
                alpha_unstable = np.zeros_like(alpha, dtype=float)
            alpha_unstable_snaps.append(np.asarray(alpha_unstable, dtype=np.float32).copy())
            alpha_snaps.append(np.asarray(alpha, dtype=np.float32).copy())
            q_source_snaps.append(np.asarray(q_source_vals, dtype=np.float32).copy())
            process_state_over_time.append(process_sm.state.value)

    return {
        "model_family": model_family,
        "solver_family": "fipy_axisymmetric_2d",
        "geometry": "axisymmetric_cylindrical_section",
        "times": np.asarray(times, dtype=np.float64),
        "t_snaps": np.asarray(t_snaps, dtype=np.float32),
        "alpha_c_snaps": np.asarray(alpha_c_snaps, dtype=np.float32),
        "alpha_r_snaps": np.asarray(alpha_r_snaps, dtype=np.float32),
        "alpha_unstable_snaps": np.asarray(alpha_unstable_snaps, dtype=np.float32),
        "alpha_snaps": np.asarray(alpha_snaps, dtype=np.float32),
        "heat_source_snaps": np.asarray(q_source_snaps, dtype=np.float32),
        "process_state_over_time": np.asarray(process_state_over_time, dtype="<U16"),
        "demold_time": process_sm.demold_time,
        "mesh": {
            "nr": nr,
            "nz": nz,
            "n_cells": n_cells,
            "radius_m": float(cfg["radius_m"]),
            "height_m": float(cfg["height_m"]),
            "dr": dr,
            "dz": dz,
            "cell_centers_r": np.asarray(mesh.cellCenters[0], dtype=np.float64),
            "cell_centers_z": np.asarray(mesh.cellCenters[1], dtype=np.float64),
        },
        "materials": {
            "rho": float(cfg["rho"]),
            "cp": float(cfg["cp"]),
            "lambda_rubber": float(cfg["lambda_rubber"]),
            "qv": float(cfg["qv"]),
            "exotherm_enabled": bool(cfg["exotherm_enabled"]),
        },
        "kinetics_params": asdict(kinetics.params),
    }


def run_fem_simulation_normalized(config):
    from services.engine_registry import ENGINE_AXISYMMETRIC_FIPY
    from services.simulation_schema import normalize_simulation_output

    sim = run_fem_simulation(config)
    return normalize_simulation_output(sim, engine=ENGINE_AXISYMMETRIC_FIPY)
