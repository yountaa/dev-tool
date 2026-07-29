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
from share import router as share_router
from modules.alerts import storage as alerts_storage
from modules.alerts.routes import router as alerts_router
from modules.silences import config as silences_config
from modules.silences import save_hub
from modules.silences.client import AlertmanagerError
from modules.silences.routes import router as silences_router
from modules.silences.schedule import scheduler
from modules.victoria import config as victoria_config
from modules.victoria.client import VictoriaError
from modules.victoria.routes import router as victoria_router

logging_setup.setup()  # NDJSON в stdout
# module в логе = первый сегмент имени логгера. У этих двух он общий, не модульный:
# «app» — жизненный цикл приложения, «http» — ошибки запросов (там модуль
# проставляется отдельно, по пути запроса).
log = logging.getLogger("app")
access_log = logging.getLogger("http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """На старте: показать настройки авторизации, подготовить папку правил, шедулер."""
    # Первым делом — действующая конфигурация: по этим строкам видно, что модуль
    # прочитал из окружения (и появятся ли вообще его вкладки). Пишем их здесь,
    # а не при импорте модулей: до setup() форматтер ещё не установлен.
    log_auth_config()             # AUTH_/RBAC_
    silences_config.log_config()  # окружения Alertmanager, хранилище
    victoria_config.log_config()  # кластеры VM и их под-вкладки
    save_hub.ensure_repo()
    alerts_storage.ensure_schema()  # alerts_history / alerts_meta (не ломает silences)
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

def _module_of(path: str) -> str:
    """Какой модуль обслуживает путь — для поля module в логах.
    Берём первый сегмент пути: /victoria/... → victoria, /access/me → access.
    Так ошибка запроса попадает в тот же фильтр по module, что и логи самого модуля."""
    seg = path.lstrip("/").split("/", 1)[0]
    return seg or "root"


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Логируем НЕ каждый запрос, а только проблемные (4xx/5xx).

    Штатные 2xx/3xx не пишем — их поток большой (вкладки часто дёргают прокси), и в
    логе от них один шум. Полная картина остаётся и без них: настройки сервиса на
    старте (auth.config), решения RBAC/авторизации (auth.*), собственные события
    модулей и вот эти ошибки. В каждой строке — module=<модуль>, чтобы ошибка
    запроса попадала в тот же фильтр, что и логи самого модуля, плюс кто пришёл
    (user/auth_hdr/groups_n) для разбора.
    (Атрибуция «кто что сделал» живёт в журнале истории модулей, а не в этих логах —
    поэтому отказ от строки на каждый запрос её не теряет.)
    """
    start = time.perf_counter()
    response = await call_next(request)
    if response.status_code >= 400:
        logging_setup.event(
            access_log, "http.error",
            level=logging.WARNING if response.status_code < 500 else logging.ERROR,
            module=_module_of(request.url.path),
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
app.include_router(share_router)      # короткие ссылки на вид: POST /share, GET /s/<id>
app.include_router(silences_router)
app.include_router(victoria_router)
app.include_router(alerts_router)


@app.get("/health")
def health():
    """Пинг для проверок живости."""
    return {"status": "ok"}
