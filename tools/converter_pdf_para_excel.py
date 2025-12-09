import pdfplumber
import pandas as pd
import re
import os
import sys

# Adiciona o diretório raiz ao path para importar os serviços do app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- IMPORTAÇÃO DA INTELIGÊNCIA DO SISTEMA ---
from app import app
from services.etl_service import carregar_referencias_estaticas, match_nome_inteligente

# --- CONFIGURAÇÃO ---
ARQUIVO_PDF_REO = "TAB 403 - 5 Parâmetros para Liberação de Massas ( Versão 24).pdf"
ARQUIVO_PDF_VISC = "TAB 403 - 6  Parâmetros para analise de Viscosidade (Versão 19).pdf"

PATH_SAIDA_REO = "planilhas/dados_reometria_convertidos.xlsx"
PATH_SAIDA_VISC = "planilhas/dados_viscosidade_convertidos.xlsx"

def tempo_para_segundos(valor):
    """Converte '01:30' ou '5' (minutos) para segundos (int)."""
    if not valor: return None
    v = str(valor).strip().replace(',', '.')
    try:
        if ':' in v:
            partes = v.split(':')
            return int(partes[0]) * 60 + int(partes[1])
        else:
            return int(float(v) * 60)
    except:
        return None

def limpar_numero(val):
    """Limpa strings como '198\n' para float."""
    if not val: return None
    limpo = re.sub(r'[^\d.,]', '', str(val))
    if not limpo: return None
    return float(limpo.replace(',', '.'))

def calcular_alvo(min_v, max_v):
    """Calcula a média entre mínimo e máximo."""
    if min_v is not None and max_v is not None:
        return (min_v + max_v) / 2
    return None

def identificar_produto(nome_pdf):
    """Usa a inteligência do ETL Service para achar o produto."""
    if not nome_pdf: return None, "Vazio"
    
    # Remove quebras de linha que o PDF pode ter inserido no meio do nome
    nome_limpo = str(nome_pdf).replace('\n', ' ').strip()
    
    obj = match_nome_inteligente(nome_limpo)
    if obj:
        return obj.cod_sankhya, obj.descricao
    return None, "Não Identificado"

def processar_reometria():
    print(f"--- 📄 Processando Reometria (com Smart Match): {ARQUIVO_PDF_REO} ---")
    dados = []
    
    if not os.path.exists(ARQUIVO_PDF_REO):
        print("❌ Arquivo PDF não encontrado.")
        return

    with pdfplumber.open(ARQUIVO_PDF_REO) as pdf:
        tipo_atual = "ALTA"
        
        for page in pdf.pages:
            tabelas = page.extract_tables()
            for tabela in tabelas:
                for row in tabela:
                    row_limpa = [str(c).replace('\n', ' ').strip() if c else '' for c in row]
                    texto_completo = " ".join(row_limpa).upper()

                    # Detecção de Seção
                    if "BAIXA TEMPERATURA" in texto_completo: tipo_atual = "BAIXA"; continue
                    if "ALTA TEMPERATURA" in texto_completo: tipo_atual = "ALTA"; continue
                    if "SIGLA" in texto_completo or "VULCAFLEX" in texto_completo: continue

                    if len(row) >= 7:
                        try:
                            nome = row_limpa[0]
                            if len(nome) < 3 or "T2" in nome: continue

                            # 1. Identificação Inteligente
                            cod, desc_oficial = identificar_produto(nome)

                            # 2. Extração de Valores
                            # Mapeamento posicional baseado no seu PDF
                            idx_temp = 2
                            idx_tempo = 3
                            idx_ts2_min = 4
                            idx_ts2_max = 5
                            idx_t90_min = 6
                            idx_t90_max = 7

                            ts2_min = tempo_para_segundos(row_limpa[idx_ts2_min])
                            ts2_max = tempo_para_segundos(row_limpa[idx_ts2_max])
                            t90_min = tempo_para_segundos(row_limpa[idx_t90_min]) if len(row) > 7 else None
                            t90_max = tempo_para_segundos(row_limpa[idx_t90_max]) if len(row) > 7 else None

                            item = {
                                "COD_SANKHYA": cod,
                                "DESC_SANKHYA": desc_oficial,
                                "NOME_PDF": nome,
                                "TIPO_PERFIL": tipo_atual,
                                "TEMP_PADRAO": limpar_numero(row_limpa[idx_temp]),
                                "TEMPO_TOTAL_S": tempo_para_segundos(row_limpa[idx_tempo]),
                                
                                "TS2_MIN": ts2_min,
                                "TS2_ALVO": calcular_alvo(ts2_min, ts2_max),
                                "TS2_MAX": ts2_max,
                                
                                "T90_MIN": t90_min,
                                "T90_ALVO": calcular_alvo(t90_min, t90_max),
                                "T90_MAX": t90_max,
                            }
                            
                            if item["TEMP_PADRAO"]: dados.append(item)
                        except: pass

    df = pd.DataFrame(dados)
    os.makedirs("planilhas", exist_ok=True)
    df.to_excel(PATH_SAIDA_REO, index=False)
    
    qtd_ok = df['COD_SANKHYA'].notnull().sum()
    print(f"✅ Reometria: {len(df)} linhas geradas.")
    print(f"   -> {qtd_ok} produtos identificados automaticamente ({len(df)-qtd_ok} pendentes).")

def processar_viscosidade():
    print(f"--- 📄 Processando Viscosidade (com Smart Match): {ARQUIVO_PDF_VISC} ---")
    dados = []
    
    if not os.path.exists(ARQUIVO_PDF_VISC):
        print("❌ Arquivo PDF não encontrado.")
        return

    with pdfplumber.open(ARQUIVO_PDF_VISC) as pdf:
        for page in pdf.pages:
            tabelas = page.extract_tables()
            for tabela in tabelas:
                for row in tabela:
                    row_limpa = [str(c).replace('\n', ' ').strip() if c else '' for c in row]
                    texto_completo = " ".join(row_limpa).upper()

                    if "MOONEY" in texto_completo or "SIGLA" in texto_completo: continue

                    if len(row) >= 4:
                        try:
                            nome = row_limpa[0]
                            if len(nome) < 3: continue
                            
                            visc_min = limpar_numero(row_limpa[-2])
                            visc_max = limpar_numero(row_limpa[-1])

                            if visc_min is None and visc_max is None: continue

                            # 1. Identificação Inteligente
                            cod, desc_oficial = identificar_produto(nome)
                            
                            dados.append({
                                "COD_SANKHYA": cod,
                                "DESC_SANKHYA": desc_oficial,
                                "NOME_PDF": nome,
                                "VISC_MIN": visc_min,
                                "VISC_ALVO": calcular_alvo(visc_min, visc_max),
                                "VISC_MAX": visc_max
                            })
                        except: pass

    df = pd.DataFrame(dados)
    df.to_excel(PATH_SAIDA_VISC, index=False)
    
    qtd_ok = df['COD_SANKHYA'].notnull().sum()
    print(f"✅ Viscosidade: {len(df)} linhas geradas.")
    print(f"   -> {qtd_ok} produtos identificados automaticamente.")

if __name__ == "__main__":
    print("⏳ Iniciando ETL de Conversão PDF -> Excel...")
    
    with app.app_context():
        # Carrega todas as tabelas auxiliares (De-Para, Catálogo, Grupos)
        # Isso garante que o match_nome_inteligente funcione com capacidade máxima
        carregar_referencias_estaticas()
        
        processar_reometria()
        processar_viscosidade()
        
    print("\n🏁 Concluído! Verifique a pasta 'planilhas/'.")