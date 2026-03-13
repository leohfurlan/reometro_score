import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.process_state_machine import (
    ProcessState,
    ProcessThermalConfig,
    VulcanizationProcessStateMachine,
)


def _default_config():
    return ProcessThermalConfig(
        mold_temperature_c=170.0,
        ambient_temperature_c=25.0,
        h_mold=10000.0,
        h_air=11.4,
        demold_alpha_mean_threshold=0.9,
    )


def test_starts_in_heating_state():
    sm = VulcanizationProcessStateMachine(_default_config())
    assert sm.state == ProcessState.HEATING
    assert sm.demold_time is None


def test_transitions_to_cooling_and_registers_demold_time():
    sm = VulcanizationProcessStateMachine(_default_config())

    state_before = sm.update(mean_alpha=0.5, t_now=12.0)
    assert state_before == ProcessState.HEATING
    assert sm.demold_time is None

    state_after = sm.update(mean_alpha=0.9, t_now=20.0)
    assert state_after == ProcessState.COOLING
    assert sm.demold_time == 20.0


def test_does_not_rewrite_demold_time_after_transition():
    sm = VulcanizationProcessStateMachine(_default_config())
    sm.update(mean_alpha=0.95, t_now=18.0)
    first_time = sm.demold_time

    sm.update(mean_alpha=0.96, t_now=35.0)
    assert sm.state == ProcessState.COOLING
    assert sm.demold_time == first_time


def test_optional_transition_to_finished_from_cooling():
    sm = VulcanizationProcessStateMachine(_default_config())
    sm.update(mean_alpha=0.95, t_now=10.0)  # HEATING -> COOLING
    state = sm.update(mean_alpha=0.999, t_now=40.0)

    assert state == ProcessState.FINISHED
