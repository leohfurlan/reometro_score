import os
import re
import shutil
import tempfile
import unicodedata
import warnings
from datetime import datetime

import pandas as pd
from sqlalchemy import func

from models.consolidado import EnsaioConsolidado
from services.etl_service import match_nome_inteligente

BANBURY_ALVOS = ("100L", "80L", "40L")
_SHEET_ANO_PATTERN = re.compile(r"^\d{4}$")
_VALORES_REOMETRO_INVALIDOS = {"", "-", "--", "---", "NAN", "NONE", "N/A"}


def _normalizar_texto(valor):
    txt = str(valor or "").strip()
    if not txt:
        return ""
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return txt.upper()


def _normalizar_lote(valor):
    if pd.isna(valor):
        return ""

    lote = str(valor).strip().upper()
    if not lote or lote == "NAN":
        return ""

    if re.fullmatch(r"\d+\.0+", lote):
        lote = lote.split(".", 1)[0]

    return lote


def _normalizar_banbury(valor):
    txt = _normalizar_texto(valor)
    if not txt:
        return None

    for numero in re.findall(r"\d+", txt):
        if numero in {"100", "80", "40"}:
            return f"{numero}L"
    return None


def normalizar_reometro(valor, descricao_produto=""):
    txt = _normalizar_texto(valor)
    if txt in _VALORES_REOMETRO_INVALIDOS:
        txt = _normalizar_texto(descricao_produto)

    if any(token in txt for token in ("PRETO", "PRETA")):
        return "preto"
    if any(token in txt for token in ("CINZA", "BRANCO", "BRANCA")):
        return "cinza"
    return None


def _resolver_caminho_planilha():
    caminho_ambiente = os.getenv("CAMINHO_REG403")
    if caminho_ambiente:
        caminho = caminho_ambiente.replace('"', "").strip()
        if caminho and os.path.exists(caminho):
            return caminho

    for fallback in ("cache_reg403_sharepoint.xlsx", "cache_reg403.xlsx"):
        caminho = os.path.abspath(fallback)
        if os.path.exists(caminho):
            return caminho

    return None


def _selecionar_coluna(colunas, candidatos_exatos=None, tokens_obrigatorios=None):
    candidatos_exatos = candidatos_exatos or ()
    tokens_obrigatorios = tokens_obrigatorios or ()

    mapa_norm = {col: _normalizar_texto(col) for col in colunas}
    exatos_norm = {_normalizar_texto(c) for c in candidatos_exatos}
    tokens_norm = [_normalizar_texto(t) for t in tokens_obrigatorios if _normalizar_texto(t)]

    for col, col_norm in mapa_norm.items():
        if col_norm in exatos_norm:
            return col

    if tokens_norm:
        for col, col_norm in mapa_norm.items():
            if all(token in col_norm for token in tokens_norm):
                return col

    return None


def _coluna_massa(colunas):
    col = _selecionar_coluna(
        colunas,
        candidatos_exatos=("MASSA", "MASSAS"),
        tokens_obrigatorios=("MASSA",),
    )
    if col:
        return col

    if len(colunas) >= 3:
        terceira = colunas[2]
        terceira_norm = _normalizar_texto(terceira)
        if terceira_norm.startswith("UNNAMED") or terceira_norm == "":
            return terceira
    return None


def _combinar_data_hora(df, coluna_data, coluna_hora):
    data_base = pd.to_datetime(df[coluna_data], errors="coerce")

    if not coluna_hora:
        return data_base

    serie_hora = df[coluna_hora]
    hora_str = serie_hora.astype(str).str.strip()
    hora_str = hora_str.replace({"NaT": "", "nan": "", "None": ""})
    delta_hora = pd.to_timedelta(hora_str, errors="coerce")

    hora_num = pd.to_numeric(serie_hora, errors="coerce")
    delta_hora = delta_hora.fillna(pd.to_timedelta(hora_num, unit="D"))
    delta_hora = delta_hora.fillna(pd.Timedelta(0))

    return data_base + delta_hora


