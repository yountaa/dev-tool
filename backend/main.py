"""Точка входа dev-tool. Модули подключаются одной строкой app.include_router()."""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

import logging_setup
from access import log_config as log_auth_config, request_log_fields, router as access_router
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
    """На старте: показать настройки авторизации, подготовить папку правил, шедулер."""
    log_auth_config()  # действующие AUTH_/RBAC_ настройки — первым делом в лог
    save_hub.ensure_repo()
    scheduler.start()
    log.info("приложение запущено")
    yield


app = FastAPI(title="dev-tool", lifespan=lifespan)

# Сжимаем большие JSON-ответы (targets, имена метрик, правила — мегабайты текста):
# по сети уходит в ~10 раз меньше, вкладки открываются заметно быстрее.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Фронт на другом origin — пускаем. На проде список лучше сузить.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _service_of(path: str) -> str:
    """Какой внутренний модуль (сервис) обслуживает путь — для поля service в логах.
    Берём первый сегмент пути: /victoria/... → victoria, /access/me → access и т.д.
    Так по строке лога сразу видно, чей роут отработал."""
    seg = path.lstrip("/").split("/", 1)[0]
    return seg or "root"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Логируем НЕ каждый запрос, а только проблемные (4xx/5xx).

    Штатные 2xx/3xx не пишем — их поток большой (вкладки часто дёргают прокси), и в
    логе от них один шум. Полная картина остаётся и без них: настройки сервиса на
    старте (auth.config), решения RBAC/авторизации (auth.*) и вот эти ошибки. В
    каждой строке — service=<модуль>, чтобы сразу знать, чей роут ответил ошибкой,
    плюс кто пришёл (user/auth_hdr/groups_n) для разбора.
    (Атрибуция «кто что сделал» живёт в git-коммитах модулей, а не в этих логах —
    поэтому отказ от строки на каждый запрос её не теряет.)
    """
    start = time.perf_counter()
    response = await call_next(request)
    if response.status_code >= 400:
        logging_setup.event(
            access_log, "http.error",
            level=logging.WARNING if response.status_code < 500 else logging.ERROR,
            service=_service_of(request.url.path),
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            **request_log_fields(request),
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
