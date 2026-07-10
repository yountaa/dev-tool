"""Точка входа dev-tool. Модули подключаются одной строкой app.include_router()."""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import logging_setup
from access import router as access_router
from modules.silences import save_hub
from modules.silences.client import AlertmanagerError
from modules.silences.routes import router as silences_router
from modules.silences.schedule import scheduler
from modules.victoria.client import VictoriaError
from modules.victoria.routes import router as victoria_router

logging_setup.setup()  # NDJSON в stdout
log = logging.getLogger("silences.app")
access_log = logging.getLogger("silences.access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """На старте: подготовить локальную папку правил и запустить шедулер."""
    save_hub.ensure_repo()
    scheduler.start()
    log.info("приложение запущено")
    yield


app = FastAPI(title="dev-tool", lifespan=lifespan)

# Фронт на другом origin — пускаем. На проде список лучше сузить.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Структурный лог каждого запроса (метод, путь, статус, длительность)."""
    start = time.perf_counter()
    response = await call_next(request)
    logging_setup.event(
        access_log, "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round((time.perf_counter() - start) * 1000, 1),
    )
    return response


@app.exception_handler(AlertmanagerError)
async def am_error(request: Request, exc: AlertmanagerError):
    # Alertmanager недоступен/ответил ошибкой — отдаём понятный 502, а не стектрейс.
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(VictoriaError)
async def vm_error(request: Request, exc: VictoriaError):
    # Компонент VM недоступен/не настроен — понятный 502, а не стектрейс.
    return JSONResponse(status_code=502, content={"detail": str(exc)})


# --- Подключение модулей (1 модуль = 1 router = 1 вкладка) ---------------------
app.include_router(access_router)     # RBAC уровня приложения: /access/me
app.include_router(silences_router)
app.include_router(victoria_router)


@app.get("/health")
def health():
    """Пинг для проверок живости."""
    return {"status": "ok"}
