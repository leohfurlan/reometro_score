import os
from datetime import datetime

from models.consolidado import EnsaioConsolidado

from .extensions import cache_service

CACHE_PLANILHA_SHAREPOINT = "cache_reg403_sharepoint.xlsx"


def recarregar_cache_memoria(overlay_fn=None):
    """
    Recarrega o cache em memoria usado pelas telas operacionais.
    """
    try:
        print("[INFO] Recarregando cache em memoria a partir do banco...")
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
        print(f"[OK] Cache atualizado com {len(todos_ensaios)} registros.")
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
