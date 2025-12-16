from datetime import datetime, timedelta
from threading import Lock
import sys

class CacheManager:
    """
    Gerenciador de cache com controle de memória e TTL.
    Previne crescimento descontrolado e dados obsoletos.
    """
    
    def __init__(self, ttl_minutes=120, max_size_mb=500):
        self.cache = {
            'dados': [],
            'materiais': [],
            'ultimo_update': None,
            'total_registros_brutos': 0
        }
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_size_mb = max_size_mb
        self.lock = Lock()
    
    def get(self):
        """Retorna cache se válido, None se expirado."""
        with self.lock:
            if not self.cache['ultimo_update']:
                return None
            
            idade = datetime.now() - self.cache['ultimo_update']
            if idade > self.ttl:
                print(f"⏰ Cache expirado ({idade.seconds//60}min > {self.ttl.seconds//60}min)")
                return None
            
            return self.cache.copy()
    
    def set(self, dados):
        """Atualiza cache com verificação de tamanho."""
        with self.lock:
            # Verifica tamanho aproximado
            size_mb = sys.getsizeof(dados) / (1024 * 1024)
            
            if size_mb > self.max_size_mb:
                print(f"⚠️ Cache grande demais ({size_mb:.1f}MB). Limpando registros antigos...")
                # Mantém apenas últimos 30 dias
                cutoff = datetime.now() - timedelta(days=30)
                dados['dados'] = [
                    e for e in dados['dados'] 
                    if e.data_hora and e.data_hora >= cutoff
                ]
                size_mb = sys.getsizeof(dados) / (1024 * 1024)
                print(f"   Reduzido para {size_mb:.1f}MB")
            
            self.cache = dados
            print(f"💾 Cache atualizado: {len(dados['dados'])} registros ({size_mb:.1f}MB)")
    
    def invalidate(self):
        """Força recarga no próximo acesso."""
        with self.lock:
            self.cache['ultimo_update'] = None
            print("🗑️ Cache invalidado")
    
    def get_stats(self):
        """Retorna estatísticas do cache."""
        with self.lock:
            if not self.cache['ultimo_update']:
                return {'status': 'vazio'}
            
            idade = datetime.now() - self.cache['ultimo_update']
            size_mb = sys.getsizeof(self.cache) / (1024 * 1024)
            
            return {
                'status': 'ativo',
                'registros': len(self.cache['dados']),
                'idade_minutos': idade.seconds // 60,
                'tamanho_mb': round(size_mb, 2),
                'ultimo_update': self.cache['ultimo_update']
            }