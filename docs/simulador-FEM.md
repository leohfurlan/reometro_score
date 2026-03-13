A seguir está um **PRD técnico enxuto** para você usar diretamente com **Codex / Cursor / Copilot**, estruturado para evoluir seu repositório **sem quebrar o sistema atual** e alinhado ao modelo físico do artigo.

---

# PRD Técnico — Motor Termo-Cinético de Vulcanização

## 1. Objetivo

Evoluir o simulador atual de vulcanização para um **modelo termo-cinético mecanicista acoplado**, capaz de:

* simular **transferência de calor + cinética de cura**
* incluir **calor exotérmico da reação**
* modelar **cura + reversão**
* simular **desmoldagem e resfriamento convectivo**
* operar em **malhas 2D axisimétricas**

sem quebrar o fluxo atual do sistema.

O sistema atual deve continuar funcionando enquanto o novo motor é desenvolvido.

---

# 2. Arquitetura alvo

Estrutura recomendada:

```
services/
│
├── kinetics_solver.py           # modelo empírico atual (não alterar)
├── vulcanization_service.py     # solver FDM atual (não alterar)
│
├── mechanistic_kinetics.py      # NOVO - cinética cura + reversão
├── thermo_solver.py             # NOVO - equação de calor com fonte exotérmica
├── process_state_machine.py     # NOVO - controle heating → demold → cooling
│
├── vulcanization_service_v2.py  # NOVO - solver acoplado FDM
│
└── fem_vulcanization_service.py # FUTURO - solver FEM ou FiPy
```

---

# 3. Modelo físico alvo

## 3.1 Equação de calor

[
\rho C_p \frac{\partial T}{\partial t}
======================================

\lambda \nabla^2 T
+
Q
]

onde

[
Q = \rho q_v \frac{d\alpha_c}{dt}
]

Constantes:

```
rho = 1020 kg/m³
Cp = 1.82 kJ/kgK
q_v = 13 kJ/kg
lambda = 0.16 W/mK
```

---

## 3.2 Cinética de cura

[
\frac{d\alpha_c}{dt}
====================

k_c(T) f(\alpha_c)
]

[
k_c(T)=A_c e^{-E_c/(RT)}
]

---

## 3.3 Cinética de reversão

[
\frac{d\alpha_r}{dt}
====================

k_r(T) f(\alpha_r)
]

[
k_r(T)=A_r e^{-E_r/(RT)}
]

---

## 3.4 Conversão final

[
\alpha = \alpha_c - \alpha_r
]

---

## 4. Máquina de estados do processo

Estados:

```
HEATING
DEMOLD
COOLING
FINISHED
```

Transição:

```
if mean(alpha) >= 0.9:
    state = DEMOLD
```

Durante DEMOLD:

```
tempo_desmoldagem = t
state → COOLING
```

---

# 5. Milestones de desenvolvimento

---

# Milestone 1 — Calor exotérmico

## Objetivo

Acoplar geração de calor ao solver atual.

---

## Arquivo

```
services/thermo_solver.py
```

---

## Função a criar

```python
def reaction_heat_source(alpha_rate, rho, qv):
    """
    Calcula geração volumétrica de calor
    """
    return rho * qv * alpha_rate
```

---

## Alteração em `vulcanization_service_v2.py`

Atualizar temperatura:

```
T_new =
T_old
+
dt * (lambda/(rho*Cp)) * laplacian
+
dt * Q/(rho*Cp)
```

---

## Critério de aceite

* temperatura aumenta no centro da peça durante cura
* comportamento compatível com literatura

---

# Milestone 2 — Nova cinética mecanística

Criar:

```
services/mechanistic_kinetics.py
```

---

## Classe principal

```python
class MechanisticKinetics:

    def rate_cure(self, T, alpha_c):
        ...

    def rate_reversion(self, T, alpha_r):
        ...

    def step(self, T, alpha_c, alpha_r, dt):
        return new_alpha_c, new_alpha_r
```

---

## Solver deve integrar:

```
alpha_c += rate_cure * dt
alpha_r += rate_reversion * dt
```

---

## Critério de aceite

* alpha_c cresce
* alpha_r aparece apenas em altas temperaturas
* alpha final estabiliza

---

# Milestone 3 — Desmoldagem

Criar:

```
services/process_state_machine.py
```

---

## Classe

```python
class VulcanizationProcess:

    state = "HEATING"

    def update(self, alpha_mean):
        if alpha_mean >= 0.9:
            self.state = "COOLING"
```

---

## Mudanças térmicas

### Durante aquecimento

```
Dirichlet
T = T_molde
```

### Durante cooling

Convecção:

[
-q = h (T - T_{amb})
]

Parâmetros

```
h_mold = 10000 W/m²K
h_air = 11.4 W/m²K
T_air = 25°C
```

---

## Critério de aceite

* sistema troca para cooling automaticamente
* temperatura começa a cair
* cura continua

---

# Milestone 4 — Solver FDM acoplado

Criar

```
services/vulcanization_service_v2.py
```

---

## Campos simulados

```
T_field
alpha_c_field
alpha_r_field
alpha_field
Q_field
```

---

## Loop principal

```
1 calcular taxa cinética
2 calcular calor da reação
3 resolver equação de calor
4 atualizar campos de cura
5 verificar demoldagem
```

---

## Critério de aceite

* solver estável
* sem NaN
* cura física plausível

---

# Milestone 5 — Geometria 2D axisimétrica

Criar solver alternativo

```
fem_vulcanization_service.py
```

Biblioteca recomendada:

```
FiPy
```

ou

```
FEniCSx
```

---

## Domínio

```
(r, z)
```

malha:

```
CylindricalGrid2D
```

variáveis:

```
T
alpha_c
alpha_r
```

---

# Milestone 6 — Validação

Criar teste:

```
tests/test_mechanistic_solver.py
```

---

## Casos de teste

Simular esferas

```
1 cm
5 cm
10 cm
```

---

## Métrica

[
\Delta \alpha
=============

\max(\alpha)-\min(\alpha)
]

critério:

```
Δα ≤ 0.015
```

---

# 6. Requisitos numéricos

Para estabilidade:

```
dt < dx² / (2 * alpha_diff)
```

Se violado:

```
usar substeps
```

---

# 7. Requisitos de persistência

Salvar campos adicionais:

```
alpha_c
alpha_r
heat_generation
process_state
demold_time
```

---

# 8. Visualização

Atualizar view

```
/reometria/simulate/view
```

adicionar layers:

```
temperatura
cura
reversão
geração de calor
```

---

# 9. Métricas finais

O sistema deve permitir extrair:

```
tempo de desmoldagem
homogeneidade de cura
temperatura máxima
curva de temperatura no centro
```

---

# 10. Resultado esperado

Ao final do projeto o simulador deve:

* prever **tempo ótimo de vulcanização**
* prever **cura residual pós-prensa**
* prever **gradientes térmicos**
* prever **reversão**
* prever **homogeneidade final**

Isso aproxima o sistema de **simuladores industriais de vulcanização**.

---

# Próximo passo (recomendado)

Se quiser, no próximo passo eu também posso gerar para você:

**um conjunto de prompts ideais para o Codex executar cada milestone automaticamente**, que acelera muito o desenvolvimento.
