from flask_login import LoginManager

from cache_manager import CacheManager
from models.usuario import Usuario

try:
    from services.formulation_engine_service import FormulationEngineService
except Exception as exc:
    print(f"Aviso: Motor de formulacao indisponivel: {exc}")
    FormulationEngineService = None


login_manager = LoginManager()
cache_service = CacheManager(ttl_minutes=30, max_size_mb=500)
formulation_engine_service = FormulationEngineService() if FormulationEngineService else None


@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))
