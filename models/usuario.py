from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

# Instância do SQLAlchemy (será inicializada com o app no app.py)
db = SQLAlchemy()

class Usuario(UserMixin, db.Model):
    """
    Modelo de Usuário para o banco de dados SQLite local.
    Gerencia autenticação e níveis de acesso (role).
    """
    __tablename__ = 'usuarios'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # Níveis de acesso: 'admin' ou 'operador'
    role = db.Column(db.String(50), default='operador', nullable=False)

    def set_password(self, password, bcrypt):
        """
        Recebe a senha em texto puro, gera o hash seguro e salva no objeto.
        Requer o objeto 'bcrypt' configurado no app.
        """
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password, bcrypt):
        """
        Verifica se a senha fornecida corresponde ao hash salvo no banco.
        Retorna True se correta, False caso contrário.
        """
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        """
        Propriedade auxiliar para verificar rapidamente se o usuário é administrador.
        Uso: if current_user.is_admin: ...
        """
        return self.role == 'admin'

    def __repr__(self):
        return f"<Usuario {self.username} - {self.role}>"