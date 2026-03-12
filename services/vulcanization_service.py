import uuid
from datetime import datetime, timezone
from pathlib import Path
import math

import numpy as np

R_GAS = 8.314462618
OUT_DIR = Path("data/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_SNAPSHOT_BYTES = 768 * 1024 * 1024  # 768 MB for persisted snapshots (T + alpha)
MAX_SNAPSHOT_CELLS = 1_500_000  # target upper bound for cells per saved snapshot


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
        cells = int(np.prod(current_shape, dtype=np.int64))
        return cells * index_count * 2 * np.dtype(np.float32).itemsize

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
                f"Aplicando otimizacao de memoria: stride espacial={store_stride}, "
                f"snapshot a cada {plan['snapshot_every_store']} steps, "
                f"armazenamento estimado ~{approx_mb:.0f} MB."
            ),
        )

    alpha_diff = 1.2e-7

    steps = int(max(math.ceil(t_end / dt), 1))
    t_field = np.full(shape, init_temp_c + 273.15, dtype=np.float32)
    cure_drive_field = np.zeros(shape, dtype=np.float32)

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
    times = np.empty((store_count,), dtype=np.float64)
    t_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)
    alpha_snaps = np.empty((store_count, *plan["stored_shape"]), dtype=np.float32)

    prev_t = 0.0
    t_bc0 = _temperature_profile(mode, 0.0, mold_temp_c, ramp_rate) + 273.15
    _apply_dirichlet_boundaries(t_field, t_bc0, axes=heated_axes)

    progress_stride = max(1, steps // 120)
    store_pos = 0
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

        if store_pos < store_count and step == int(store_indices[store_pos]):
            times[store_pos] = t_now
            t_snaps[store_pos] = t_field[store_slices].astype(np.float32, copy=False)
            alpha_snaps[store_pos] = alpha_field[store_slices].astype(np.float32, copy=False)
            store_pos += 1

        if step == 0 or step == steps or (step % progress_stride == 0):
            progress = 5 + int((float(step) / max(float(steps), 1.0)) * 90.0)
            _emit_progress("calculando", progress, f"Passo {step} de {steps}")

        prev_t = t_now

    _check_cancel()
    _emit_progress("salvando", 97, "Salvando resultados da simulacao...")
    sim_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    out_path = OUT_DIR / f"sim_{sim_id}.npz"

    np.savez_compressed(
        out_path,
        sim_id=sim_id,
        fit_id=fit_payload["fit_id"],
        mode=mode,
        dim=dim,
        shape=np.array(plan["stored_shape"]),
        full_shape=np.array(shape),
        platen_axis=np.array(selected_platen_axis, dtype=int),
        heated_axes=np.array(heated_axes, dtype=int),
        store_stride=np.array(store_stride, dtype=int),
        snapshot_every_source=np.array(max(int(snapshot_every), 1), dtype=int),
        snapshot_every_store=np.array(int(plan["snapshot_every_store"]), dtype=int),
        dx=dx,
        dt=dt,
        t_end=t_end,
        times=times,
        t_snaps=t_snaps,
        alpha_snaps=alpha_snaps,
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
    full_shape = tuple(int(x) for x in d["full_shape"]) if "full_shape" in files else tuple(int(x) for x in d["shape"])
    store_stride = int(d["store_stride"]) if "store_stride" in files else 1
    snapshot_every_source = int(d["snapshot_every_source"]) if "snapshot_every_source" in files else 20
    snapshot_every_store = int(d["snapshot_every_store"]) if "snapshot_every_store" in files else snapshot_every_source

    return {
        "sim_id": str(d["sim_id"]),
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
        "alpha_snaps": d["alpha_snaps"],
    }
