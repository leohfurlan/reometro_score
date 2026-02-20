from datetime import datetime

from models.usuario import db


class Formulation(db.Model):
    __tablename__ = "formulation"

    id = db.Column(db.Integer, primary_key=True)
    external_code = db.Column(db.String(64), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=True)
    version = db.Column(db.String(64), nullable=False, default="v1")
    source = db.Column(db.String(64), nullable=False, default="legacy")
    batch_reference = db.Column(db.String(128), nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    ingredients = db.relationship(
        "FormulationIngredient",
        backref="formulation",
        lazy=True,
        cascade="all, delete-orphan",
    )
    process_parameters = db.relationship(
        "ProcessParameters",
        backref="formulation",
        uselist=False,
        lazy=True,
        cascade="all, delete-orphan",
    )
    measured_properties = db.relationship(
        "MeasuredProperties",
        backref="formulation",
        uselist=False,
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Formulation id={self.id} external_code={self.external_code}>"


class FormulationIngredient(db.Model):
    __tablename__ = "formulation_ingredient"

    id = db.Column(db.Integer, primary_key=True)
    formulation_id = db.Column(
        db.Integer, db.ForeignKey("formulation.id"), nullable=False, index=True
    )
    material_code = db.Column(db.String(64), nullable=False, index=True)
    material_name = db.Column(db.String(255), nullable=True)
    phr = db.Column(db.Float, nullable=False)
    ingredient_type = db.Column(db.String(32), nullable=True, index=True)
    unit_cost_per_kg = db.Column(db.Float, nullable=True)
    reinforcement_index = db.Column(db.Float, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "formulation_id",
            "material_code",
            name="uq_formulation_ingredient_code",
        ),
    )

    def __repr__(self):
        return (
            f"<FormulationIngredient formulation_id={self.formulation_id} "
            f"material_code={self.material_code} phr={self.phr}>"
        )


class ProcessParameters(db.Model):
    __tablename__ = "process_parameters"

    id = db.Column(db.Integer, primary_key=True)
    formulation_id = db.Column(
        db.Integer,
        db.ForeignKey("formulation.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    mixing_temp_c = db.Column(db.Float, nullable=True)
    mixing_time_min = db.Column(db.Float, nullable=True)
    curing_temp_c = db.Column(db.Float, nullable=True)
    curing_time_min = db.Column(db.Float, nullable=True)
    rotor_speed_rpm = db.Column(db.Float, nullable=True)
    pressure_bar = db.Column(db.Float, nullable=True)
    dump_temp_c = db.Column(db.Float, nullable=True)
    preheat_temp_c = db.Column(db.Float, nullable=True)
    ambient_humidity_pct = db.Column(db.Float, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<ProcessParameters formulation_id={self.formulation_id}>"


class MeasuredProperties(db.Model):
    __tablename__ = "measured_properties"

    id = db.Column(db.Integer, primary_key=True)
    formulation_id = db.Column(
        db.Integer,
        db.ForeignKey("formulation.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    hardness = db.Column(db.Float, nullable=True)
    tensile = db.Column(db.Float, nullable=True)
    elongation = db.Column(db.Float, nullable=True)
    abrasion = db.Column(db.Float, nullable=True)
    ts2 = db.Column(db.Float, nullable=True)
    t90 = db.Column(db.Float, nullable=True)
    risk_score = db.Column(db.Float, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    measured_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<MeasuredProperties formulation_id={self.formulation_id}>"
