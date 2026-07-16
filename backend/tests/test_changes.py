"""Тесты на изменения из ревью: параллельность и обработка ошибок на вкладке
«Алерты», общий httpx-клиент silences, тип/логика resolve_tenant, разбивка
«Диска» по группам vmstorage, короткие ссылки (/share, /s/<id>).

Внешние сервисы (Alertmanager/Prometheus/VM) не нужны — их вызовы подменяем.
Запуск:  cd backend && python -m unittest discover tests
"""
import asyncio
import json
import os
import tempfile
import unittest

# Окружение задаём ДО импорта модулей приложения — их config читает переменные при
# импорте. load_dotenv(override=False) наши значения не перетрёт.
os.environ.setdefault("alert_test", "http://am.test:9093")   # окружение silences «test»
os.environ.setdefault("vm_solo", "http://vmselect.solo")      # простой кластер (без тенантов)
os.environ.setdefault("vm_multi__a", "http://vmselect.a")     # мультитенантный: тенанты a, b
os.environ.setdefault("vm_multi__b", "http://vmselect.b")
os.environ.setdefault("STORAGE_BACKEND", "local")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.silences import client as am_client
from modules.silences.client import AlertmanagerError
from modules.silences.routes import router as silences_router
from modules.victoria import config as vconfig
from modules.victoria import disk as vdisk
from modules.victoria.client import VictoriaError
import share
from share import router as share_router


def _app() -> TestClient:
    """Минимальное приложение только с роутером silences (без scheduler/storage/lifespan)."""
    app = FastAPI()
    app.include_router(silences_router)
    return TestClient(app)


class ListAlertsErrorHandling(unittest.TestCase):
    """GET /silences/{env}/alerts — что происходит при падении каждого источника."""

    def setUp(self):
        self.client = _app()
        # Оригиналы, чтобы восстанавливать после подмены.
        self._orig_alerts = am_client.list_alerts
        self._orig_rules = am_client.list_rule_defs

    def tearDown(self):
        am_client.list_alerts = self._orig_alerts
        am_client.list_rule_defs = self._orig_rules

    def _patch(self, alerts_impl, rules_impl):
        am_client.list_alerts = alerts_impl
        am_client.list_rule_defs = rules_impl

    def test_both_ok_returns_rule_items(self):
        """Оба источника живы: отдаём определения правил + пометки AM."""
        async def alerts(env):
            return [{"labels": {"alertname": "HighCPU"}, "status": {"state": "active", "silencedBy": ["x"]}}]
        async def rules(env):
            return [{"name": "HighCPU", "query": "cpu > 90", "state": "firing", "alerts": []}]
        self._patch(alerts, rules)

        r = self.client.get("/silences/test/alerts")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "HighCPU")
        self.assertTrue(body[0]["silenced"])        # пометка AM доехала
        self.assertEqual(body[0]["am_state"], "active")

    def test_am_down_still_200_without_marks(self):
        """AM недоступен — best-effort: правила показываем, пометок AM просто нет."""
        async def alerts(env):
            raise AlertmanagerError("AM недоступен")
        async def rules(env):
            return [{"name": "HighCPU", "query": "up", "state": "firing", "alerts": []}]
        self._patch(alerts, rules)

        r = self.client.get("/silences/test/alerts")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertFalse(body[0]["silenced"])       # пометок нет, но вкладка не пустая
        self.assertEqual(body[0]["am_state"], "")

    def test_rules_source_down_returns_502(self):
        """Источник правил недоступен — понятный 502 (а не пустая вкладка/500)."""
        async def alerts(env):
            return []
        async def rules(env):
            raise AlertmanagerError("vmalert недоступен")
        self._patch(alerts, rules)

        r = self.client.get("/silences/test/alerts")
        self.assertEqual(r.status_code, 502)
        self.assertIn("vmalert", r.json()["detail"])

    def test_no_rule_source_falls_back_to_am_alerts(self):
        """Источник правил не задан (пусто) — показываем живые алерты самого AM."""
        async def alerts(env):
            return [{"labels": {"alertname": "DiskFull"}, "status": {"state": "active"},
                     "startsAt": "2026-01-01T00:00:00Z", "annotations": {}}]
        async def rules(env):
            return []   # нет источника правил
        self._patch(alerts, rules)

        r = self.client.get("/silences/test/alerts")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "DiskFull")
        self.assertEqual(body[0]["state"], "firing")

    def test_unexpected_error_is_not_swallowed(self):
        """Не-AlertmanagerError из источника правил не должен молча превратиться в []."""
        async def alerts(env):
            return []
        async def rules(env):
            raise ValueError("баг в разборе")
        self._patch(alerts, rules)

        with self.assertRaises(ValueError):
            self.client.get("/silences/test/alerts")

    def test_unknown_env_404(self):
        """Неизвестное окружение — 404 (require_env)."""
        r = self.client.get("/silences/nosuchenv/alerts")
        self.assertEqual(r.status_code, 404)

    def test_sources_run_in_parallel(self):
        """Ключевая проверка оптимизации: оба источника опрашиваются ОДНОВРЕМЕННО.

        Если бы вызовы шли последовательно, второй стартовал бы только после
        завершения первого. Пишем события старт/финиш и убеждаемся, что ОБА
        старта произошли раньше любого финиша.
        """
        order = []

        async def alerts(env):
            order.append("alerts_start")
            await asyncio.sleep(0.2)
            order.append("alerts_end")
            return []

        async def rules(env):
            order.append("rules_start")
            await asyncio.sleep(0.2)
            order.append("rules_end")
            return []

        self._patch(alerts, rules)
        self.client.get("/silences/test/alerts")

        self.assertEqual(set(order[:2]), {"alerts_start", "rules_start"},
                         f"источники не распараллелены, порядок: {order}")


