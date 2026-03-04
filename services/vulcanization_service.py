import uuid
from datetime import datetime
from pathlib import Path

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


def run_simulation(fit_payload, mode, dim, shape_raw, dx, dt, t_end, mold_temp_c, init_temp_c=25.0, ramp_rate=0.0, snapshot_every=20):
    shape = _parse_shape(dim, shape_raw)
    alpha_diff = 1.2e-7

    steps = int(max(t_end / dt, 1))
    t_field = np.full(shape, init_temp_c + 273.15, dtype=float)
    z_field = np.zeros(shape, dtype=float)

    k0 = float(fit_payload["k0"])
    ea = float(fit_payload["Ea"])
    n = float(np.clip(fit_payload["n"], 0.5, 12.0))

    times, t_snaps, alpha_snaps = [], [], []

    for step in range(steps + 1):
        t_now = step * dt
        t_bc = _temperature_profile(mode, t_now, mold_temp_c, ramp_rate) + 273.15

        lap = np.zeros_like(t_field)
        for axis in range(dim):
            lap += (np.roll(t_field, 1, axis=axis) - 2 * t_field + np.roll(t_field, -1, axis=axis)) / (dx * dx)

        t_field = t_field + alpha_diff * dt * lap

        slices = [slice(None)] * dim
        for axis in range(dim):
            low = slices.copy()
            low[axis] = 0
            high = slices.copy()
            high[axis] = -1
            t_field[tuple(low)] = t_bc
            t_field[tuple(high)] = t_bc

        k_t = np.maximum(k0 * np.exp(-ea / (R_GAS * np.maximum(t_field, 1.0))), 0.0)
        z_field = z_field + np.power(k_t, 1.0 / n) * dt
        z_n = np.power(np.maximum(z_field, 0.0), n)
        alpha_field = z_n / (1.0 + z_n)

        if step % max(int(snapshot_every), 1) == 0 or step == steps:
            times.append(t_now)
            t_snaps.append(t_field.copy())
            alpha_snaps.append(alpha_field.copy())

    sim_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
    out_path = OUT_DIR / f"sim_{sim_id}.npz"

    np.savez_compressed(
        out_path,
        sim_id=sim_id,
        fit_id=fit_payload["fit_id"],
        mode=mode,
        dim=dim,
        shape=np.array(shape),
        dx=dx,
        dt=dt,
        t_end=t_end,
        times=np.array(times),
        t_snaps=np.array(t_snaps),
        alpha_snaps=np.array(alpha_snaps),
    )
    return sim_id, str(out_path)


def load_simulation(sim_id):
    path = OUT_DIR / f"sim_{sim_id}.npz"
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=True)
    return {
        "sim_id": str(d["sim_id"]),
        "fit_id": str(d["fit_id"]),
        "mode": str(d["mode"]),
        "dim": int(d["dim"]),
        "shape": tuple(int(x) for x in d["shape"]),
        "dx": float(d["dx"]),
        "dt": float(d["dt"]),
        "t_end": float(d["t_end"]),
        "times": d["times"],
        "t_snaps": d["t_snaps"],
        "alpha_snaps": d["alpha_snaps"],
    }
