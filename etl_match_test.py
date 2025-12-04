import pandas as pd
import re
import os
import json
from datetime import datetime
from difflib import get_close_matches

# Serviços
from connection import connect_to_database
from etl_planilha import carregar_dicionario_lotes
from services.sankhya_service import importar_catalogo_sankhya

# --- CONFIGURAÇÃO ---
DATA_INICIO_TESTE = '2025-09-01' 
ARQUIVO_SAIDA = "relatorio_auditoria_v8_final.xlsx"
ARQUIVO_MAPA_GRUPOS = "mapa_tipo_equipamentos.xlsx"
ARQUIVO_DE_PARA = "de_para_massas.json" # <--- Novo dicionário
CUTOFF_FUZZY = 0.7 

def carregar_arquivos_auxiliares():
    """Carrega Mapa de Grupos e Dicionário De-Para"""
    mapa_grupos = {}
    de_para = {}
    
    # 1. Mapa de Grupos
    if os.path.exists(ARQUIVO_MAPA_GRUPOS):
        try:
            df = pd.read_excel(ARQUIVO_MAPA_GRUPOS)
            mapa_grupos = dict(zip(df['COD_GRUPO'], df['TIPO SUGERIDO']))
        except: pass
        
    # 2. De-Para (Correções)
    if os.path.exists(ARQUIVO_DE_PARA):
        try:
            with open(ARQUIVO_DE_PARA, 'r', encoding='utf-8') as f:
                de_para = json.load(f)
            print(f"✅ Dicionário de Correção carregado: {len(de_para)} termos.")
        except Exception as e:
            print(f"⚠️ Erro ao ler JSON De-Para: {e}")
            
    return mapa_grupos, de_para

def buscar_dados_brutos_sql():
    print(f"--- 1. Buscando dados do SQL Server (>= {DATA_INICIO_TESTE}) ---")
    conn = connect_to_database()
    cursor = conn.cursor()
    
    query = '''
    SELECT 
        COD_ENSAIO, 
        NUMERO_LOTE, 
        BATCH, 
        DATA,
        CODIGO as CODIGO_REOMETRO, 
        NOME as NOME_REOMETRO,
        AMOSTRA,
        COD_GRUPO
    FROM dbo.ENSAIO
    WHERE DATA >= ?
    ORDER BY DATA DESC
    '''
    
    cursor.execute(query, (datetime.strptime(DATA_INICIO_TESTE, '%Y-%m-%d'),))
    colunas = [column[0] for column in cursor.description]
    rows = cursor.fetchall()
    dados = [tuple(row) for row in rows]
    df = pd.DataFrame(dados, columns=colunas)
    conn.close()
    return df

def extrair_lote_da_string(texto_sujo, mapa_lotes):
    if not texto_sujo: return None, None
    texto = str(texto_sujo).strip().upper()
    
    # Prioridade para Asterisco
    if '*' in texto:
        partes = texto.split('*')
        if len(partes) >= 2:
            lado_direito = partes[1].strip()
            candidato = lado_direito.lstrip('0')
            if not candidato: candidato = '0'
            if candidato in mapa_lotes:
                return candidato, "Asterisco (*)"

    if texto in mapa_lotes: return texto, "Exato"

    todos_numeros = re.findall(r'\d+', texto)
    for num in reversed(todos_numeros): 
        if len(num) < 3: continue 
        candidato = num.lstrip('0')
        if candidato in mapa_lotes:
            return candidato, "Regex (Busca)"
            
    return None, None

def match_nome_inteligente(texto_bruto, catalogo_nomes, de_para):
    if not texto_bruto: return None, "Vazio", 0
    texto = str(texto_bruto).strip().upper()
    
    # --- 1. VERIFICAÇÃO DE-PARA (Correção Manual) ---
    if texto in de_para:
        nome_corrigido = de_para[texto]
        if nome_corrigido in catalogo_nomes:
            return catalogo_nomes[nome_corrigido], "Correção Manual (Excel)", 1.0
    
    # --- 2. Busca Normal ---
    if texto in catalogo_nomes: return catalogo_nomes[texto], "Exato", 1.0
    if ("MASSA " + texto) in catalogo_nomes: return catalogo_nomes["MASSA " + texto], "Add 'MASSA'", 1.0
    if texto.startswith("MASSA "):
        sem = texto.replace("MASSA ", "").strip()
        if sem in catalogo_nomes: return catalogo_nomes[sem], "Remove 'MASSA'", 1.0

    # --- 3. Fuzzy Match ---
    opcoes = list(catalogo_nomes.keys())
    matches = get_close_matches(texto, opcoes, n=1, cutoff=CUTOFF_FUZZY)
    
    if matches:
        melhor_match = matches[0]
        score = 0.8
        metodo = f"Fuzzy (~{texto})"
        
        palavras_orig = set(texto.split())
        palavras_match = set(melhor_match.split())
        diferenca = palavras_match - palavras_orig
        palavras_risco = {'ORB', 'AQ', 'PRETO', 'BRANCO', 'STD', 'ESPECIAL'}
        
        if diferenca.intersection(palavras_risco):
            metodo = f"Fuzzy AMBÍGUO ({melhor_match})"
            score = 0.5 
            
        return catalogo_nomes[melhor_match], metodo, score
        
    return None, "Não Encontrado", 0

