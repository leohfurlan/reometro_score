# Motor XAI (Treinamento, Simulacao e Explicabilidade)

## 1. Escopo atual

O motor de IA atual foi implementado para:

- Treinar um modelo de regressao para estimar **dureza** (Shore A).
- Simular a dureza prevista para uma receita de composicao (formula + ajustes).
- Expor explicabilidade local usando SHAP (impacto por materia-prima).

No estado atual, somente a propriedade `dureza` e prevista.

## 2. Onde esta implementado

- Servico de IA: `services/simulador_ia_service.py`
- Integracao Flask (APIs): `app.py`
  - import com fallback: `app.py:27`
  - treino: `app.py:1604`
  - simulacao: `app.py:1624`
  - helpers de ingredientes: `app.py:1564`, `app.py:1578`, `app.py:1589`

## 3. Dependencias

Dependencias principais para esta parte:

- `xgboost`
- `shap`
- `pandas`
- `numpy`
- `sqlalchemy` (via `db.engine`)

Se `xgboost` ou `shap` falharem no import, o app sobe com fallback (`SimuladorIAService = None`) e as rotas retornam erro `503`.

## 4. Pipeline de treinamento

### 4.1 Extracao de dados (`extrair_dados_treinamento`)

Fonte de features:

- Tabela `tb_formula_item`
- Campos lidos: `cd_produto`, `cd_materia_prima`, `qt_phr`

Transformacao:

- Pivot por `cd_produto`.
- Cada materia-prima vira coluna no formato `mp_<codigo>`.
- Valores ausentes viram `0`.

Fonte de alvo (target):

- Tabela `ensaio_consolidado`
- Campos lidos: `cod_sankhya as cd_produto`, `dureza`
- Filtro: `dureza IS NOT NULL`

Agregacao do alvo:

- Media de `dureza` por `cd_produto` (quando existem varios ensaios para o mesmo produto).

Join final:

- `inner join` entre formula pivotada e alvo agregado.
- Retorno final contem features + coluna `dureza`.

### 4.2 Treino (`treinar_modelo`)

Regras:

- Se dataset vazio ou com menos de 5 linhas: retorna erro de dados insuficientes.

Modelo:

- `xgb.XGBRegressor(n_estimators=100, learning_rate=0.05, max_depth=5, random_state=42)`
- Treino direto em toda base (`modelo.fit(X, y)`), sem split treino/teste.

Persistencia:

- Modelo salvo em `instance/modelo_dureza_v1.pkl`
- Lista de colunas salva em `instance/modelo_colunas_v1.pkl`
- Sempre sobrescreve os arquivos da versao atual.

Retorno:

- `{"status": "sucesso", "r2": ...}` com R2 calculado no mesmo conjunto de treino.

## 5. Pipeline de simulacao

### 5.1 Entrada de ingredientes

No `app.py`, a simulacao usa dois passos:

1. Base da formula (`_ingredientes_da_formula`) quando `usar_formula_base = true`.
2. Ajustes enviados no JSON (`_parse_ingredientes_payload`) que sobrescrevem/adicionam PHR.

Normalizacao de chave (`_normalizar_chave_mp`):

- Aceita `"105"` e `"mp_105"`.
- Converte para formato canonicamente `mp_105`.
- Ignora chave invalida (nao numerica).

### 5.2 Predicao e XAI (`simular_nova_receita`)

Fluxo:

1. Verifica se existe modelo treinado (`MODEL_PATH`).
2. Carrega modelo + colunas treinadas.
3. Cria vetor de entrada com 1 linha e zeros para todas as colunas do treino.
4. Preenche somente colunas presentes no treino.
5. Prediz dureza.
6. Executa SHAP (`TreeExplainer`) na mesma linha simulada.

Regra de filtragem de impacto:

- Apenas contribuições com `abs(shap_value) > 0.01` entram na saida.

Retorno do servico:

- `dureza_prevista`
- `unidade` (`Shore A`)
- `impacto_ingredientes`
- `base_value` (valor esperado do modelo)

## 6. Contratos de API

## 6.1 Treinar modelo

- Metodo: `POST`
- Rota: `/api/xai/treinar`
- Autenticacao: `login_required`
- Permissao: somente `admin` (`current_user.role == 'admin'`)

Possiveis respostas:

- `200`: treino com sucesso
- `400`: erro de negocio (ex.: dados insuficientes)
- `403`: usuario sem permissao
- `503`: servico indisponivel (dependencias nao carregadas)
- `500`: erro inesperado no treino

Exemplo de retorno:

```json
{
  "status": "sucesso",
  "r2": 0.99
}
```

## 6.2 Simular formula

- Metodo: `POST`
- Rota: `/api/xai/simular/<cd_produto>`
- Autenticacao: `login_required`
- Permissao: somente `admin`

Body JSON aceito:

```json
{
  "usar_formula_base": true,
  "ingredientes": {
    "mp_105": 52.0,
    "204": 3.1
  }
}
```

Sem `ingredientes`, a simulacao usa somente a composicao cadastrada da formula (quando `usar_formula_base` for verdadeiro).

Retorno de sucesso:

```json
{
  "status": "sucesso",
  "cd_produto": 839,
  "ingredientes_utilizados": {
    "mp_105": 52.0
  },
  "propriedades_estimadas": {
    "dureza": {
      "valor": 51.78,
      "unidade": "Shore A"
    }
  },
  "xai": {
    "base_value": 58.24,
    "impacto_ingredientes": {
      "mp_105": -0.42
    }
  }
}
```

Possiveis respostas de erro:

- `400`: sem ingredientes validos, modelo nao treinado, ou erro de negocio do servico
- `403`: usuario sem permissao
- `404`: formula inexistente
- `503`: servico de IA indisponivel
- `500`: erro inesperado na simulacao

## 7. Limitacoes conhecidas (estado atual)

- O alvo predito e apenas **dureza**.
- Nao existe validacao fora da amostra (R2 atual e em treino).
- Nao existe versionamento de modelo no banco; o arquivo local e sobrescrito.
- Ingredientes fora do conjunto de colunas treinadas sao ignorados.
- O SHAP pode listar MPs nao presentes no payload, pois explica o ponto no espaco completo de features do modelo.

## 8. Fluxo operacional recomendado

1. Garantir dependencias instaladas (`xgboost`, `shap`).
2. Logar com usuario `admin`.
3. Executar `POST /api/xai/treinar`.
4. Executar `POST /api/xai/simular/<cd_produto>`.
5. Ler `propriedades_estimadas.dureza` e `xai.impacto_ingredientes`.

