"""Standalone simulator: python -m uvicorn lab_server:app --host 127.0.0.1 --port 8877 --no-access-log"""
import os
from pathlib import Path
from resilience import CircuitBreaker, CooldownScheduler
from simulation import LocalSimulationServer, make_app
root = Path(os.environ.get('EMAKTAB_DATA_DIR', Path(__file__).parent / 'data'))
root.mkdir(parents=True, exist_ok=True, mode=0o700)
app = make_app(LocalSimulationServer(CircuitBreaker(root/'standalone-lab.sqlite3', cooldown=CooldownScheduler(base=3, maximum=60, jitter=0))))
os.chmod(root/'standalone-lab.sqlite3',0o600)
