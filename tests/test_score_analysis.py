import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reoscore.use_cases.score_analysis import (
    _normalize_profile_name,
    _normalize_reometer_label,
    _resolve_profile_reometer_label,
)


class _DummyEnsaio:
    reometro_alta = "BRANCO"
    reometro_baixa = "PRETO"


def test_normalize_reometer_label_maps_branco_to_cinza():
    assert _normalize_reometer_label("BRANCO") == "Cinza"
    assert _normalize_reometer_label("reometro cinza") == "Cinza"
    assert _normalize_reometer_label("PRETO") == "Preto"
    assert _normalize_reometer_label("NRS") is None
    assert _normalize_reometer_label(None) is None


def test_normalize_profile_name_maps_branco_to_cinza():
    assert _normalize_profile_name("Alta (BRANCO)") == "Alta (CINZA)"
    assert _normalize_profile_name("Alta (Branco)") == "Alta (Cinza)"


def test_resolve_profile_reometer_label_uses_profile_then_ensaio():
    ensaio = _DummyEnsaio()

    assert _resolve_profile_reometer_label("alta", "Alta (Preto)", ensaio) == "Preto"
    assert _resolve_profile_reometer_label("alta", None, ensaio) == "Cinza"
    assert _resolve_profile_reometer_label("baixa", None, ensaio) == "Preto"
