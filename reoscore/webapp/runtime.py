import os
from datetime import datetime

from models.consolidado import EnsaioConsolidado

from .extensions import cache_service

CACHE_PLANILHA_SHAREPOINT = "cache_reg403_sharepoint.xlsx"


def _normalizar_ids(ids_prioritarios):
    if not ids_prioritarios:
        return []

    ids_norm = []
    vistos = set()
    for value in ids_prioritarios:
        try:
            i = int(value)
        except Exception:
            continue
        if i <= 0 or i in vistos:
            continue
        vistos.add(i)
        ids_norm.append(i)
    return ids_norm


def _merge_cache_por_id(base, delta):
    mapa = {}
    for ensaio in (base or []):
        try:
            chave = int(getattr(ensaio, "id_ensaio", 0) or 0)
        except Exception:
            continue
        if chave > 0:
            mapa[chave] = ensaio

    for ensaio in (delta or []):
        try:
            chave = int(getattr(ensaio, "id_ensaio", 0) or 0)
        except Exception:
            continue
        if chave > 0:
            mapa[chave] = ensaio

    return sorted(
        mapa.values(),
        key=lambda e: (getattr(e, "data_hora", None) or datetime.min),
        reverse=True,
    )


def recarregar_cache_memoria(overlay_fn=None, ids_prioritarios=None, force_full=False):
    """
    Recarrega o cache operacional.
    Se houver cache em memoria, tenta aplicar apenas os registros novos/alterados.
    """
    try:
        print("[INFO] Recarregando cache em memoria...")
        ids_filtro = _normalizar_ids(ids_prioritarios)
        snapshot = cache_service.peek()
        usar_incremental = bool(not force_full and snapshot and snapshot.get("dados"))

        if usar_incremental:
            query = EnsaioConsolidado.query
            delta = []

            if ids_filtro:
                delta = query.filter(EnsaioConsolidado.id_ensaio.in_(ids_filtro)).all()
            else:
                ultimo_cache = snapshot.get("ultimo_update")
                if ultimo_cache:
                    delta = query.filter(EnsaioConsolidado.updated_at >= ultimo_cache).all()

            dados_cache = _merge_cache_por_id(snapshot.get("dados"), delta)
            if callable(overlay_fn):
                dados_cache = overlay_fn(dados_cache)

            if not dados_cache:
                print("[WARN] Cache incremental vazio. Mantendo cache atual.")
                return len(snapshot.get("dados") or [])

            materiais_unicos = sorted({e.massa_descricao for e in dados_cache if e.massa_descricao})
            cache_service.set(
                {
                    "dados": dados_cache,
                    "materiais": materiais_unicos,
                    "ultimo_update": datetime.now(),
                }
            )
            print(
                f"[OK] Cache incremental: {len(delta)} alterados, "
                f"total em memoria {len(dados_cache)}."
            )
            return len(dados_cache)

        print("[INFO] Executando recarga completa do cache...")
        todos_ensaios = EnsaioConsolidado.query.order_by(EnsaioConsolidado.data_hora.desc()).all()

        if callable(overlay_fn):
            todos_ensaios = overlay_fn(todos_ensaios)

        if not todos_ensaios:
            print("[WARN] Banco de dados vazio. Cache nao atualizado.")
            return 0

        materiais_unicos = sorted({ensaio.massa_descricao for ensaio in todos_ensaios if ensaio.massa_descricao})
        cache_service.set(
            {
                "dados": todos_ensaios,
                "materiais": materiais_unicos,
                "ultimo_update": datetime.now(),
            }
        )
        print(f"[OK] Cache completo atualizado com {len(todos_ensaios)} registros.")
        return len(todos_ensaios)
    except Exception as exc:
        print(f"[ERRO] Erro ao recarregar cache: {exc}")
        return 0


def preparar_planilha_sharepoint(download_excel_fn, forcar_download=False, nome_cache=CACHE_PLANILHA_SHAREPOINT):
    """
    Baixa ou reaproveita a planilha do SharePoint e atualiza CAMINHO_REG403.
    """
    if not download_excel_fn:
        return None

    caminho_cache = os.path.abspath(nome_cache)
    if not forcar_download and os.path.exists(caminho_cache) and os.path.getsize(caminho_cache) > 0:
        os.environ["CAMINHO_REG403"] = caminho_cache
        return caminho_cache

    try:
        caminho_baixado = download_excel_fn(nome_destino=nome_cache)
        if caminho_baixado:
            caminho_abs = os.path.abspath(caminho_baixado)
            os.environ["CAMINHO_REG403"] = caminho_abs
            return caminho_abs
    except Exception as exc:
        print(f"[WARN] Falha ao baixar planilha do SharePoint: {exc}")

    return None


def bootstrap_operacional(app, download_excel_fn, carregar_referencias_fn, recarregar_cache_fn=None):
    """
    Executa o warmup operacional da aplicacao sem acoplar o bootstrap Flask ao app.py.
    """
    cache_loader = recarregar_cache_fn or recarregar_cache_memoria

    with app.app_context():
        if EnsaioConsolidado.query.first():
            cache_loader()

    print("\n=== REOSCORE V13 (MODULARIZED & SIDECAR) ===")

    caminho_sharepoint = preparar_planilha_sharepoint(download_excel_fn, forcar_download=False)
    if caminho_sharepoint:
        print(f"  > Planilha SharePoint configurada em: {caminho_sharepoint}")
    else:
        print("[WARN] Aviso: Planilha do SharePoint nao configurada. Use 'Atualizar Dados' para sincronizar.")

    with app.app_context():
        carregar_referencias_fn()

    return caminho_sharepoint
