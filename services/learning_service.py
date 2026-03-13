import warnings
import re
from datetime import datetime

from sqlalchemy import func

from models.aprendizado import CorrecaoAprendizado
from models.consolidado import EnsaioConsolidado
from models.formula import Formula
from models.usuario import Usuario, db


def _norm(texto):
    return str(texto or "").strip().upper()


def _compact_lote_key(texto):
    return re.sub(r"[^A-Z0-9]", "", _norm(texto))


def _aliases_lote_key(texto):
    base = _norm(texto)
    if not base:
        return []

    aliases = [base]

    if re.fullmatch(r"\d+\.0+", base):
        aliases.append(base.split(".", 1)[0])

    compact = _compact_lote_key(base)
    if compact and compact not in aliases:
        aliases.append(compact)

    return aliases


def _resolver_massa_id(nome_massa):
    valor = str(nome_massa or "").strip()
    if not valor:
        raise ValueError("Massa invalida para salvar aprendizado.")

    if valor.isdigit():
        return int(valor)

    alvo = _norm(valor)

    formula = Formula.query.filter(func.upper(Formula.ds_composto) == alvo).first()
    if formula:
        return int(formula.cd_produto)

    ensaio = (
        EnsaioConsolidado.query.with_entities(EnsaioConsolidado.cod_sankhya)
        .filter(
            func.upper(EnsaioConsolidado.massa_descricao) == alvo,
            EnsaioConsolidado.cod_sankhya.isnot(None),
        )
        .order_by(EnsaioConsolidado.updated_at.desc(), EnsaioConsolidado.data_hora.desc())
        .first()
    )
    if ensaio and ensaio.cod_sankhya is not None:
        return int(ensaio.cod_sankhya)

    raise ValueError(f"Massa '{nome_massa}' nao encontrada para aprendizado.")


def _resolver_usuario_id(usuario):
    username = str(usuario or "").strip()
    if not username:
        return None

    user = Usuario.query.filter(func.upper(Usuario.username) == username.upper()).first()
    return int(user.id) if user else None


def _buscar_nomes_massas_por_codigo(codigos):
    if not codigos:
        return {}

    nomes = {}
    rows = (
        EnsaioConsolidado.query.with_entities(
            EnsaioConsolidado.cod_sankhya,
            EnsaioConsolidado.massa_descricao,
        )
        .filter(
            EnsaioConsolidado.cod_sankhya.in_(list(codigos)),
            EnsaioConsolidado.massa_descricao.isnot(None),
        )
        .order_by(EnsaioConsolidado.updated_at.desc(), EnsaioConsolidado.data_hora.desc())
        .all()
    )
    for cod, nome in rows:
        if cod is None or not nome:
            continue
        cod_int = int(cod)
        if cod_int not in nomes:
            nomes[cod_int] = _norm(nome)
    return nomes


def carregar_aprendizado_mapa():
    """
    Retorna o mapa de aprendizado persistido no SQLAlchemy:
      {
        "CHAVE_ORIGINAL": {"lote_real": "LOTE_CORRETO", "massa": "NOME_MASSA"}
      }
    """
    rows = (
        db.session.query(CorrecaoAprendizado, Formula.ds_composto)
        .outerjoin(Formula, Formula.cd_produto == CorrecaoAprendizado.massa_id)
        .order_by(CorrecaoAprendizado.data_correcao.desc(), CorrecaoAprendizado.id.desc())
        .all()
    )

    massa_ids_sem_formula = {
        int(correcao.massa_id)
        for correcao, massa_nome in rows
        if correcao.massa_id is not None and not massa_nome
    }
    fallback_nomes = _buscar_nomes_massas_por_codigo(massa_ids_sem_formula)

    dados = {}
    for correcao, massa_nome in rows:
        chave = _norm(correcao.lote_original)
        lote_real = _norm(correcao.lote_correto) or chave
        massa = _norm(massa_nome) if massa_nome else fallback_nomes.get(int(correcao.massa_id), _norm(correcao.massa_id))
        payload = {"lote_real": lote_real, "massa": massa}
        for alias in _aliases_lote_key(chave):
            if alias not in dados:
                dados[alias] = payload

    return dados


def carregar_aprendizado():
    """
    DEPRECATED: manter apenas para compatibilidade temporaria.
    Retorna:
      {
        "CHAVE_ORIGINAL": {"lote_real": "LOTE_CORRETO", "massa": "NOME_MASSA"}
      }
    """
    warnings.warn(
        "carregar_aprendizado() esta deprecated. Use queries em CorrecaoAprendizado.",
        DeprecationWarning,
        stacklevel=2,
    )

    return carregar_aprendizado_mapa()


def ensinar_lote(string_original, lote_correto, nome_massa, usuario=None, diferenca_dureza=None):
    key = _norm(string_original)
    if not key:
        raise ValueError("lote_original vazio.")

    lote = _norm(lote_correto) if lote_correto else key
    massa_id = _resolver_massa_id(nome_massa)
    usuario_id = _resolver_usuario_id(usuario)

    registro = CorrecaoAprendizado.query.filter_by(lote_original=key).first()
    if registro:
        registro.lote_correto = lote
        registro.massa_id = massa_id
        registro.usuario_id = usuario_id
        registro.data_correcao = datetime.now()
        if diferenca_dureza is not None:
            registro.diferenca_dureza = float(diferenca_dureza)
    else:
        registro = CorrecaoAprendizado(
            lote_original=key,
            lote_correto=lote,
            massa_id=massa_id,
            usuario_id=usuario_id,
            data_correcao=datetime.now(),
            diferenca_dureza=(float(diferenca_dureza) if diferenca_dureza is not None else None),
        )
        db.session.add(registro)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return True
