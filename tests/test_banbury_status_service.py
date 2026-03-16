import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import banbury_status_service as service


class _ProdutoFake:
    def __init__(self, cod_sankhya, descricao):
        self.cod_sankhya = cod_sankhya
        self.descricao = descricao


def _escrever_aba_ano(writer, nome_aba, rows):
    df = pd.DataFrame(rows)
    df.to_excel(writer, sheet_name=nome_aba, index=False, startrow=1)


def test_normalizar_reometro_suporta_variantes():
    assert service.normalizar_reometro("PRETO") == "preto"
    assert service.normalizar_reometro("branca") == "cinza"
    assert service.normalizar_reometro("---", "MASSA PRETA") == "preto"
    assert service.normalizar_reometro(None, "MASSA CINZA") == "cinza"


def test_obter_status_producao_banbury_retorna_mais_recente_por_equipamento(monkeypatch, tmp_path):
    arquivo = tmp_path / "planilha_sharepoint.xlsx"
    with pd.ExcelWriter(arquivo, engine="openpyxl") as writer:
        _escrever_aba_ano(
            writer,
            "2026",
            [
                {
                    "DATA DE ENTRADA": "2026-03-10",
                    "HORARIO QUE COMECOU O LOTE": "08:00:00",
                    "MASSAS": "MASSA A",
                    "BANBURY": "100",
                    "LOTE": "9001",
                    "REOMETRO (ALTA)": "PRETO",
                },
                {
                    "DATA DE ENTRADA": "2026-03-11",
                    "HORARIO QUE COMECOU O LOTE": "10:00:00",
                    "MASSAS": "MASSA PRETA",
                    "BANBURY": "100",
                    "LOTE": "9002",
                    "REOMETRO (ALTA)": "---",
                },
                {
                    "DATA DE ENTRADA": "2026-03-11",
                    "HORARIO QUE COMECOU O LOTE": "09:00:00",
                    "MASSAS": "MASSA CINZA",
                    "BANBURY": "80",
                    "LOTE": "8001",
                    "REOMETRO (ALTA)": "CINZA",
                },
                {
                    "DATA DE ENTRADA": "2026-03-10",
                    "HORARIO QUE COMECOU O LOTE": "07:30:00",
                    "MASSAS": "MASSA AZUL",
                    "BANBURY": "40",
                    "LOTE": "4001",
                    "REOMETRO (ALTA)": "CINZA",
                },
                {
                    "DATA DE ENTRADA": "2026-03-11",
                    "HORARIO QUE COMECOU O LOTE": "12:00:00",
                    "MASSAS": "MASSA AZUL",
                    "BANBURY": "40",
                    "LOTE": "4002",
                    "REOMETRO (ALTA)": "---",
                },
            ],
        )

    monkeypatch.setenv("CAMINHO_REG403", str(arquivo))

    def _fake_match(nome):
        nome_norm = str(nome).strip().upper()
        if nome_norm == "MASSA PRETA":
            return _ProdutoFake(1002, "MASSA PRETA")
        if nome_norm == "MASSA CINZA":
            return _ProdutoFake(8001, "MASSA CINZA")
        if nome_norm == "MASSA AZUL":
            return _ProdutoFake(4002, "MASSA AZUL")
        return None

    monkeypatch.setattr(service, "match_nome_inteligente", _fake_match)

    resultado = service.obter_status_producao_banbury()
    assert resultado["status"] == "ok"
    assert len(resultado["cards"]) == 3

    cards = {c["equipamento"]: c for c in resultado["cards"]}

    card_100 = cards["Banbury 100L"]
    assert card_100["status"] == "ok"
    assert card_100["codigo"] == "1002"
    assert card_100["descricao"] == "MASSA PRETA"
    assert card_100["lote"] == "9002"
    assert card_100["reometro"] == "preto"

    card_80 = cards["Banbury 80L"]
    assert card_80["status"] == "ok"
    assert card_80["codigo"] == "8001"
    assert card_80["lote"] == "8001"
    assert card_80["reometro"] == "cinza"

    card_40 = cards["Banbury 40L"]
    assert card_40["status"] == "ok"
    assert card_40["codigo"] == "4002"
    assert card_40["lote"] == "4002"
    assert card_40["reometro"] == "cinza"


def test_obter_status_producao_banbury_retorna_erro_quando_nao_ha_planilha(monkeypatch):
    monkeypatch.delenv("CAMINHO_REG403", raising=False)
    monkeypatch.setattr(service.os.path, "exists", lambda _path: False)
    resultado = service.obter_status_producao_banbury()

    assert resultado["status"] == "erro"
    assert len(resultado["cards"]) == 3
    assert all(card["status"] == "sem_dados" for card in resultado["cards"])
