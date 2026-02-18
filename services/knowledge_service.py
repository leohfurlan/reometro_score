from models.usuario import db
from models.knowledge import KnowledgeRule

class KnowledgeService:
    @staticmethod
    def add_rule(target_property, effect_value, effect_type='linear', ingredient_code=None, min_phr=None, max_phr=None, description=None):
        rule = KnowledgeRule(
            target_property=target_property,
            effect_value=effect_value,
            effect_type=effect_type,
            ingredient_code=ingredient_code,
            min_phr=min_phr,
            max_phr=max_phr,
            description=description
        )
        db.session.add(rule)
        db.session.commit()
        return rule

    @staticmethod
    def get_rules(active_only=True):
        query = KnowledgeRule.query
        if active_only:
            query = query.filter_by(active=True)
        return query.all()

    @staticmethod
    def apply_rules(predictions: dict, ingredients: dict) -> dict:
        """
        Applies active rules to the predicted values.
        predictions: {'dureza': {'value': 65.0, ...}, ...}
        ingredients: {'mp_105': 50.0, ...}
        """
        rules = KnowledgeService.get_rules()
        adjusted_predictions = predictions.copy()

        for rule in rules:
            prop = rule.target_property
            if prop not in adjusted_predictions or adjusted_predictions[prop].get('value') is None:
                continue

            # Check ingredient condition
            if rule.ingredient_code:
                # normalize code check (assuming ingredients keys are like 'mp_105')
                code_key = rule.ingredient_code if rule.ingredient_code.startswith('mp_') else f"mp_{rule.ingredient_code}"
                phr = ingredients.get(code_key, 0.0)
                
                if phr <= 0:
                    continue
                    
                if rule.min_phr and phr < rule.min_phr:
                    continue
                if rule.max_phr and phr > rule.max_phr:
                    continue
            
            # Apply effect
            original_val = adjusted_predictions[prop]['value']
            if rule.effect_type == 'linear':
                new_val = original_val + rule.effect_value
            elif rule.effect_type == 'percentage':
                new_val = original_val * rule.effect_value
            else:
                new_val = original_val

            adjusted_predictions[prop]['value'] = round(new_val, 2)
            
            # Add trace
            if 'rules_applied' not in adjusted_predictions[prop]:
                adjusted_predictions[prop]['rules_applied'] = []
            
            adjusted_predictions[prop]['rules_applied'].append({
                'id': rule.id,
                'desc': rule.description,
                'effect': f"{rule.effect_type} {rule.effect_value}"
            })

        return adjusted_predictions
