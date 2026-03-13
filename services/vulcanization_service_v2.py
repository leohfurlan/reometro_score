import math
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from services.mechanistic_kinetics import (
    MechanisticKinetics,
    MechanisticKineticsParams,
    R_GAS,
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
    thermal_increment_from_source,
)

OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_SNAPSHOT_BYTES = 768 * 1024 * 1024  # 768 MB for persisted snapshots
MAX_SNAPSHOT_CELLS = 1_500_000
DEFAULT_A_IND = 1.0e12  # 1/s, keeps induction almost instantaneous when not provided
DEFAULT_E_IND = 0.0  # J/mol


def _build_simulation_v2_path(sim_id):
    return OUT_DIR / f"sim_v2_{sim_id}.npz"


def _parse_shape(dim, shape_raw):
    parts = [int(x.strip()) for x in str(shape_raw).split(",") if str(x).strip()]
    if len(parts) != dim:
        defaults = {1: [200], 2: [80, 80], 3: [30, 40, 40]}
        return tuple(defaults[dim])
    return tuple(parts)


def _temperature_profile(mode, t, base_temp, ramp_rate):
    if mode == "autoclave" and ramp_rate > 0:
        return base_temp + ramp_rate * t
    return base_temp


def _apply_dirichlet_boundaries(field, boundary_value, axes=None):
    dim = field.ndim
    if axes is None:
        axes = tuple(range(dim))
    else:
        axes = tuple(int(a) for a in axes if 0 <= int(a) < dim)

    slices = [slice(None)] * dim
    for axis in axes:
        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        field[tuple(low)] = boundary_value
        field[tuple(high)] = boundary_value


def _apply_convective_boundaries(field, dt, dx, rho, cp, h_conv, ambient_k, axes=None):
    """
    Simple explicit/relaxed convective boundary update for Cartesian grids.
    Uses a half-cell balance at faces with stable relaxation:
      beta = 2*h*dt/(rho*cp*dx)
      T_face <- T_face + (beta/(1+beta)) * (T_inf - T_face)
    """
    dim = field.ndim
    if axes is None:
        axes = tuple(range(dim))
    else:
        axes = tuple(int(a) for a in axes if 0 <= int(a) < dim)

    beta = (2.0 * float(h_conv) * float(dt)) / (max(float(rho), 1e-9) * max(float(cp), 1e-9) * max(float(dx), 1e-12))
    relax = float(beta / (1.0 + max(beta, 0.0)))
    ambient_k = float(ambient_k)

    slices = [slice(None)] * dim
    for axis in axes:
        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        low_t = tuple(low)
        high_t = tuple(high)
        field[low_t] = field[low_t] + (relax * (ambient_k - field[low_t]))
        field[high_t] = field[high_t] + (relax * (ambient_k - field[high_t]))


def _stable_dt_limit(alpha_diff, dx, dim):
    dim_safe = max(int(dim), 1)
    return (dx * dx) / (2.0 * dim_safe * alpha_diff)


def _substep_config(dt, alpha_diff, dx, dim, safety=0.95):
    dt_limit = _stable_dt_limit(alpha_diff, dx, dim) * float(safety)
    if dt_limit <= 0:
        return 1, dt
    substeps = max(1, int(math.ceil(dt / dt_limit)))
    return substeps, dt / substeps


