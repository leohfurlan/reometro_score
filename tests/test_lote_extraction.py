import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.etl_service as etl_service
from reoscore.use_cases.learning_corrections import _score_extracted_lot


def test_rotulo_reometro_descarta_nomes_nao_cinza_preto():
    assert etl_service._rotulo_reometro_por_descricao("REOMETRO NRS") is None
    assert etl_service._rotulo_reometro_por_descricao("REOMETRO CAMELBACK CPA CVM") is None
    assert etl_service._rotulo_reometro_por_descricao("REOMETRO PRETO") == "PRETO"
    assert etl_service._rotulo_reometro_por_descricao("REOMETRO BRANCO") == "CINZA"


def test_extracao_generica_preserva_zero_significativo_no_final(monkeypatch):
    monkeypatch.setattr(etl_service, "_MAPA_LOTES_PLANILHA", {})

    # Cenarios reportados: evitar reduzir 10380->1038 e 10400->1040.
    assert etl_service._extrair_por_numeros("Lote 10380000", exigir_mapa=False) == "10380"
    assert etl_service._extrair_por_numeros("Lote 10400000", exigir_mapa=False) == "10400"
    assert etl_service.extrair_lote_da_string("Lote 10380000")[0] == "10380"


def test_score_cleanup_prefere_lote_com_comprimento_referencia():
    # Ao comparar duas extracoes validas, 10380 deve ser preferido a 1038.
    assert _score_extracted_lot("10380", "Generico") < _score_extracted_lot("1038", "Generico")
