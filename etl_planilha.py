import pandas as pd
import os
import shutil
import tempfile
import warnings
from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

# Ignora avisos do Excel (estilo bordas, formatação, etc.)
warnings.simplefilter("ignore")

CACHE_PADRAO_SHAREPOINT = os.path.abspath("cache_reg403_sharepoint.xlsx")

def _resolver_caminho_planilha():
    """
    Retorna o caminho local para o arquivo baixado via SharePoint.
    Dá preferência à variável CAMINHO_REG403 (definida pelo app)
    e, se ausente, tenta o cache padrão gerado pelo sharepoint_loader.
    """
    caminho_arquivo = os.getenv("CAMINHO_REG403")
    if caminho_arquivo:
        return caminho_arquivo.replace('"', '')

    if os.path.exists(CACHE_PADRAO_SHAREPOINT):
        return CACHE_PADRAO_SHAREPOINT

    return None

def carregar_dicionario_lotes():
    caminho_arquivo = _resolver_caminho_planilha()
    
    print(f"--- 📂 ETL: Carregando Planilha de Lotes ---")
    
    if not caminho_arquivo:
        print("❌ ERRO: Caminho do arquivo do SharePoint não definido. Execute a sincronização primeiro.")
        return {}

    if not os.path.exists(caminho_arquivo):
        print("❌ ERRO: Arquivo de lote não encontrado.")
        print(f"   -> Caminho buscado: {caminho_arquivo}")
        return {}

    # print(f"   > Lendo arquivo: ...{str(caminho_arquivo)[-40:]}")

    # 2. Clone Temporário 
    # (Mantemos essa prática para evitar travar o arquivo se ele estiver aberto no Excel localmente)
    temp_dir = tempfile.gettempdir()
    caminho_clone = os.path.join(temp_dir, "temp_reg403_leitura.xlsx")

    try:
        shutil.copy2(caminho_arquivo, caminho_clone)
    except Exception as e:
        print(f"⚠️ Aviso: Não foi possível criar cópia temporária. Tentando ler direto. Erro: {e}")
        caminho_clone = caminho_arquivo

    mapa_lote_massa = {}
    
    # Abas que o sistema vai procurar
    abas_para_ler = ['2023', '2024', '2025', '2026'] 
    
    try:
        # Abre o arquivo (usando engine openpyxl para .xlsx)
        # Lemos o arquivo inteiro uma vez para pegar os nomes das abas, 
        # mas para performance, o pandas já carrega sob demanda.
        xls = pd.ExcelFile(caminho_clone, engine='openpyxl')
        
        for aba in abas_para_ler:
            if aba not in xls.sheet_names:
                continue
                
            try:
                # header=1: Pula a primeira linha (títulos visuais) e pega o cabeçalho real
                df = pd.read_excel(xls, sheet_name=aba, header=1)
                
                # Normaliza nomes das colunas (Maiúsculo e sem espaços)
                df.columns = [str(col).strip().upper() for col in df.columns]

                # Correção específica para abas onde a coluna MASSA pode estar deslocada
                if 'MASSA' not in df.columns and len(df.columns) > 3:
                    # Tenta pegar a 3ª coluna como Massa (índice 2)
                    col_index_2 = df.columns[2] 
                    df.rename(columns={col_index_2: 'MASSA'}, inplace=True)

                if 'LOTE' not in df.columns or 'MASSA' not in df.columns:
                    # Se mesmo assim não achar, pula a aba
                    continue

                # --- NOVA LÓGICA: Captura da Coluna REOMETRO ---
                # Procura por colunas que contenham "REOMETRO" (ex: "REOMETRO (ALTA)")
                col_equip = None
                for col in df.columns:
                    if "REOMETRO" in col and "ALTA" in col:
                        col_equip = col
                        break

                # Limpeza dos dados
                df = df.dropna(subset=['LOTE', 'MASSA'])
                df['LOTE'] = df['LOTE'].astype(str).str.strip().str.upper()
                df['MASSA'] = df['MASSA'].astype(str).str.strip()
                
                # Filtra lixo (lotes com menos de 3 caracteres)
                df = df[df['LOTE'].str.len() > 2] 
                
                # Itera para montar o dicionário rico
                for _, row in df.iterrows():
                    lote = str(row['LOTE']).strip().upper()
                    if len(lote) < 3: continue
                    
                    massa = str(row['MASSA']).strip()
                    
                    # Captura o equipamento se a coluna existir e tiver valor
                    equip = None
                    if col_equip and pd.notna(row[col_equip]):
                        val_equip = str(row[col_equip]).strip().upper()
                        if "CINZA" in val_equip: equip = "CINZA"
                        elif "PRETO" in val_equip: equip = "PRETO"
                    
                    mapa_lote_massa[lote] = {
                        'massa': massa,
                        'equipamento': equip
                    }
                
            except Exception as e:
                print(f"⚠️ Aviso na aba '{aba}': {e}")
        
        xls.close()
                
    except Exception as e:
        print(f"❌ Erro crítico ao ler planilha Excel: {e}")
    finally:
        # Remove o arquivo temporário se ele foi criado
        if caminho_clone != caminho_arquivo and os.path.exists(caminho_clone):
            try: os.remove(caminho_clone)
            except: pass

    print(f"✅ SUCESSO: {len(mapa_lote_massa)} lotes carregados da planilha.")
    return mapa_lote_massa

if __name__ == "__main__":
    # Teste rápido se rodar o script diretamente
    dic = carregar_dicionario_lotes()
    print(f"Amostra: {list(dic.items())[:3]}")