class SharedHttpClient(unittest.TestCase):
    """Общий httpx-клиент silences: один экземпляр на процесс (keep-alive)."""

    def test_http_is_singleton(self):
        am_client._client = None            # сбрасываем для чистоты
        first = am_client._http()
        second = am_client._http()
        self.assertIs(first, second)        # тот же объект — соединения переиспользуются
        import httpx
        self.assertIsInstance(first, httpx.AsyncClient)

    def test_http_ignores_env_proxy(self):
        """trust_env=False — запрос к внутреннему адресу не уводится в HTTP(S)_PROXY."""
        am_client._client = None
        self.assertFalse(am_client._http().trust_env)


class DiskUsageByGroup(unittest.TestCase):
    """victoria.disk: под-вкладка «Диск» отдаёт строку на КАЖДУЮ группу vmstorage."""

    def setUp(self):
        self._orig_query = vdisk.client.query

    def tearDown(self):
        vdisk.client.query = self._orig_query

    @staticmethod
    def _vec(pairs):
        """Ответ vmselect в формате Prometheus: [(group|None, value), …] → bytes."""
        return json.dumps({"data": {"result": [
            {"metric": ({"group": g} if g is not None else {}), "value": [0, v]}
            for g, v in pairs
        ]}}).encode()

    def test_rows_per_group(self):
        """По группе на строку: свои занято/свободно/процент, ETA — где есть."""
        vec = self._vec

        async def fake_query(env, expr, time=None, tenant=None):
            if "[10m:]" in expr:                      # ETA_DAYS_EXPR (предиктивный)
                return vec([("main", "45")])
            if "vm_data_size_bytes" in expr:          # USED_EXPR
                return vec([("main", "600"), ("cold", "200")])
            return vec([("main", "400"), ("cold", "800")])   # FREE_EXPR
        vdisk.client.query = fake_query

        rows = asyncio.run(vdisk._one("x"))
        self.assertEqual([r["group"] for r in rows], ["cold", "main"])  # сортировка по группе
        main = rows[1]
        self.assertEqual(main["used"], 600.0)
        self.assertEqual(main["free"], 400.0)
        self.assertEqual(main["total"], 1000.0)
        self.assertAlmostEqual(main["percent"], 60.0)
        self.assertEqual(main["eta_days"], 45.0)
        self.assertIsNone(rows[0]["eta_days"])        # у cold прогноза нет — «—», не ошибка

    def test_cluster_down_single_error_row(self):
        """Кластер целиком недоступен — одна строка с ошибкой, без падения вкладки."""
        async def fake_query(env, expr, time=None, tenant=None):
            raise VictoriaError("нет связи")
        vdisk.client.query = fake_query

        rows = asyncio.run(vdisk._one("edge"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["env"], "edge")
        self.assertIn("нет связи", rows[0]["error"])

    def test_no_group_label_becomes_empty_group(self):
        """Метрики без лейбла group (обычный Prometheus) — одна строка с group=""."""
        vec = self._vec

        async def fake_query(env, expr, time=None, tenant=None):
            if "[10m:]" in expr:
                return vec([])
            return vec([(None, "100")])
        vdisk.client.query = fake_query

        rows = asyncio.run(vdisk._one("solo"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group"], "")
        self.assertEqual(rows[0]["total"], 200.0)


class ShortLinks(unittest.TestCase):
    """/share и /s/<id>: сохранить путь, получить редирект; защита от open redirect."""

    def setUp(self):
        # Хранилище — временный файл, чтобы тесты не трогали data/short_links.json.
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SHORT_LINKS_FILE"] = os.path.join(self._tmp.name, "links.json")
        app = FastAPI()
        app.include_router(share_router)
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("SHORT_LINKS_FILE", None)
        self._tmp.cleanup()

    def test_roundtrip(self):
        """Создали ссылку → /s/<id> редиректит ровно на сохранённый путь."""
        path = "/?m=victoria&env=full&tab=query&q=up&mode=graph"
        r = self.client.post("/share", json={"path": path})
        self.assertEqual(r.status_code, 200)
        link_id = r.json()["id"]

        r2 = self.client.get(f"/s/{link_id}", follow_redirects=False)
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r2.headers["location"], path)

    def test_same_path_same_id(self):
        """Повторное «поделиться» тем же видом не плодит новые id."""
        a = self.client.post("/share", json={"path": "/?m=silences&env=prod"}).json()["id"]
        b = self.client.post("/share", json={"path": "/?m=silences&env=prod"}).json()["id"]
        self.assertEqual(a, b)

    def test_rejects_absolute_and_protocol_relative(self):
        """Абсолютные URL и //host не принимаем — иначе open redirect с нашего домена."""
        for bad in ("https://evil.example/x", "//evil.example/x", "notslash"):
            r = self.client.post("/share", json={"path": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_unknown_id_404(self):
        r = self.client.get("/s/nope1234", follow_redirects=False)
        self.assertEqual(r.status_code, 404)


class ShortLinksPostgres(unittest.TestCase):
    """STORAGE_BACKEND=postgres: ссылки идут в БД (вместо psycopg2 — стаб-словарь)."""

    def setUp(self):
        self._orig = (share.USE_PG, share._pg_save, share._pg_get)
        self.db = {}
        share.USE_PG = True
        share._pg_save = lambda link_id, path: self.db.__setitem__(link_id, path)
        share._pg_get = lambda link_id: self.db.get(link_id)
        app = FastAPI()
        app.include_router(share_router)
        self.client = TestClient(app)

    def tearDown(self):
        share.USE_PG, share._pg_save, share._pg_get = self._orig

    def test_roundtrip_via_db(self):
        path = "/?m=victoria&env=prod&q=up"
        link_id = self.client.post("/share", json={"path": path}).json()["id"]
        self.assertEqual(self.db[link_id], path)   # легло в «БД», а не в файл
        r = self.client.get(f"/s/{link_id}", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], path)

    def test_db_down_returns_503(self):
        def boom(link_id, path):
            raise RuntimeError("нет связи с БД")
        share._pg_save = boom
        r = self.client.post("/share", json={"path": "/?m=victoria"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("хранилище ссылок недоступно", r.json()["detail"])


class ResolveTenant(unittest.TestCase):
    """victoria.config.resolve_tenant: подпись тенанта или None для одно-тенантного."""

    def test_solo_cluster_returns_none(self):
        self.assertIsNone(vconfig.resolve_tenant("solo", None))
        self.assertIsNone(vconfig.resolve_tenant("solo", "whatever"))

    def test_multi_valid_tenant_kept(self):
        self.assertEqual(vconfig.resolve_tenant("multi", "b"), "b")

    def test_multi_unknown_tenant_falls_back_to_first(self):
        # неизвестная/пустая подпись → первая по алфавиту (a)
        self.assertEqual(vconfig.resolve_tenant("multi", "zzz"), "a")
        self.assertEqual(vconfig.resolve_tenant("multi", None), "a")


if __name__ == "__main__":
    unittest.main()
