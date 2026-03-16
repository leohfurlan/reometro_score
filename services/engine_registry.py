from dataclasses import dataclass
import os


ENGINE_EMPIRICAL_V1 = "empirical_v1"
ENGINE_THERMO_KINETIC_V2 = "thermo_kinetic_v2"
ENGINE_AXISYMMETRIC_FIPY = "axisymmetric_fipy"

ENGINE_STATUS_STABLE = "stable"
ENGINE_STATUS_EXPERIMENTAL = "experimental"
ENGINE_STATUS_PROTOTYPE = "prototype"


@dataclass(frozen=True)
class EngineSpec:
    key: str
    label: str
    status: str
    description: str
    enabled_by_default: bool
    enable_env_var: str | None = None


ENGINE_ORDER = (
    ENGINE_EMPIRICAL_V1,
    ENGINE_THERMO_KINETIC_V2,
    ENGINE_AXISYMMETRIC_FIPY,
)

ENGINE_SPECS = {
    ENGINE_EMPIRICAL_V1: EngineSpec(
        key=ENGINE_EMPIRICAL_V1,
        label="Empirical v1",
        status=ENGINE_STATUS_STABLE,
        description="Motor legado empirico (producao).",
        enabled_by_default=True,
        enable_env_var=None,
    ),
    ENGINE_THERMO_KINETIC_V2: EngineSpec(
        key=ENGINE_THERMO_KINETIC_V2,
        label="Thermo-kinetic v2",
        status=ENGINE_STATUS_EXPERIMENTAL,
        description="Motor novo termo-cinetico acoplado (uso experimental).",
        enabled_by_default=True,
        enable_env_var="REOMETRIA_ENABLE_THERMO_KINETIC_V2",
    ),
    ENGINE_AXISYMMETRIC_FIPY: EngineSpec(
        key=ENGINE_AXISYMMETRIC_FIPY,
        label="Axisymmetric FiPy",
        status=ENGINE_STATUS_PROTOTYPE,
        description="Prototipo axisimetrico em FiPy (nao produtivo).",
        enabled_by_default=False,
        enable_env_var="REOMETRIA_ENABLE_AXISYMMETRIC_FIPY",
    ),
}


def _parse_bool_env(raw_value, *, default):
    if raw_value is None:
        return bool(default)
    value = str(raw_value).strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


def normalize_engine(value):
    normalized = str(value or "").strip().lower()
    if normalized in ENGINE_SPECS:
        return normalized
    return ENGINE_EMPIRICAL_V1


def get_engine_spec(engine):
    return ENGINE_SPECS[normalize_engine(engine)]


def get_engine_status(engine):
    return get_engine_spec(engine).status


def is_engine_enabled(engine):
    spec = get_engine_spec(engine)
    if not spec.enable_env_var:
        return True
    return _parse_bool_env(os.getenv(spec.enable_env_var), default=spec.enabled_by_default)


def get_default_engine():
    requested_default = normalize_engine(os.getenv("REOMETRIA_DEFAULT_ENGINE", ENGINE_EMPIRICAL_V1))
    requested_spec = get_engine_spec(requested_default)

    # Guard rail: default engine cannot silently become experimental/prototype.
    if requested_spec.status != ENGINE_STATUS_STABLE:
        return ENGINE_EMPIRICAL_V1
    if not is_engine_enabled(requested_default):
        return ENGINE_EMPIRICAL_V1
    return requested_default


def resolve_engine_for_execution(requested_engine, *, explicit_selection):
    default_engine = get_default_engine()
    if not explicit_selection:
        return default_engine

    normalized = normalize_engine(requested_engine)
    if not is_engine_enabled(normalized):
        return default_engine
    return normalized


def list_engine_catalog(*, include_disabled=True):
    default_engine = get_default_engine()
    catalog = []
    for engine_key in ENGINE_ORDER:
        spec = ENGINE_SPECS[engine_key]
        enabled = bool(is_engine_enabled(engine_key))
        if (not include_disabled) and (not enabled) and (engine_key != default_engine):
            continue
        catalog.append(
            {
                "engine": spec.key,
                "label": spec.label,
                "status": spec.status,
                "enabled": enabled,
                "is_default": bool(spec.key == default_engine),
                "description": spec.description,
            }
        )
    return catalog

