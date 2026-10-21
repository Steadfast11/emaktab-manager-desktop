"""In-process local backend: no credentials, no outbound requests."""
import time
import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import Literal
from resilience import CircuitBreaker, CooldownScheduler, RateLimitDetector, Outcome


class TestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario: Literal['SUCCESS', 'BAD_CREDENTIALS', 'RATE_LIMIT', 'RATE_LIMIT_EXPIRED']
    identity: Literal['lab-device-01', 'lab-device-02'] = 'lab-device-01'


class LocalSimulationServer:
    def __init__(self, breaker):
        self.breaker = breaker
        self.blocked_until = 0
        self.events = []

    def login(self, case):
        stamp = time.monotonic()
        if case.scenario == 'RATE_LIMIT_EXPIRED':
            self.blocked_until = 0
        elif case.scenario == 'RATE_LIMIT':
            self.blocked_until = max(self.blocked_until, stamp+30)
        if stamp < self.blocked_until:
            code, status = 'RATE_LIMIT', 429
            headers = {'Retry-After': str(max(1, int(self.blocked_until-stamp+.999)))}
            body = {'code': code}
        elif case.scenario == 'BAD_CREDENTIALS':
            code, status, headers, body = 'BAD_CREDENTIALS', 401, {}, {'code': 'BAD_CREDENTIALS'}
        else:
            code, status, headers, body = 'SUCCESS', 200, {}, {'code': 'SUCCESS', 'authenticated': True}
        if case.scenario == 'RATE_LIMIT_EXPIRED' and status == 200:
            body['event'] = 'RATE_LIMIT_EXPIRED'
        self.events.append({'identity':case.identity, 'code':code, 'time':time.time()})
        self.events = self.events[-100:]
        return status, headers, body

    def step(self, case):
        self.breaker.assert_allowed()
        status, headers, body = self.login(case)
        detection = RateLimitDetector.classify_http(status, headers, body)
        if detection.outcome == Outcome.RATE_LIMIT:
            self.breaker.pause_rate(detection)
        elif detection.outcome == Outcome.SUCCESS:
            self.breaker.success()
        elif detection.outcome == Outcome.BAD_CREDENTIALS:
            self.breaker.rejected()
        return {'outcome':detection.outcome, 'http_status':status, 'breaker':self.breaker.status()}


def make_app(server):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.headers.get('host') not in ('127.0.0.1:8877','localhost:8877',f"127.0.0.1:{os.environ.get('EMAKTAB_PORT', '8765')}",f"localhost:{os.environ.get('EMAKTAB_PORT', '8765')}",'testserver'):
            return JSONResponse({'detail':'Loopback only'},status_code=403)
        return await call_next(request)
    @app.exception_handler(RequestValidationError)
    async def private_error(request, exc):
        return JSONResponse({'detail':'Synthetic scenarios only; credentials are not accepted'},status_code=422)
    @app.post('/login')
    async def login(case: TestCase):
        status, headers, body = server.login(case)
        return JSONResponse(body, status_code=status, headers=headers)
    @app.post('/step')
    async def step(case: TestCase):
        try:
            return server.step(case)
        except ValueError as e:
            return JSONResponse({'detail':str(e)},status_code=409)
    @app.post('/resume')
    async def resume():
        try:
            server.breaker.resume()
            return server.breaker.status()
        except ValueError as e:
            return JSONResponse({'detail':str(e)},status_code=409)
    @app.post('/reset')
    async def reset():
        # Only this simulator's isolated database is reset, never the real Manager breaker.
        server.blocked_until = 0
        server.events = []
        with server.breaker.db() as c:
            c.execute("UPDATE breaker SET state='IDLE',blocked_at=0,blocked_until=0,attempt=0,evidence='' WHERE id=1")
            c.execute('DELETE FROM recovery_attempts')
        return server.breaker.status()
    @app.get('/state')
    async def state():
        return {'breaker':server.breaker.status(),'events':server.events,'backend_wait':max(0,int(server.blocked_until-time.monotonic()+.999))}
    return app
