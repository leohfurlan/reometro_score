from dataclasses import dataclass
from enum import Enum


class ProcessState(str, Enum):
    HEATING = "HEATING"
    COOLING = "COOLING"
    FINISHED = "FINISHED"


@dataclass(frozen=True)
class ProcessThermalConfig:
    """
    Thermal/process configuration for state transitions and boundary intent.

    Units:
    - mold_temperature_c: mold/platen setpoint [degC]
    - ambient_temperature_c: ambient cooling temperature [degC]
    - h_mold: mold-side convection/transfer coefficient [W/(m^2*K)]
    - h_air: air-side convection coefficient [W/(m^2*K)]
    - demold_alpha_mean_threshold: mean alpha threshold for demolding trigger [-]
    """

    mold_temperature_c: float
    ambient_temperature_c: float
    h_mold: float
    h_air: float
    demold_alpha_mean_threshold: float = 0.9


class VulcanizationProcessStateMachine:
    """
    Minimal process state machine:
    HEATING -> COOLING -> FINISHED.
    """

    def __init__(self, config: ProcessThermalConfig):
        self.config = config
        self.state = ProcessState.HEATING
        self.demold_time = None

    @staticmethod
    def _clip_alpha(value):
        return max(0.0, min(1.0, float(value)))

    def update(self, mean_alpha, t_now):
        """
        Update state from mean conversion and current time.

        Parameters:
        - mean_alpha: mean conversion in domain [-]
        - t_now: current simulation time [s]

        Transition rule:
        - if state == HEATING and mean_alpha >= demold_alpha_mean_threshold:
            state -> COOLING
            demold_time = t_now
        """
        alpha = self._clip_alpha(mean_alpha)
        t_now = float(max(t_now, 0.0))

        if self.state == ProcessState.HEATING and alpha >= float(self.config.demold_alpha_mean_threshold):
            self.state = ProcessState.COOLING
            self.demold_time = t_now
            return self.state

        # Optional terminal latch: once cooling reaches full conversion, close process.
        if self.state == ProcessState.COOLING and alpha >= 0.999:
            self.state = ProcessState.FINISHED

        return self.state
