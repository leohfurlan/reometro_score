from datetime import datetime
from models.usuario import db

class EnsaioConsolidado(db.Model):
    __tablename__ = 'ensaio_consolidado'

    # Chaves e Metadados
    id_ensaio = db.Column(db.Integer, primary_key=True)
    data_hora = db.Column(db.DateTime, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # Identificação do Material
    lote = db.Column(db.String(50), index=True)
    batch = db.Column(db.String(10))
    cod_sankhya = db.Column(db.Integer, index=True)
    massa_descricao = db.Column(db.String(200))
    
    # Parâmetros Físicos
    temp_plato = db.Column(db.Float)
    ts2 = db.Column(db.Float)
    t90 = db.Column(db.Float)
    viscosidade = db.Column(db.Float)
    origem_viscosidade = db.Column(db.String(50)) # 'Real', 'Média', 'N/A'

    # Resultados do Score
    score_final = db.Column(db.Float)
    acao_recomendada = db.Column(db.String(100))
    
    # Auditoria do Processo
    metodo_identificacao = db.Column(db.String(50)) # MANUAL, LOTE, TEXTO, FANTASMA
    lote_original = db.Column(db.String(100))
    material_original = db.Column(db.String(200))
    
    # --- Propriedades de Compatibilidade (Facade) ---
    
    @property
    def massa(self):
        class MassaFacade:
            def __init__(self, cod, desc):
                self.cod_sankhya = cod
                self.descricao = desc
                self.parametros = {} # Mock para evitar erro no template
        return MassaFacade(self.cod_sankhya, self.massa_descricao)

    @property
    def valores_medidos(self):
        """
        Retorna um dicionário apenas com os valores que não são nulos.
        Isso permite que o template use .get('Key', 0) corretamente.
        """
        dados = {}
        if self.ts2 is not None: dados['Ts2'] = self.ts2
        if self.t90 is not None: dados['T90'] = self.t90
        if self.viscosidade is not None: dados['Viscosidade'] = self.viscosidade
        return dados
    
    # Propriedades auxiliares para evitar erros de atributo no template
    @property
    def ts2_fora(self): return False 
    @property
    def t90_fora(self): return False
    @property
    def viscosidade_fora(self): return False
    @property
    def parametros_usados(self): return {}
    @property
    def nome_perfil_usado(self): return "Consolidado"
    @property
    def temp_padrao_usado(self): return None
    @property
    def temp_plato_display(self): return f"{self.temp_plato:.0f}" if self.temp_plato else ""