def _snapshot_plan(shape, dim, steps, snapshot_every):
    base_every = max(int(snapshot_every), 1)
    total_cells = int(np.prod(shape, dtype=np.int64))

    if total_cells <= 0:
        return {
            "snapshot_every_store": base_every,
            "spatial_stride": 1,
            "stored_shape": tuple(shape),
            "store_indices": [0, steps],
            "estimated_bytes": 0,
            "downsampled": False,
        }

    stride = 1
    if total_cells > MAX_SNAPSHOT_CELLS:
        stride = max(1, int(math.ceil((total_cells / MAX_SNAPSHOT_CELLS) ** (1.0 / max(dim, 1)))))

    def _stored_shape_for_stride(current_stride):
        return tuple(((int(n) - 1) // current_stride) + 1 for n in shape)

    def _store_indices_for_every(current_every):
        indices = list(range(0, steps + 1, current_every))
        if not indices or indices[-1] != steps:
            indices.append(steps)
        return indices

    def _estimated_bytes(current_shape, index_count):
        # Stored fields: temperature, alpha_c, alpha_r, alpha, heat_source, induction_progress.
        cells = int(np.prod(current_shape, dtype=np.int64))
        return cells * index_count * 6 * np.dtype(np.float32).itemsize

    stored_shape = _stored_shape_for_stride(stride)
    snapshot_every_store = base_every
    store_indices = _store_indices_for_every(snapshot_every_store)
    est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    if est_bytes > MAX_SNAPSHOT_BYTES:
        time_stride = int(math.ceil(est_bytes / MAX_SNAPSHOT_BYTES))
        snapshot_every_store = max(base_every, base_every * max(time_stride, 1))
        store_indices = _store_indices_for_every(snapshot_every_store)
        est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    while est_bytes > MAX_SNAPSHOT_BYTES and stride < max(shape):
        stride += 1
        stored_shape = _stored_shape_for_stride(stride)
        est_bytes = _estimated_bytes(stored_shape, len(store_indices))

    return {
        "snapshot_every_store": snapshot_every_store,
        "spatial_stride": int(stride),
        "stored_shape": tuple(int(x) for x in stored_shape),
        "store_indices": store_indices,
        "estimated_bytes": int(est_bytes),
        "downsampled": bool(int(stride) > 1 or snapshot_every_store > base_every),
    }


def _laplacian_with_mixed_boundaries(field, dx, dirichlet_axes, dirichlet_value):
    dim = field.ndim
    lap = np.zeros_like(field)
    slices = [slice(None)] * dim

    for axis in range(dim):
        backward = np.roll(field, 1, axis=axis)
        forward = np.roll(field, -1, axis=axis)

        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        low_t = tuple(low)
        high_t = tuple(high)

        if axis in dirichlet_axes:
            backward[low_t] = dirichlet_value
            forward[high_t] = dirichlet_value
        else:
            backward[low_t] = field[low_t]
            forward[high_t] = field[high_t]

        lap += (backward - (2.0 * field) + forward) / (dx * dx)

    return lap


def _safe_material_props(rho, cp, lambda_rubber, qv):
    rho_val = float(max(rho, 1e-9))
    cp_val = float(max(cp, 1e-9))
    lambda_val = float(max(lambda_rubber, 1e-12))
    qv_val = float(max(qv, 0.0))
    return rho_val, cp_val, lambda_val, qv_val


def _resolve_induction_params(fit_payload, a_ind, e_ind):
    """
    Resolve induction Arrhenius parameters used in:
      t_scorch(T) = (1 / A_ind) * exp(E_ind / (R * T))
    """
    if a_ind is None and isinstance(fit_payload, dict):
        a_ind = fit_payload.get("A_ind")
    if e_ind is None and isinstance(fit_payload, dict):
        e_ind = fit_payload.get("E_ind")

    try:
        a_ind_val = float(a_ind) if a_ind is not None else DEFAULT_A_IND
    except (TypeError, ValueError):
        a_ind_val = DEFAULT_A_IND
    try:
        e_ind_val = float(e_ind) if e_ind is not None else DEFAULT_E_IND
    except (TypeError, ValueError):
        e_ind_val = DEFAULT_E_IND

    a_ind_val = float(max(a_ind_val, 1e-30))
    e_ind_val = float(max(e_ind_val, 0.0))
    return a_ind_val, e_ind_val


def _scorch_time_arrhenius(temp_k, a_ind, e_ind):
    temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
    exponent = np.clip(float(e_ind) / (R_GAS * temp_safe), -700.0, 700.0)
    t_scorch = np.exp(exponent) / float(max(a_ind, 1e-30))
    return np.maximum(t_scorch, 1e-12)


def _build_kinetics(fit_payload, kinetics_params):
    defaults = MechanisticKineticsParams()
    merged = asdict(defaults)

    if isinstance(fit_payload, dict):
        if fit_payload.get("k0") is not None:
            merged["Ac"] = float(fit_payload["k0"])
        if fit_payload.get("Ea") is not None:
            merged["Eac"] = float(fit_payload["Ea"])
        if fit_payload.get("n") is not None:
            merged["Nn"] = float(fit_payload["n"])

    if isinstance(kinetics_params, MechanisticKineticsParams):
        merged.update(asdict(kinetics_params))
    elif isinstance(kinetics_params, dict):
        for key in merged:
            if key in kinetics_params and kinetics_params[key] is not None:
                merged[key] = kinetics_params[key]

    params = MechanisticKineticsParams(**merged)
    return MechanisticKinetics(params)


def save_simulation_v2(
    *,
    fit_id,
    mode,
    dim,
    stored_shape,
    full_shape,
    platen_axis,
    heated_axes,
    store_stride,
    snapshot_every_source,
    snapshot_every_store,
    dx,
    dt,
    t_end,
    times,
    t_snaps,
    alpha_c_snaps,
    alpha_r_snaps,
    alpha_snaps,
    heat_source_snaps,
    process_state_over_time,
    process_state_final,
    demold_time,
    rho,
    cp,
    lambda_rubber,
    qv,
    exotherm_enabled,
    A_ind=DEFAULT_A_IND,
    E_ind=DEFAULT_E_IND,
    kinetics_params=None,
    induction_progress_snaps=None,
    sim_id=None,
):
    """
    Persist v2 simulation snapshots and metadata to data/out/sim_v2_<sim_id>.npz.

    This function is versioned and isolated from legacy v1 format.
    """
    if sim_id is None:
        sim_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]

    path = _build_simulation_v2_path(sim_id)
    state_trace = np.asarray(process_state_over_time, dtype="<U16")
    demold_scalar = np.nan if demold_time is None else float(demold_time)

    np.savez_compressed(
        path,
        sim_id=str(sim_id),
        solver_version=np.array(2, dtype=int),
        fit_id=str(fit_id),
        mode=str(mode),
        dim=int(dim),
        shape=np.asarray(stored_shape, dtype=int),
        full_shape=np.asarray(full_shape, dtype=int),
        platen_axis=np.array(int(platen_axis), dtype=int),
        heated_axes=np.asarray(tuple(int(x) for x in heated_axes), dtype=int),
        store_stride=np.array(int(store_stride), dtype=int),
        snapshot_every_source=np.array(int(snapshot_every_source), dtype=int),
        snapshot_every_store=np.array(int(snapshot_every_store), dtype=int),
        dx=float(dx),
        dt=float(dt),
        t_end=float(t_end),
        times=np.asarray(times, dtype=np.float64),
        t_snaps=np.asarray(t_snaps, dtype=np.float32),
        alpha_c_snaps=np.asarray(alpha_c_snaps, dtype=np.float32),
        alpha_r_snaps=np.asarray(alpha_r_snaps, dtype=np.float32),
        alpha_snaps=np.asarray(alpha_snaps, dtype=np.float32),
        heat_source_snaps=np.asarray(heat_source_snaps, dtype=np.float32),
        induction_progress_snaps=np.asarray(
            induction_progress_snaps if induction_progress_snaps is not None else np.zeros_like(alpha_snaps),
            dtype=np.float32,
        ),
        process_state_over_time=state_trace,
        process_state_final=np.array(str(process_state_final), dtype="<U16"),
        demold_time=np.array(demold_scalar, dtype=np.float64),
        rho=np.array(float(rho), dtype=np.float64),
        cp=np.array(float(cp), dtype=np.float64),
        lambda_rubber=np.array(float(lambda_rubber), dtype=np.float64),
        qv=np.array(float(qv), dtype=np.float64),
        A_ind=np.array(float(A_ind), dtype=np.float64),
        E_ind=np.array(float(E_ind), dtype=np.float64),
        exotherm_enabled=np.array(bool(exotherm_enabled), dtype=np.bool_),
        kinetics_params=np.array(dict(kinetics_params or {}), dtype=object),
    )
    return str(sim_id), str(path)


def run_simulation_v2(
    fit_payload,
    mode,
    dim,
    shape_raw,
    dx,
    dt,
    t_end,
    mold_temp_c,
    init_temp_c=25.0,
    ramp_rate=0.0,
    snapshot_every=20,
    platen_axis=None,
    progress_callback=None,
    cancel_checker=None,
    rho=RHO_RUBBER,
    cp=CP_RUBBER,
    lambda_rubber=LAMBDA_RUBBER,
    qv=QV_VULCANIZATION,
    exotherm_enabled=True,
    A_ind=None,
    E_ind=None,
    kinetics_params=None,
):
    def _emit_progress(stage, progress, message):
        if callable(progress_callback):
            progress_callback(
                {
                    "stage": str(stage),
                    "progress": int(np.clip(progress, 0, 100)),
                    "message": str(message),
                }
            )

    def _check_cancel():
        if callable(cancel_checker) and bool(cancel_checker()):
            raise RuntimeError("SIMULATION_CANCELLED")

    rho_val, cp_val, lambda_val, qv_val = _safe_material_props(rho, cp, lambda_rubber, qv)
    a_ind_val, e_ind_val = _resolve_induction_params(fit_payload, A_ind, E_ind)
    alpha_diff = lambda_val / (rho_val * cp_val)
    kinetics = _build_kinetics(fit_payload, kinetics_params)
    process_config = ProcessThermalConfig(
        mold_temperature_c=float(mold_temp_c),
        ambient_temperature_c=25.0,
        h_mold=10000.0,
        h_air=11.4,
        demold_alpha_mean_threshold=0.9,
    )
    process_sm = VulcanizationProcessStateMachine(process_config)

    _emit_progress("validando", 1, "Validando parametros e malha (v2)...")
    shape = _parse_shape(dim, shape_raw)
    plan = _snapshot_plan(shape, dim, int(max(math.ceil(t_end / dt), 1)), snapshot_every)
    store_stride = int(plan["spatial_stride"])
    store_slices = tuple(slice(None, None, store_stride) for _ in range(dim))
    store_indices = plan["store_indices"]
    store_count = len(store_indices)

    if plan["downsampled"]:
        approx_mb = plan["estimated_bytes"] / (1024.0 * 1024.0)
        _emit_progress(
            "otimizando_memoria",
            2,
            (
                f"Aplicando otimizacao de memoria (v2): stride espacial={store_stride}, "
                f"snapshot a cada {plan['snapshot_every_store']} steps, "
                f"armazenamento estimado ~{approx_mb:.0f} MB."
            ),
        )

    steps = int(max(math.ceil(t_end / dt), 1))
    t_field = np.full(shape, init_temp_c + 273.15, dtype=np.float32)

    alpha_c0 = float(np.clip(kinetics.params.alpha_c0, 0.0, 1.0))
    alpha_r0 = float(np.clip(kinetics.params.alpha_r0, 0.0, alpha_c0))
    alpha_c_field = np.full(shape, alpha_c0, dtype=np.float32)
    alpha_r_field = np.full(shape, alpha_r0, dtype=np.float32)
    alpha_field = np.clip(alpha_c_field - alpha_r_field, 0.0, 1.0).astype(np.float32, copy=False)
    q_source_field = np.zeros(shape, dtype=np.float32)
    induction_progress_field = np.zeros(shape, dtype=np.float32)

    if mode == "prensa":
        selected_platen_axis = 0 if platen_axis is None else int(platen_axis)
        selected_platen_axis = int(np.clip(selected_platen_axis, 0, max(dim - 1, 0)))
        heated_axes = (selected_platen_axis,)
    else:
        selected_platen_axis = -1
        heated_axes = tuple(range(dim))

    _emit_progress("inicializando", 3, "Inicializando campos acoplados (v2)...")
    times = np.empty((store_count,), dtype=np.float64)
    t_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_c_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_r_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    heat_source_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    induction_progress_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    process_state_over_time = np.empty((store_count,), dtype="<U16")

    prev_t = 0.0
    t_bc0 = _temperature_profile(mode, 0.0, mold_temp_c, ramp_rate) + 273.15
    _apply_dirichlet_boundaries(t_field, t_bc0, axes=heated_axes)

    progress_stride = max(1, steps // 120)
    store_pos = 0
    _emit_progress("calculando", 5, "Executando passos de simulacao v2...")

    for step in range(steps + 1):
        _check_cancel()
        t_now = min(step * dt, t_end)

        if step > 0:
            dt_step = t_now - prev_t
            substeps, dt_inner = _substep_config(dt_step, alpha_diff, dx, dim)
            for sub_idx in range(substeps):
                _check_cancel()
                t_sub = prev_t + (sub_idx + 1) * dt_inner
                t_mold_k = _temperature_profile(mode, t_sub, mold_temp_c, ramp_rate) + 273.15

                if process_sm.state == ProcessState.HEATING:
                    h_conv = process_config.h_mold
                    ambient_k = t_mold_k
                    convective_axes = heated_axes
                else:
                    h_conv = process_config.h_air
                    ambient_k = process_config.ambient_temperature_c + 273.15
                    convective_axes = tuple(range(dim))

                # Conduction is solved with Neumann-like faces; convection is applied explicitly afterwards.
                lap = _laplacian_with_mixed_boundaries(t_field, dx, tuple(), 0.0)

                alpha_c_prev = np.asarray(alpha_c_field, dtype=float)
                alpha_r_prev = np.asarray(alpha_r_field, dtype=float)
                t_scorch_local = _scorch_time_arrhenius(t_field, a_ind_val, e_ind_val)
                induction_progress_field = induction_progress_field + (dt_inner / t_scorch_local)
                induction_progress_field = np.nan_to_num(
                    induction_progress_field,
                    nan=0.0,
                    posinf=1e9,
                    neginf=0.0,
                ).astype(np.float32, copy=False)
                induction_unlocked = np.asarray(induction_progress_field >= 1.0, dtype=bool)

                alpha_c_next, alpha_r_next, alpha_next = kinetics.step_explicit(
                    T=t_field,
                    alpha_c=alpha_c_prev,
                    alpha_r=alpha_r_prev,
                    dt=dt_inner,
                )
                alpha_c_next = np.where(induction_unlocked, alpha_c_next, alpha_c_prev)
                alpha_next = np.clip(alpha_c_next - alpha_r_next, 0.0, 1.0)

                # Heat source uses only cure growth (d alpha_c / dt), excluding reversion.
                alpha_c_rate = np.maximum((alpha_c_next - alpha_c_prev) / max(dt_inner, 1e-12), 0.0)
                if bool(exotherm_enabled) and qv_val > 0.0:
                    q_source_field = reaction_heat_source(alpha_c_rate, rho=rho_val, qv=qv_val).astype(np.float32, copy=False)
                else:
                    q_source_field.fill(0.0)

                t_field = t_field + (alpha_diff * dt_inner * lap)
                t_field = t_field + thermal_increment_from_source(q_source_field, dt_inner, rho=rho_val, cp=cp_val)
                _apply_convective_boundaries(
                    t_field,
                    dt=dt_inner,
                    dx=dx,
                    rho=rho_val,
                    cp=cp_val,
                    h_conv=h_conv,
                    ambient_k=ambient_k,
                    axes=convective_axes,
                )
                t_field = np.nan_to_num(t_field, nan=0.0, posinf=1e6, neginf=0.0).astype(np.float32, copy=False)

                alpha_c_field = np.clip(alpha_c_next, 0.0, 1.0).astype(np.float32, copy=False)
                alpha_r_field = np.clip(alpha_r_next, 0.0, 1.0).astype(np.float32, copy=False)
                alpha_r_field = np.minimum(alpha_r_field, alpha_c_field).astype(np.float32, copy=False)
                alpha_field = np.clip(alpha_next, 0.0, 1.0).astype(np.float32, copy=False)
                process_sm.update(mean_alpha=float(np.mean(alpha_field)), t_now=t_sub)

        if store_pos < store_count and step == int(store_indices[store_pos]):
            times[store_pos] = t_now
            t_snaps[store_pos] = t_field[store_slices].astype(np.float32, copy=False)
            alpha_c_snaps[store_pos] = alpha_c_field[store_slices].astype(np.float32, copy=False)
            alpha_r_snaps[store_pos] = alpha_r_field[store_slices].astype(np.float32, copy=False)
            alpha_snaps[store_pos] = alpha_field[store_slices].astype(np.float32, copy=False)
            heat_source_snaps[store_pos] = q_source_field[store_slices].astype(np.float32, copy=False)
            induction_progress_snaps[store_pos] = induction_progress_field[store_slices].astype(np.float32, copy=False)
            process_state_over_time[store_pos] = process_sm.state.value
            store_pos += 1

        if step == 0 or step == steps or (step % progress_stride == 0):
            progress = 5 + int((float(step) / max(float(steps), 1.0)) * 90.0)
            _emit_progress("calculando", progress, f"Passo {step} de {steps}")

        prev_t = t_now

    _check_cancel()
    _emit_progress("salvando", 97, "Salvando resultados da simulacao v2...")
    sim_id, out_path = save_simulation_v2(
        fit_id=fit_payload.get("fit_id", "fit_unknown"),
        mode=mode,
        dim=dim,
        stored_shape=plan["stored_shape"],
        full_shape=shape,
        platen_axis=selected_platen_axis,
        heated_axes=heated_axes,
        store_stride=store_stride,
        snapshot_every_source=max(int(snapshot_every), 1),
        snapshot_every_store=int(plan["snapshot_every_store"]),
        dx=dx,
        dt=dt,
        t_end=t_end,
        times=times,
        t_snaps=t_snaps,
        alpha_c_snaps=alpha_c_snaps,
        alpha_r_snaps=alpha_r_snaps,
        alpha_snaps=alpha_snaps,
        heat_source_snaps=heat_source_snaps,
        induction_progress_snaps=induction_progress_snaps,
        process_state_over_time=process_state_over_time,
        process_state_final=process_sm.state.value,
        demold_time=process_sm.demold_time,
        rho=rho_val,
        cp=cp_val,
        lambda_rubber=lambda_val,
        qv=qv_val,
        A_ind=a_ind_val,
        E_ind=e_ind_val,
        exotherm_enabled=bool(exotherm_enabled),
        kinetics_params=asdict(kinetics.params),
    )
    _emit_progress("concluido", 100, "Simulacao v2 concluida.")
    return sim_id, out_path


def load_simulation_v2(sim_id):
    path = _build_simulation_v2_path(sim_id)
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=True)
    files = set(d.files)

    platen_axis = int(d["platen_axis"]) if "platen_axis" in files else 0
    heated_axes = tuple(int(x) for x in d["heated_axes"]) if "heated_axes" in files else tuple(range(int(d["dim"])))
    full_shape = tuple(int(x) for x in d["full_shape"]) if "full_shape" in files else tuple(int(x) for x in d["shape"])
    store_stride = int(d["store_stride"]) if "store_stride" in files else 1
    snapshot_every_source = int(d["snapshot_every_source"]) if "snapshot_every_source" in files else 20
    snapshot_every_store = int(d["snapshot_every_store"]) if "snapshot_every_store" in files else snapshot_every_source

    alpha_snaps = d["alpha_snaps"]
    alpha_c_snaps = d["alpha_c_snaps"] if "alpha_c_snaps" in files else alpha_snaps
    alpha_r_snaps = d["alpha_r_snaps"] if "alpha_r_snaps" in files else np.zeros_like(alpha_snaps)
    heat_source_snaps = d["heat_source_snaps"] if "heat_source_snaps" in files else np.zeros_like(alpha_snaps)
    induction_progress_snaps = (
        d["induction_progress_snaps"] if "induction_progress_snaps" in files else np.zeros_like(alpha_snaps)
    )
    process_state_over_time = (
        np.asarray(d["process_state_over_time"], dtype=str)
        if "process_state_over_time" in files
        else np.full((len(d["times"]),), ProcessState.HEATING.value, dtype="<U16")
    )
    process_state_final = (
        str(d["process_state_final"])
        if "process_state_final" in files
        else str(process_state_over_time[-1] if len(process_state_over_time) else ProcessState.HEATING.value)
    )
    demold_time_raw = float(d["demold_time"]) if "demold_time" in files else float("nan")
    demold_time = None if np.isnan(demold_time_raw) else demold_time_raw

    kinetics_params = None
    if "kinetics_params" in files:
        raw = d["kinetics_params"]
        if isinstance(raw, np.ndarray) and raw.shape == ():
            try:
                kinetics_params = dict(raw.item())
            except Exception:
                kinetics_params = None

    return {
        "sim_id": str(d["sim_id"]),
        "solver_version": int(d["solver_version"]) if "solver_version" in files else 2,
        "fit_id": str(d["fit_id"]),
        "mode": str(d["mode"]),
        "dim": int(d["dim"]),
        "shape": tuple(int(x) for x in d["shape"]),
        "full_shape": full_shape,
        "platen_axis": platen_axis,
        "heated_axes": heated_axes,
        "store_stride": store_stride,
        "snapshot_every_source": snapshot_every_source,
        "snapshot_every_store": snapshot_every_store,
        "dx": float(d["dx"]) * float(store_stride),
        "dx_compute": float(d["dx"]),
        "dt": float(d["dt"]),
        "t_end": float(d["t_end"]),
        "times": d["times"],
        "t_snaps": d["t_snaps"],
        "alpha_c_snaps": alpha_c_snaps,
        "alpha_r_snaps": alpha_r_snaps,
        "alpha_snaps": alpha_snaps,
        "heat_source_snaps": heat_source_snaps,
        "induction_progress_snaps": induction_progress_snaps,
        "process_state_over_time": process_state_over_time,
        "process_state_final": process_state_final,
        "demold_time": demold_time,
        "rho": float(d["rho"]) if "rho" in files else RHO_RUBBER,
        "cp": float(d["cp"]) if "cp" in files else CP_RUBBER,
        "lambda_rubber": float(d["lambda_rubber"]) if "lambda_rubber" in files else LAMBDA_RUBBER,
        "qv": float(d["qv"]) if "qv" in files else QV_VULCANIZATION,
        "A_ind": float(d["A_ind"]) if "A_ind" in files else DEFAULT_A_IND,
        "E_ind": float(d["E_ind"]) if "E_ind" in files else DEFAULT_E_IND,
        "exotherm_enabled": bool(d["exotherm_enabled"]) if "exotherm_enabled" in files else True,
        "kinetics_params": kinetics_params,
    }


def run_simulation(*args, **kwargs):
    return run_simulation_v2(*args, **kwargs)


def load_simulation(sim_id):
    return load_simulation_v2(sim_id)
