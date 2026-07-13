"""Тесты на изменения из ревью: параллельность и обработка ошибок на вкладке
«Алерты», общий httpx-клиент silences, тип/логика resolve_tenant.

Внешние сервисы (Alertmanager/Prometheus/VM) не нужны — их вызовы подменяем.
Запуск:  cd backend && python -m unittest discover tests
"""
import asyncio
import os
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
