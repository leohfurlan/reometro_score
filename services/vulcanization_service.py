import uuid
from datetime import datetime
from pathlib import Path
import math

import numpy as np

R_GAS = 8.314462618
OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _parse_shape(dim, shape_raw):
    parts = [int(x.strip()) for x in shape_raw.split(",") if x.strip()]
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


def _stable_dt_limit(alpha_diff, dx, dim):
    dim_safe = max(int(dim), 1)
    return (dx * dx) / (2.0 * dim_safe * alpha_diff)


def _substep_config(dt, alpha_diff, dx, dim, safety=0.95):
    dt_limit = _stable_dt_limit(alpha_diff, dx, dim) * float(safety)
    if dt_limit <= 0:
        return 1, dt
    substeps = max(1, int(math.ceil(dt / dt_limit)))
    return substeps, dt / substeps


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


def run_simulation(
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

    _emit_progress("validando", 1, "Validando parametros e malha...")
    shape = _parse_shape(dim, shape_raw)
    alpha_diff = 1.2e-7

    steps = int(max(math.ceil(t_end / dt), 1))
    t_field = np.full(shape, init_temp_c + 273.15, dtype=float)
    cure_drive_field = np.zeros(shape, dtype=float)

    k0 = float(fit_payload["k0"])
    ea = float(fit_payload["Ea"])
    n = float(np.clip(fit_payload["n"], 0.5, 12.0))

    if mode == "prensa":
        selected_platen_axis = 0 if platen_axis is None else int(platen_axis)
        selected_platen_axis = int(np.clip(selected_platen_axis, 0, max(dim - 1, 0)))
        heated_axes = (selected_platen_axis,)
    else:
        selected_platen_axis = -1
        heated_axes = tuple(range(dim))

    _emit_progress("inicializando", 3, "Inicializando campos de temperatura e cura...")
    times, t_snaps, alpha_snaps = [], [], []

    prev_t = 0.0
    t_bc0 = _temperature_profile(mode, 0.0, mold_temp_c, ramp_rate) + 273.15
    _apply_dirichlet_boundaries(t_field, t_bc0, axes=heated_axes)

    progress_stride = max(1, steps // 120)
    _emit_progress("calculando", 5, "Executando passos de simulacao...")

    for step in range(steps + 1):
        _check_cancel()
        t_now = min(step * dt, t_end)

        if step > 0:
            dt_step = t_now - prev_t
            substeps, dt_inner = _substep_config(dt_step, alpha_diff, dx, dim)
            for sub_idx in range(substeps):
                _check_cancel()
                t_sub = prev_t + (sub_idx + 1) * dt_inner
                t_bc = _temperature_profile(mode, t_sub, mold_temp_c, ramp_rate) + 273.15

                lap = _laplacian_with_mixed_boundaries(t_field, dx, heated_axes, t_bc)

                t_field = t_field + alpha_diff * dt_inner * lap
                _apply_dirichlet_boundaries(t_field, t_bc, axes=heated_axes)

                k_t = np.maximum(k0 * np.exp(-ea / (R_GAS * np.maximum(t_field, 1.0))), 0.0)
                t_prev_sub = prev_t + (sub_idx * dt_inner)
                dt_pow_n = max((t_sub ** n) - (t_prev_sub ** n), 0.0)
                cure_drive_field = cure_drive_field + (k_t * dt_pow_n)

        alpha_field = cure_drive_field / (1.0 + cure_drive_field)

        if step % max(int(snapshot_every), 1) == 0 or step == steps:
            times.append(t_now)
            t_snaps.append(t_field.copy())
            alpha_snaps.append(alpha_field.copy())

        if step == 0 or step == steps or (step % progress_stride == 0):
            progress = 5 + int((float(step) / max(float(steps), 1.0)) * 90.0)
            _emit_progress("calculando", progress, f"Passo {step} de {steps}")

        prev_t = t_now

    _check_cancel()
    _emit_progress("salvando", 97, "Salvando resultados da simulacao...")
    sim_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    out_path = OUT_DIR / f"sim_{sim_id}.npz"

    np.savez_compressed(
        out_path,
        sim_id=sim_id,
        fit_id=fit_payload["fit_id"],
        mode=mode,
        dim=dim,
        shape=np.array(shape),
        platen_axis=np.array(selected_platen_axis, dtype=int),
        heated_axes=np.array(heated_axes, dtype=int),
        dx=dx,
        dt=dt,
        t_end=t_end,
        times=np.array(times),
        t_snaps=np.array(t_snaps),
        alpha_snaps=np.array(alpha_snaps),
    )
    _emit_progress("concluido", 100, "Simulacao concluida.")
    return sim_id, str(out_path)


def load_simulation(sim_id):
    path = OUT_DIR / f"sim_{sim_id}.npz"
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=True)
    files = set(d.files)
    platen_axis = int(d["platen_axis"]) if "platen_axis" in files else 0
    heated_axes = tuple(int(x) for x in d["heated_axes"]) if "heated_axes" in files else tuple(range(int(d["dim"])))

    return {
        "sim_id": str(d["sim_id"]),
        "fit_id": str(d["fit_id"]),
        "mode": str(d["mode"]),
        "dim": int(d["dim"]),
        "shape": tuple(int(x) for x in d["shape"]),
        "platen_axis": platen_axis,
        "heated_axes": heated_axes,
        "dx": float(d["dx"]),
        "dt": float(d["dt"]),
        "t_end": float(d["t_end"]),
        "times": d["times"],
        "t_snaps": d["t_snaps"],
        "alpha_snaps": d["alpha_snaps"],
    }
