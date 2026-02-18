from models.usuario import db
from datetime import datetime

class KnowledgeRule(db.Model):
    __tablename__ = 'tb_knowledge_rule'

    id = db.Column(db.Integer, primary_key=True)
    
    # Target property (e.g., 'dureza', 'densidade')
    target_property = db.Column(db.String(50), nullable=False)
    
    # Condition: Ingredient ID (optional, if rule is specific to an ingredient)
    ingredient_code = db.Column(db.String(50), nullable=True)
    
    # Condition: Min/Max PHR (optional)
    min_phr = db.Column(db.Float, nullable=True)
    max_phr = db.Column(db.Float, nullable=True)
    
    # Effect: 'linear' (+5) or 'percentage' (*1.10)
    effect_type = db.Column(db.String(20), default='linear') 
    effect_value = db.Column(db.Float, nullable=False)
    
    # Metadata
    confidence = db.Column(db.Float, default=1.0) # 0.0 to 1.0
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<Rule {self.target_property} ({self.effect_type}: {self.effect_value})>"