def _resolver_produto(descricao_bruta):
    descricao = str(descricao_bruta or "").strip()
    if not descricao or descricao.upper() == "NAN":
        return None, ""

    try:
        produto = match_nome_inteligente(descricao)
    except Exception:
        produto = None

    if not produto:
        codigo_db, descricao_db = _buscar_produto_consolidado(descricao)
        return codigo_db, (descricao_db or descricao)

    codigo = getattr(produto, "cod_sankhya", None) or getattr(produto, "cod", None)
    descricao_catalogo = getattr(produto, "descricao", None) or descricao

    if codigo is None:
        codigo_db, descricao_db = _buscar_produto_consolidado(descricao_catalogo)
        if codigo_db:
            return codigo_db, (descricao_db or str(descricao_catalogo).strip())

    return (str(codigo) if codigo is not None else None), str(descricao_catalogo).strip()


def _buscar_produto_consolidado(descricao):
    descricao_norm = _normalizar_texto(descricao)
    if not descricao_norm:
        return None, None

    try:
        exato = (
            EnsaioConsolidado.query.with_entities(
                EnsaioConsolidado.cod_sankhya,
                EnsaioConsolidado.massa_descricao,
            )
            .filter(
                func.upper(EnsaioConsolidado.massa_descricao) == descricao_norm,
                EnsaioConsolidado.cod_sankhya.isnot(None),
            )
            .order_by(
                EnsaioConsolidado.updated_at.desc(),
                EnsaioConsolidado.data_hora.desc(),
            )
            .first()
        )
        if exato and exato.cod_sankhya is not None:
            return str(exato.cod_sankhya), str(exato.massa_descricao or descricao).strip()

        aproximado = (
            EnsaioConsolidado.query.with_entities(
                EnsaioConsolidado.cod_sankhya,
                EnsaioConsolidado.massa_descricao,
            )
            .filter(
                EnsaioConsolidado.cod_sankhya.isnot(None),
                func.upper(EnsaioConsolidado.massa_descricao).contains(descricao_norm),
            )
            .order_by(
                EnsaioConsolidado.updated_at.desc(),
                EnsaioConsolidado.data_hora.desc(),
            )
            .first()
        )
        if aproximado and aproximado.cod_sankhya is not None:
            return str(aproximado.cod_sankhya), str(aproximado.massa_descricao or descricao).strip()
    except Exception:
        return None, None

    return None, None


def _iter_registros_planilha(caminho_planilha):
    warnings.simplefilter("ignore")
    caminho_clone = caminho_planilha

    try:
        caminho_clone = os.path.join(tempfile.gettempdir(), "temp_reg403_dashboard_banbury.xlsx")
        shutil.copy2(caminho_planilha, caminho_clone)
    except Exception:
        caminho_clone = caminho_planilha

    try:
        xls = pd.ExcelFile(caminho_clone, engine="openpyxl")
        for nome_aba in xls.sheet_names:
            if not _SHEET_ANO_PATTERN.fullmatch(str(nome_aba).strip()):
                continue

            try:
                df = pd.read_excel(xls, sheet_name=nome_aba, header=1)
            except Exception:
                continue

            if df is None or df.empty:
                continue

            colunas = [str(c).strip() for c in df.columns]
            coluna_data = _selecionar_coluna(
                colunas,
                candidatos_exatos=("DATA DE ENTRADA", "DATA"),
                tokens_obrigatorios=("DATA",),
            )
            coluna_hora = _selecionar_coluna(
                colunas,
                tokens_obrigatorios=("HORARIO", "LOTE"),
            )
            coluna_massa = _coluna_massa(colunas)
            coluna_banbury = _selecionar_coluna(
                colunas,
                candidatos_exatos=("BANBURY",),
                tokens_obrigatorios=("BANBURY",),
            )
            coluna_lote = _selecionar_coluna(
                colunas,
                candidatos_exatos=("LOTE",),
                tokens_obrigatorios=("LOTE",),
            )
            coluna_reometro = _selecionar_coluna(
                colunas,
                tokens_obrigatorios=("REOMETRO",),
            )

            if not coluna_data or not coluna_banbury or not coluna_lote:
                continue

            data_hora = _combinar_data_hora(df, coluna_data, coluna_hora)
            for idx, dt in data_hora.items():
                if pd.isna(dt):
                    continue

                equipamento = _normalizar_banbury(df.at[idx, coluna_banbury])
                if equipamento not in BANBURY_ALVOS:
                    continue

                massa = df.at[idx, coluna_massa] if coluna_massa in df.columns else None
                lote = _normalizar_lote(df.at[idx, coluna_lote])
                if not lote:
                    continue

                reometro_raw = df.at[idx, coluna_reometro] if coluna_reometro in df.columns else ""

                yield {
                    "equipamento": equipamento,
                    "data_hora": dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt,
                    "descricao_bruta": "" if pd.isna(massa) else str(massa).strip(),
                    "lote": lote,
                    "reometro_raw": "" if pd.isna(reometro_raw) else str(reometro_raw).strip(),
                    "origem_aba": str(nome_aba),
                }
    finally:
        if caminho_clone != caminho_planilha and os.path.exists(caminho_clone):
            try:
                os.remove(caminho_clone)
            except Exception:
                pass


