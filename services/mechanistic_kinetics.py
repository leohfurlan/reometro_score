from dataclasses import dataclass

import numpy as np

R_GAS = 8.314462618  # J/(mol*K)


@dataclass(frozen=True)
class MechanisticKineticsParams:
    """
    Parameters for the mechanistic cure/reversion model.

    Units:
    - Ac: cure Arrhenius pre-exponential factor [1/s]
    - Eac: cure activation energy [J/mol]
    - Ar: reversion Arrhenius pre-exponential factor [1/s]
    - Ear: reversion activation energy [J/mol]
    - Kn: cure autocatalytic gain [-]
    - Nn: cure order for remaining reactive fraction [-]
    - Kx: reversion gain factor [-]
    - Nx: reversion order for remaining reversible fraction [-]
    - alpha_c0: initial cure conversion alpha_c in [0, 1]
    - alpha_r0: initial reversion conversion alpha_r in [0, 1]
    """

    Ac: float = 8.0e5
    Eac: float = 7.8e4
    Ar: float = 0.0
    Ear: float = 1.1e5
    Kn: float = 1.0
    Nn: float = 1.2
    Kx: float = 1.0
    Nx: float = 1.0
    alpha_c0: float = 0.0
    alpha_r0: float = 0.0


def final_alpha(alpha_c, alpha_r):
    """
    Compute net conversion.

    Parameters:
    - alpha_c: cure conversion [-]
    - alpha_r: reversion conversion [-]

    Returns:
    - alpha = alpha_c - alpha_r [-]
    """
    return np.asarray(alpha_c, dtype=float) - np.asarray(alpha_r, dtype=float)


class MechanisticKinetics:
    """
    Mechanistic kinetic core for cure and reversion with explicit time stepping.

    Model equations:
    - d(alpha_c)/dt = k_c(T) * (1 - alpha_c)^Nn * (1 + Kn*alpha_c)
    - d(alpha_r)/dt = k_r(T) * Kx * (1 - alpha_r)^Nx
    - alpha = alpha_c - alpha_r

    Arrhenius terms:
    - k_c(T) = Ac * exp(-Eac/(R*T))
    - k_r(T) = Ar * exp(-Ear/(R*T))
    """

    def __init__(self, params: MechanisticKineticsParams | None = None):
        self.params = params or MechanisticKineticsParams()

    @staticmethod
    def _arrhenius(a_factor, e_act, temp_k):
        temp_safe = np.maximum(np.asarray(temp_k, dtype=float), 1.0)
        exponent = np.clip(-float(e_act) / (R_GAS * temp_safe), -700.0, 700.0)
        return float(max(a_factor, 0.0)) * np.exp(exponent)

    @staticmethod
    def _clip_alpha(alpha):
        return np.clip(np.asarray(alpha, dtype=float), 0.0, 1.0)

    def cure_rate(self, T, alpha_c):
        """
        Cure rate d(alpha_c)/dt [1/s].

        Parameters:
        - T: temperature field [K]
        - alpha_c: cure conversion field [-]
        """
        p = self.params
        alpha_c = self._clip_alpha(alpha_c)
        n_order = float(np.clip(p.Nn, 0.05, 8.0))
        k_c = self._arrhenius(p.Ac, p.Eac, T)
        unreacted = np.clip(1.0 - alpha_c, 0.0, 1.0)
        autocatalytic = 1.0 + (float(p.Kn) * alpha_c)
        rate = k_c * np.power(unreacted, n_order) * np.maximum(autocatalytic, 0.0)
        return np.maximum(rate, 0.0)

    def reversion_rate(self, T, alpha_r):
        """
        Reversion rate d(alpha_r)/dt [1/s].

        Parameters:
        - T: temperature field [K]
        - alpha_r: reversion conversion field [-]
        """
        p = self.params
        alpha_r = self._clip_alpha(alpha_r)
        x_order = float(np.clip(p.Nx, 0.05, 8.0))
        k_r = self._arrhenius(p.Ar, p.Ear, T)
        reversible_left = np.clip(1.0 - alpha_r, 0.0, 1.0)
        rate = k_r * float(max(p.Kx, 0.0)) * np.power(reversible_left, x_order)
        return np.maximum(rate, 0.0)

    def step_explicit(self, T, alpha_c, alpha_r, dt):
        """
        Perform one explicit Euler step for cure/reversion fields.

        Parameters:
        - T: temperature field [K]
        - alpha_c: cure conversion at t [-]
        - alpha_r: reversion conversion at t [-]
        - dt: time step [s]

        Returns:
        - alpha_c_next: cure conversion at t+dt [-]
        - alpha_r_next: reversion conversion at t+dt [-]
        - alpha_next: net conversion alpha_c - alpha_r [-]
        """
        dt = float(max(dt, 0.0))
        alpha_c_now = self._clip_alpha(alpha_c)
        alpha_r_now = self._clip_alpha(alpha_r)
        alpha_r_now = np.minimum(alpha_r_now, alpha_c_now)

        r_c = self.cure_rate(T, alpha_c_now)
        r_r = self.reversion_rate(T, alpha_r_now)

        alpha_c_next = self._clip_alpha(alpha_c_now + (r_c * dt))
        alpha_r_next = self._clip_alpha(alpha_r_now + (r_r * dt))
        alpha_r_next = np.minimum(alpha_r_next, alpha_c_next)

        alpha_next = final_alpha(alpha_c_next, alpha_r_next)
        alpha_next = np.nan_to_num(alpha_next, nan=0.0, posinf=1.0, neginf=0.0)
        return alpha_c_next, alpha_r_next, alpha_next
