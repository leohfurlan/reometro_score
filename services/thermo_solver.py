import math

import numpy as np

# Physical constants for rubber compounds used in vulcanization models.
RHO_RUBBER = 1020.0  # kg/m^3
CP_RUBBER = 1820.0  # J/(kg*K)
QV_VULCANIZATION = 13000.0  # J/kg
LAMBDA_RUBBER = 0.16  # W/(m*K) - configurable placeholder


class ThermalMaterial:
    def __init__(self, rho=RHO_RUBBER, cp=CP_RUBBER, conductivity=LAMBDA_RUBBER, qv=QV_VULCANIZATION):
        self.rho = float(max(rho, 1e-9))
        self.cp = float(max(cp, 1e-9))
        self.conductivity = float(max(conductivity, 1e-12))
        self.qv = float(max(qv, 0.0))

    @property
    def thermal_diffusivity(self):
        return self.conductivity / (self.rho * self.cp)


def reaction_heat_source(alpha_c_rate, rho=RHO_RUBBER, qv=QV_VULCANIZATION):
    """
    Compute volumetric heat generation from cure kinetics.

    Parameters:
        alpha_c_rate: Cure rate field d(alpha_c)/dt [1/s].
        rho: Density [kg/m^3].
        qv: Specific reaction enthalpy [J/kg].

    Returns:
        Volumetric source term q_source [W/m^3], with:
            q_source = rho * qv * d(alpha_c)/dt
    """
    return float(rho) * float(qv) * np.asarray(alpha_c_rate, dtype=float)


def thermal_increment_from_source(q_source, dt, rho=RHO_RUBBER, cp=CP_RUBBER):
    """
    Compute temperature increment caused only by volumetric heat source.

    Parameters:
        q_source: Volumetric heat source [W/m^3].
        dt: Time increment [s].
        rho: Density [kg/m^3].
        cp: Specific heat [J/(kg*K)].

    Returns:
        Temperature increment dT [K] from source term:
            dT = q_source * dt / (rho * cp)
    """
    rho_val = float(rho)
    cp_val = float(cp)
    if rho_val <= 0.0 or cp_val <= 0.0:
        raise ValueError("rho and cp must be positive.")
    return (np.asarray(q_source, dtype=float) * float(dt)) / (rho_val * cp_val)


def apply_dirichlet_boundaries(field, boundary_value, axes=None):
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


def laplacian_with_mixed_boundaries(field, dx, dirichlet_axes=None, dirichlet_value=0.0):
    dim = field.ndim
    dirichlet_axes = set(int(a) for a in (dirichlet_axes or ()))
    lap = np.zeros_like(field, dtype=float)
    slices = [slice(None)] * dim
    dx2 = float(dx) * float(dx)

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
            # Zero-gradient for non-Dirichlet boundaries.
            backward[low_t] = field[low_t]
            forward[high_t] = field[high_t]

        lap += (backward - (2.0 * field) + forward) / dx2

    return lap


def apply_convective_boundaries(field, dt, dx, material, h_conv, ambient_k, axes=None):
    dim = field.ndim
    if axes is None:
        axes = tuple(range(dim))
    else:
        axes = tuple(int(a) for a in axes if 0 <= int(a) < dim)

    # Half-cell control volume at boundaries.
    coeff = (2.0 * float(h_conv)) / (material.rho * material.cp * max(float(dx), 1e-12))
    factor = float(dt) * coeff
    ambient_k = float(ambient_k)

    slices = [slice(None)] * dim
    for axis in axes:
        low = slices.copy()
        low[axis] = 0
        high = slices.copy()
        high[axis] = -1
        low_t = tuple(low)
        high_t = tuple(high)
        field[low_t] = field[low_t] + factor * (ambient_k - field[low_t])
        field[high_t] = field[high_t] + factor * (ambient_k - field[high_t])


def stable_dt_limit(alpha_diff, dx, dim):
    dim_safe = max(int(dim), 1)
    return (float(dx) * float(dx)) / (2.0 * dim_safe * max(float(alpha_diff), 1e-30))


def substep_config(dt, alpha_diff, dx, dim, safety=0.95):
    dt_limit = stable_dt_limit(alpha_diff, dx, dim) * float(safety)
    if dt_limit <= 0:
        return 1, float(dt)
    substeps = max(1, int(math.ceil(float(dt) / dt_limit)))
    return substeps, float(dt) / substeps


def explicit_heat_step(
    field_k,
    dt,
    dx,
    material,
    internal_heat_w_m3,
    boundary_mode="dirichlet",
    boundary_value_k=None,
    dirichlet_axes=None,
    h_conv=None,
    ambient_k=None,
    convective_axes=None,
):
    field_k = np.asarray(field_k, dtype=float)
    source = np.asarray(internal_heat_w_m3, dtype=float) / (material.rho * material.cp)
    lap = laplacian_with_mixed_boundaries(
        field_k,
        dx=dx,
        dirichlet_axes=tuple(dirichlet_axes or ()),
        dirichlet_value=float(boundary_value_k if boundary_value_k is not None else 0.0),
    )
    updated = field_k + float(dt) * ((material.thermal_diffusivity * lap) + source)

    if boundary_mode == "dirichlet" and boundary_value_k is not None:
        apply_dirichlet_boundaries(updated, float(boundary_value_k), axes=dirichlet_axes)
    elif boundary_mode == "convective" and h_conv is not None and ambient_k is not None:
        apply_convective_boundaries(
            updated,
            dt=dt,
            dx=dx,
            material=material,
            h_conv=h_conv,
            ambient_k=ambient_k,
            axes=convective_axes,
        )

    return np.nan_to_num(updated, nan=0.0, posinf=1e6, neginf=0.0)