def _card_sem_dados(equipamento):
    return {
        "equipamento": f"Banbury {equipamento}",
        "codigo": None,
        "descricao": None,
        "lote": None,
        "reometro": None,
        "data_hora": None,
        "origem_aba": None,
        "status": "sem_dados",
        "mensagem": "Sem dados recentes.",
    }


def _resolver_reometro_final(registro_principal, registros_ordenados):
    reometro = normalizar_reometro(
        registro_principal.get("reometro_raw"),
        registro_principal.get("descricao_bruta"),
    )
    if reometro:
        return reometro

    equipamento = registro_principal.get("equipamento")
    lote = registro_principal.get("lote")
    descricao_norm = _normalizar_texto(registro_principal.get("descricao_bruta"))

    for item in registros_ordenados:
        if item.get("equipamento") != equipamento:
            continue
        if item.get("lote") != lote:
            continue
        reometro_item = normalizar_reometro(item.get("reometro_raw"), item.get("descricao_bruta"))
        if reometro_item:
            return reometro_item

    if descricao_norm:
        for item in registros_ordenados:
            if item.get("equipamento") != equipamento:
                continue
            if _normalizar_texto(item.get("descricao_bruta")) != descricao_norm:
                continue
            reometro_item = normalizar_reometro(item.get("reometro_raw"), item.get("descricao_bruta"))
            if reometro_item:
                return reometro_item

    for item in registros_ordenados:
        if item.get("equipamento") != equipamento:
            continue
        reometro_item = normalizar_reometro(item.get("reometro_raw"), item.get("descricao_bruta"))
        if reometro_item:
            return reometro_item

    return None


def obter_status_producao_banbury():
    cards = [_card_sem_dados(eq) for eq in BANBURY_ALVOS]
    caminho_planilha = _resolver_caminho_planilha()

    if not caminho_planilha:
        return {
            "status": "erro",
            "mensagem": "Planilha do SharePoint nao encontrada no cache local.",
            "arquivo": None,
            "cards": cards,
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        }

    try:
        registros = list(_iter_registros_planilha(caminho_planilha))
    except Exception as exc:
        return {
            "status": "erro",
            "mensagem": f"Falha ao ler planilha do SharePoint: {exc}",
            "arquivo": caminho_planilha,
            "cards": cards,
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        }

    if not registros:
        return {
            "status": "ok",
            "mensagem": None,
            "arquivo": caminho_planilha,
            "cards": cards,
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        }

    registros.sort(key=lambda item: item["data_hora"], reverse=True)
    latest_por_equip = {}
    for item in registros:
        latest_por_equip.setdefault(item["equipamento"], item)

    resultado_cards = []
    for equipamento in BANBURY_ALVOS:
        row = latest_por_equip.get(equipamento)
        if not row:
            resultado_cards.append(_card_sem_dados(equipamento))
            continue

        codigo, descricao = _resolver_produto(row.get("descricao_bruta"))
        reometro = _resolver_reometro_final(row, registros)

        resultado_cards.append(
            {
                "equipamento": f"Banbury {equipamento}",
                "codigo": codigo,
                "descricao": descricao or row.get("descricao_bruta") or None,
                "lote": row.get("lote"),
                "reometro": reometro,
                "data_hora": (
                    row["data_hora"].isoformat(timespec="seconds")
                    if isinstance(row.get("data_hora"), datetime)
                    else None
                ),
                "origem_aba": row.get("origem_aba"),
                "status": "ok",
                "mensagem": None,
            }
        )

    return {
        "status": "ok",
        "mensagem": None,
        "arquivo": caminho_planilha,
        "cards": resultado_cards,
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
    }
