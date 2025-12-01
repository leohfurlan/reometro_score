from models.ensaio import Ensaio
from models.massa import Massa

# --- ÁREA DE TESTES (SIMULAÇÃO) ---

# 1. Criar as Massas (Materiais) e seus Parâmetros
# Exemplo 1: O material do teu Excel
massa_std = Massa(cod_sankhya=26791, descricao="MASSA CAMELBACK AGR STD")
massa_std.adicionar_parametro("Ts2", peso=8, alvo=60, minimo=40, maximo=80)
massa_std.adicionar_parametro("T90", peso=6, alvo=100, minimo=80, maximo=120)
massa_std.adicionar_parametro("Viscosidade", peso=10, alvo=63, minimo=56, maximo=70)

# Exemplo 2: Um novo material fictício (Borracha Premium)
massa_premium = Massa(cod_sankhya=2020, descricao="BORRACHA_PREMIUM_X")
massa_premium.adicionar_parametro("Ts2", peso=8, alvo=45, minimo=40, maximo=50) # Mais rigoroso
massa_premium.adicionar_parametro("T90", peso=6, alvo=90, minimo=85, maximo=95)

# 2. Simular Ensaios (Dados que viriam do Banco de Dados)
# Ensaio A: Dados perfeitos para o STD
dados_ensaio_1 = {"Ts2": 60, "T90": 100, "Viscosidade": 63}
ensaio_1 = Ensaio(id_ensaio=437441, massa_objeto=massa_std, valores_medidos=dados_ensaio_1)

# Ensaio B: Dados desviados para o STD
dados_ensaio_2 = {"Ts2": 70, "T90": 110, "Viscosidade": 58} 
# Nota: Ts2=70 está na metade do caminho entre alvo(60) e max(80), deve perder 15 pontos (Score 85)
ensaio_2 = Ensaio(id_ensaio=437442, massa_objeto=massa_std, valores_medidos=dados_ensaio_2)

# 3. Executar Cálculos
print(f"Score Ensaio 1: {ensaio_1.calcular_score():.2f}")
print("-" * 30)

ensaio_2.calcular_score()
for detalhe in ensaio_2.detalhes_score:
    print(detalhe)
print(f"Score Final Ensaio 2: {ensaio_2.score_final:.2f}")