def executar_auditoria():
    print("\n=== AUDITORIA V8 (FINAL) ===\n")

    # 1. CARREGAR
    mapa_lotes_planilha = carregar_dicionario_lotes()
    _, catalogo_sankhya = importar_catalogo_sankhya()
    mapa_grupos, de_para_correcoes = carregar_arquivos_auxiliares()
    
    df_lab = buscar_dados_brutos_sql()
    lista_analise = []
    
    print("--- Processando... ---")
    
    for index, row in df_lab.iterrows():
        id_ens = row['COD_ENSAIO']
        grupo = row['COD_GRUPO']
        lote_orig = row['NUMERO_LOTE']
        amostra = row['AMOSTRA']
        cod_reo = row['CODIGO_REOMETRO']
        
        tipo_equip = mapa_grupos.get(grupo, "INDEFINIDO")
        usar_cod_reo = (tipo_equip != "VISCOSIMETRO")
        if not usar_cod_reo: cod_reo = None
        
        # --- 1. MATCH LOTE ---
        lote_final, metodo_lote = extrair_lote_da_string(lote_orig, mapa_lotes_planilha)
        if not lote_final:
            lote_final, metodo_lote = extrair_lote_da_string(amostra, mapa_lotes_planilha)
            if lote_final: metodo_lote = f"Amostra ({metodo_lote})"
            
        status_lote = "FALHA"
        obj_lote = None
        
        if lote_final:
            nome_planilha = mapa_lotes_planilha[lote_final]
            # Aqui passamos o de_para para corrigir nomes errados da planilha
            obj_lote, _, _ = match_nome_inteligente(nome_planilha, catalogo_sankhya, de_para_correcoes)
            if obj_lote:
                status_lote = "SUCESSO"
            else:
                status_lote = "FANTASMA"

        # --- 2. MATCH TEXTO ---
        obj_texto = None
        metodo_texto = "---"
        score_texto = 0
        
        fontes = [(amostra, "Amostra")]
        if usar_cod_reo: fontes.append((cod_reo, "Cód. Reo"))
            
        for txt, fonte in fontes:
            if not txt: continue
            obj, met, sc = match_nome_inteligente(txt, catalogo_sankhya, de_para_correcoes)
            if obj:
                if sc > score_texto:
                    obj_texto = obj
                    metodo_texto = f"{fonte}: {met}"
                    score_texto = sc
                if sc == 1.0: break 

        # --- CONSOLIDAÇÃO FINAL ---
        conclusao = "FALHA"
        final_cod = "---"
        final_desc = "---"
        final_lote = lote_final if lote_final else "---"
        
        # Lógica de Mastigação (Processo Intermediário)
        eh_mastigacao = False
        texto_geral = (str(amostra) + str(lote_orig)).upper()
        if "MASTIGA" in texto_geral or "MAST" in texto_geral:
             eh_mastigacao = True

        if status_lote == "SUCESSO":
            conclusao = "PERFEITO (LOTE)"
            final_cod = obj_lote.cod_sankhya
            final_desc = obj_lote.descricao
        
        elif obj_texto:
            conclusao = "PARCIAL (TEXTO)"
            final_cod = obj_texto.cod_sankhya
            final_desc = obj_texto.descricao
            
        # Se falhou tudo, mas é mastigação, marcamos como processo interno
        elif eh_mastigacao:
            conclusao = "PROCESSO INTERMEDIÁRIO"
            final_desc = "Mastigação / Composto (Não Final)"

        lista_analise.append({
            "ID": id_ens,
            "Grupo": grupo,
            "Tipo Equip": tipo_equip,
            "Data": row['DATA'],
            "Cód. Sankhya": final_cod,
            "Descrição Sankhya": final_desc,
            "Lote Identificado": final_lote,
            "CONCLUSÃO": conclusao,
            # Detalhes
            "Lote Orig": lote_orig,
            "Amostra Orig": amostra
        })

    # --- EXPORTAÇÃO ---
    print(f"--- Gerando Excel: {ARQUIVO_SAIDA} ---")
    df_main = pd.DataFrame(lista_analise)
    
    # Ordem personalizada para o relatório
    ordem_conclusao = {
        "FALHA": 0,
        "PROCESSO INTERMEDIÁRIO": 1,
        "PARCIAL (TEXTO)": 2, 
        "PERFEITO (LOTE)": 3
    }
    df_main['rank'] = df_main['CONCLUSÃO'].map(ordem_conclusao)
    df_main.sort_values(['rank', 'Data'], ascending=[True, False], inplace=True)
    df_main.drop('rank', axis=1, inplace=True)

    with pd.ExcelWriter(ARQUIVO_SAIDA, engine='openpyxl') as writer:
        df_main.to_excel(writer, sheet_name='AUDITORIA FINAL', index=False)
        
        # Aba só de falhas reais (excluindo mastigação)
        df_erros_reais = df_main[df_main['CONCLUSÃO'] == 'FALHA']
        if not df_erros_reais.empty:
            df_erros_reais.to_excel(writer, sheet_name='ERROS CRÍTICOS', index=False)

    print(f"✅ Relatório V8 Gerado!")
    print(f"   Note que 'PROCESSO INTERMEDIÁRIO' (Mastigação) não é mais considerado erro crítico.")

if __name__ == "__main__":
    executar_auditoria()