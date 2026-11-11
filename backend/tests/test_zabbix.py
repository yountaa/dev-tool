"""Тесты модуля zabbix: разбор конфигурации, перевод записи auditlog в слова,
запрос к ES, деградация без Zabbix API, 502 при недоступном ES.

Внешние сервисы не поднимаются — вызовы ES и Zabbix API подменяются.
Запуск:  cd backend && python -m unittest discover tests
"""
import asyncio
import os
import unittest

# Окружение — ДО импорта модуля: config читает его при импорте.
os.environ.setdefault("zbxaudit_t1", "zbx-audit-*")
os.environ.setdefault("zbxapi_t1", "http://zbx.test/api_jsonrpc.php")
os.environ.setdefault("zbxapitoken_t1", "tok")
os.environ.setdefault("zbxaudit_t2", "zbx2-audit")
os.environ.setdefault("ZBX_AUDIT_ES_URL", "http://es.test:9200")
os.environ.setdefault("SILENCE_TZ", "Europe/Moscow")

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from modules.zabbix import audit, client, config
from modules.zabbix.client import ZabbixAuditError
from modules.zabbix.routes import router


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ZabbixAuditError)
    async def _err(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    return TestClient(app)


DOC = {
    "auditid": "cabc", "userid": "7", "username": "ivanov", "clock": 1762599802,
    "ip": "10.1.2.3", "action": 1, "resourcetype": 4, "resourceid": "10084",
    "resourcename": "web-01", "recordsetid": "crs1",
    "details": '{"host.status":["update","1","0"],"host.groups[5]":["add"],'
               '"host.groups[5].groupid":["add","5"]}',
}


class Config(unittest.TestCase):
    def test_prefixes(self):
        """zbxapitoken_ не должен прочитаться как zbxapi_ окружения «token_…»."""
        self.assertEqual(config.INDEX["t1"], "zbx-audit-*")
        self.assertEqual(config.API["t1"], ["http://zbx.test/api_jsonrpc.php"])
        self.assertEqual(config.API_TOKEN["t1"], "tok")
        self.assertNotIn("token_t1", config.API)
        self.assertTrue(config.api_enabled("t1"))
        self.assertFalse(config.api_enabled("t2"))


class EsAuth(unittest.TestCase):
    def test_api_key_wins(self):
        """API key → заголовок ApiKey, basic auth при этом не отправляется."""
        old = (config.ES_API_KEY, config.ES_USER)
        config.ES_API_KEY, config.ES_USER = "enc==", "svc"
        try:
            self.assertEqual(client._es_headers(), {"Authorization": "ApiKey enc=="})
            self.assertIsNone(client._es_auth())
        finally:
            config.ES_API_KEY, config.ES_USER = old

    def test_two_nodes_failover(self):
        """Первая нода ES лежит → ответ со второй."""
        import httpx
        calls = []

        def handler(request):
            calls.append(request.url.host)
            if request.url.host == "es1":
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"hits": {"total": 0, "hits": []}})

        old = (config.ES_URLS, client._client)
        config.ES_URLS = ["http://es1:9200", "http://es2:9200"]
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            out = asyncio.run(client.es_search("idx", {}))
        finally:
            config.ES_URLS, client._client = old
        self.assertEqual(calls, ["es1", "es2"])
        self.assertEqual(out["hits"]["total"], 0)


class Normalize(unittest.TestCase):
    def test_record(self):
        r = audit.normalize(DOC)
        self.assertEqual(r["action_label"], "Обновлено")
        self.assertEqual(r["rtype_label"], "Узел сети")
        # 1762599802 = 2025-11-08 11:03:22 UTC = 14:03:22 МСК
        self.assertEqual(r["time"], "08.11.2025 14:03:22")
        fields = [c["field"] for c in r["changes"]]
        # служебная строка host.groups[5] без значения схлопнута в дочернюю
        self.assertEqual(fields, ["host.status", "host.groups[5].groupid"])
        st = r["changes"][0]
        self.assertEqual((st["new_label"], st["old_label"]), ("выключено", "включено"))
        self.assertEqual(st["label"], "Состояние")
        self.assertEqual(r["changes"][1]["label"], "Группы")

    def test_real_document(self):
        """Документ из zabbix-auditlog-jdbc-* как есть: числа — числами, details — строкой."""
        src = {
            "resourceid": 654, "action": 1, "resourcetype": 0, "resource_cuid": None,
            "userid": 398, "recordsetid": "cmw85cou700001ioclwii07c8",
            "@timestamp": "2026-11-08T18:15:23.874968964Z",
            "auditid": "cmw85cou800011iocib10s188", "username": "101287",
            "clock": 1794161364, "details": '{"user.roleid":["update","41","1"]}',
            "resourcename": "audit", "ip": "10.5.26.42", "tags": ["zabbix-auditlog"],
        }
        r = audit.normalize(src)
        self.assertEqual((r["rid"], r["userid"], r["rtype_label"]), ("654", "398", "Пользователь"))
        self.assertEqual(r["time"], "08.11.2026 21:09:24")
        c = r["changes"][0]
        self.assertEqual((c["label"], c["new"], c["old"]), ("Роль", "41", "1"))
        self.assertEqual(audit._ref_for(c["field"])[0], "role.get")

    def test_unknown_codes_kept(self):
        r = audit.normalize({"action": 99, "resourcetype": 999, "clock": 0})
        self.assertIn("99", r["action_label"])
        self.assertIn("999", r["rtype_label"])

    def test_bad_details(self):
        self.assertEqual(audit.parse_details("не json")[0]["new"], "не json")
        self.assertEqual(audit.parse_details(""), [])

    def test_wall_time(self):
        """Настенное время МСК → unix без сдвига на таймзону контейнера."""
        self.assertEqual(audit.to_epoch("2025-11-08T14:03:22"), 1762599802)


class Groups(unittest.TestCase):
    """Поля одного элемента списка (макрос, тег) — одной группой с заголовком."""

    def test_macro_created(self):
        changes = audit.parse_details(
            '{"host.macros[77]":["add"],"host.macros[77].hostmacroid":["add","77"],'
            '"host.macros[77].macro":["add","{$DB_HOST}"],"host.macros[77].value":["add","db-1"],'
            '"host.name":["update","b","a"],"host.macros[78].value":["update","2","1"]}'
        )
        grp = [c for c in changes if c.get("group") == "host.macros[77]"]
        self.assertEqual({c["group_title"] for c in grp}, {"Макрос {$DB_HOST}"})
        self.assertEqual({c["group_op"] for c in grp}, {"добавлено"})
        val = next(c for c in grp if c["field"].endswith(".value"))
        self.assertEqual(val["label"], "Значение")
        # ID элемента и имя макроса уже в заголовке — в строках не дублируются
        hidden = {c["field"] for c in grp if c["redundant"]}
        self.assertEqual(hidden, {"host.macros[77].hostmacroid", "host.macros[77].macro"})
        # поля одной группы идут подряд
        idx = [i for i, c in enumerate(changes) if c.get("group") == "host.macros[77]"]
        self.assertEqual(idx, list(range(idx[0], idx[0] + len(idx))))
        # правка без имени макроса — заголовок без имени, его подставит API
        upd = next(c for c in changes if c.get("group") == "host.macros[78]")
        self.assertEqual((upd["group_title"], upd["group_id"]), ("Макрос", "78"))

    def test_deleted_macro_header_only(self):
        changes = audit.parse_details('{"host.macros[9]":["delete"]}')
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]["header_only"])
        self.assertEqual(changes[0]["group_op"], "удалено")

    def test_untranslated_path_kept_original(self):
        """Полупереведённый путь хуже оригинала — показываем как есть."""
        c = audit.parse_details('{"host.inventory.os":["update","Linux","Windows"]}')[0]
        self.assertEqual(c["label"], "host.inventory.os")
        c = audit.parse_details('{"host.status":["update","1","0"]}')[0]
        self.assertEqual(c["label"], "Состояние")

    def test_macro_name_from_api(self):
        async def resolve(env, method, id_field, ids, output, extra=None):
            if method == "usermacro.get":
                self.assertEqual(ids, {"78"})
                return {"78": {"macro": "{$TIMEOUT}"}}
            return {}

        rec = audit.normalize({"action": 1, "resourcetype": 4, "resourceid": 1, "clock": 1,
                               "details": '{"host.macros[78].value":["update","2","1"]}'})
        orig = client.resolve
        client.resolve = resolve
        try:
            asyncio.run(audit.enrich("t1", [rec]))
        finally:
            client.resolve = orig
        self.assertEqual(rec["changes"][0]["group_title"], "Макрос {$TIMEOUT}")


class UiLinks(unittest.TestCase):
    def _rec(self, **kw):
        r = audit.normalize({"action": 1, "resourcetype": 13, "resourceid": 2001, "clock": 1})
        r.update(kw)
        return r

    def test_base_from_api_url(self):
        self.assertEqual(config.UI["t1"], "http://zbx.test")

    def test_trigger_on_template(self):
        r = self._rec(found=True, context="template")
        self.assertEqual(
            audit.ui_url("t1", r),
            "http://zbx.test/zabbix.php?action=popup&popup=trigger.edit&triggerid=2001&context=template",
        )

    def test_macro_links_to_its_host(self):
        """Ресурс «Макрос» → форма узла сети, на котором он живёт (у макроса своей формы нет)."""
        async def resolve(env, method, id_field, ids, output, extra=None):
            if id_field == "hostmacroid":
                return {"501": {"hosts": [{"hostid": "10084", "name": "web-01", "status": "0"}]}}
            if id_field == "globalmacroid":
                return {"7": {"macro": "{$SNMP}"}}
            return {}

        recs = [audit.normalize({"action": 0, "resourcetype": 29, "resourceid": rid, "clock": 1})
                for rid in (501, 7)]
        orig = client.resolve
        client.resolve = resolve
        try:
            asyncio.run(audit.enrich("t1", recs))
        finally:
            client.resolve = orig
        self.assertEqual(recs[0]["ui_url"], "http://zbx.test/zabbix.php?action=popup&popup=host.edit&hostid=10084")
        self.assertEqual(recs[0]["hosts"], ["web-01"])
        self.assertEqual(recs[1]["ui_url"], "http://zbx.test/zabbix.php?action=macros.edit")

    def test_no_link_for_deleted_or_missing(self):
        self.assertEqual(audit.ui_url("t1", self._rec(found=True, action=2)), "")
        # вход/выход/неудачный вход/выполнение — событие, а не изменение: без ссылки
        for action in (4, 7, 8, 9):
            r = audit.normalize({"action": action, "resourcetype": 0, "resourceid": 7, "clock": 1})
            self.assertEqual(audit.ui_url("t1", r), "")
        # API включён, а триггер не нашёлся — удалён позже, ссылка вела бы в ошибку
        self.assertEqual(audit.ui_url("t1", self._rec()), "")
        # у окружения без zbxapi_/zbxui_ ссылок нет
        self.assertEqual(audit.ui_url("t2", self._rec(found=True)), "")


class Review(unittest.TestCase):
    """Разбор по типам: LLD, прототипы, удалённые связи, периоды обслуживания."""

    def _enrich(self, recs, resolve):
        orig = client.resolve
        client.resolve = resolve
        try:
            asyncio.run(audit.enrich("t1", recs))
        finally:
            client.resolve = orig

    def test_lld_rule_is_52_with_host_and_link(self):
        self.assertEqual(audit.RESOURCE_TYPES[23], "Правило обнаружения сети")
        self.assertEqual(audit.RESOURCE_TYPES[52], "Правило низкоуровневого обнаружения")

        async def resolve(env, method, id_field, ids, output, extra=None):
            self.assertEqual(method, "discoveryrule.get")
            return {"900": {"hosts": [{"name": "Linux by Zabbix agent", "status": "3"}]}}

        r = audit.normalize({"action": 1, "resourcetype": 52, "resourceid": 900, "clock": 1})
        self._enrich([r], resolve)
        self.assertEqual(r["hosts"], ["Linux by Zabbix agent"])
        self.assertEqual(r["ui_url"], "http://zbx.test/host_discovery.php?form=update&itemid=900&context=template")

    def test_prototype_link_needs_parent_lld(self):
        async def resolve(env, method, id_field, ids, output, extra=None):
            self.assertIn("selectDiscoveryRule", extra)
            return {"31000": {"hosts": [{"name": "web-01", "status": "0"}], "discoveryRule": {"itemid": "900"}}}

        r = audit.normalize({"action": 1, "resourcetype": 36, "resourceid": 31000, "clock": 1})
        self._enrich([r], resolve)
        self.assertEqual(
            r["ui_url"],
            "http://zbx.test/zabbix.php?action=popup&popup=item.prototype.edit"
            "&itemid=31000&parent_discoveryid=900&context=host",
        )

    def test_host_removed_from_maintenance_named_from_history(self):
        """Удаление узла из обслуживания: в записи только ID связи — узел берём из
        записи, где эту связь добавляли, а имя — из API."""
        rec = audit.normalize({"action": 1, "resourcetype": 27, "resourceid": 15, "clock": 100,
                               "details": '{"maintenance.hosts[55]":["delete"]}'})
        self.assertEqual(rec["changes"][0]["group_kind"], "hosts")

        async def es(index, body):
            should = body["query"]["bool"]["should"]
            self.assertEqual(should, [{"wildcard": {"details": {"value": '*"maintenance.hosts[55].hostid"*'}}}])
            return {"hits": {"hits": [{"_source": {
                "details": '{"maintenance.hosts[55]":["add"],"maintenance.hosts[55].hostid":["add","10084"]}'}}]}}

        async def resolve(env, method, id_field, ids, output, extra=None):
            if method == "host.get":
                return {"10084": {"name": "web-01"}}
            return {}

        orig = client.es_search
        client.es_search = es
        try:
            asyncio.run(audit.resolve_deleted_links("idx", [rec]))
        finally:
            client.es_search = orig
        self._enrich([rec], resolve)
        c = rec["changes"][0]
        self.assertEqual((c["label"], c["op_label"], c["old"], c["old_label"]),
                         ("Узлы сети", "удалено", "10084", "web-01"))
        self.assertNotIn("group", c)

    def test_maintenance_period_in_words(self):
        changes = audit.parse_details(
            '{"maintenance.timeperiods[3]":["add"],'
            '"maintenance.timeperiods[3].timeperiod_type":["add","3"],'
            '"maintenance.timeperiods[3].start_time":["add","79200"],'
            '"maintenance.timeperiods[3].period":["add","9000"],'
            '"maintenance.timeperiods[3].dayofweek":["add","21"]}'
        )
        got = {c["field"].rsplit(".", 1)[-1]: c["new_label"] for c in changes}
        self.assertEqual(got["timeperiod_type"], "еженедельно")
        self.assertEqual(got["start_time"], "22:00")
        self.assertEqual(got["period"], "2 ч 30 мин")
        self.assertEqual(got["dayofweek"], "пн, ср, пт")
        self.assertEqual({c["group_title"] for c in changes}, {"Период"})


class Query(unittest.TestCase):
    def test_filters(self):
        body = audit.build_query({"from": 1, "to": 2, "actions": [0, 2], "user": "ivanov", "q": "cpu"})
        flt = body["query"]["bool"]["filter"]
        self.assertEqual(flt[0], {"range": {"clock": {"gte": 1, "lte": 2}}})
        self.assertIn({"terms": {"action": [0, 2]}}, flt)
        self.assertIn({"term": {"username": {"value": "ivanov", "case_insensitive": True}}}, flt)
        # свободный поиск — подстрокой по keyword-полям, в т.ч. по details
        q = flt[-1]["bool"]["should"]
        self.assertIn({"wildcard": {"details": {"value": "*cpu*", "case_insensitive": True}}}, q)
        self.assertEqual(body["sort"][0], {"clock": {"order": "desc"}})
        self.assertLessEqual(body["size"], config.MAX_RECORDS)


class Routes(unittest.TestCase):
    def setUp(self):
        self.c = _app()
        self._es, self._res = client.es_search, client.resolve

    def tearDown(self):
        client.es_search, client.resolve = self._es, self._res

    def test_environments(self):
        envs = self.c.get("/zabbix/environments").json()
        self.assertIn({"name": "t1", "api": True}, envs)
        self.assertIn({"name": "t2", "api": False}, envs)

    def test_search_with_api_names(self):
        async def es(index, body):
            self.assertEqual(index, "zbx-audit-*")
            return {"hits": {"total": {"value": 1}, "hits": [{"_source": DOC}]}}

        async def resolve(env, method, id_field, ids, output, extra=None):
            if method == "hostgroup.get":
                return {"5": {"name": "Linux servers"}}
            return {}

        client.es_search, client.resolve = es, resolve
        out = self.c.get("/zabbix/t1/audit?last=3600&actions=1,x").json()
        self.assertEqual(out["total"], 1)
        grp = out["records"][0]["changes"][1]
        self.assertEqual(grp["new_label"], "Linux servers")

    def test_api_failure_degrades(self):
        """Падение Zabbix API не ломает выдачу — остаются ID."""
        async def es(index, body):
            return {"hits": {"total": 1, "hits": [{"_source": DOC}]}}

        async def boom(*a, **k):
            raise ZabbixAuditError("api down")

        client.es_search = es
        orig = client._api_call
        client._api_call = boom
        try:
            out = self.c.get("/zabbix/t1/audit").json()
        finally:
            client._api_call = orig
        self.assertEqual(out["records"][0]["changes"][1]["new"], "5")

    def test_es_down_is_502(self):
        async def es(index, body):
            raise ZabbixAuditError("нет связи с ES")

        client.es_search = es
        r = self.c.get("/zabbix/t1/audit")
        self.assertEqual(r.status_code, 502)

    def test_search_by_host_finds_its_triggers(self):
        """«web-01» в названии ресурса → в запрос попадают ID триггеров узла из API."""
        calls = []

        async def api(env, method, params):
            calls.append(method)
            if method == "host.get":
                return [{"hostid": "10084", "parentTemplates": [{"templateid": "10001"}]}]
            if method == "trigger.get":
                self.assertEqual(params["hostids"], ["10084", "10001"])
                return [{"triggerid": "2001"}, {"triggerid": "2002"}]
            return []

        bodies = []

        async def es(index, body):
            bodies.append(body)
            return {"_shards": {"total": 1}, "hits": {"total": 0, "hits": []}}

        orig = client._api_call
        client._api_call, client.es_search = api, es
        try:
            self.c.get("/zabbix/t1/audit?name=web-01")
        finally:
            client._api_call = orig
        name_clause = bodies[0]["query"]["bool"]["filter"][-1]["bool"]["should"]
        self.assertIn(
            {"bool": {"filter": [{"term": {"resourcetype": 13}}, {"terms": {"resourceid": ["2001", "2002"]}}]}},
            name_clause,
        )
        self.assertIn(
            {"bool": {"filter": [{"term": {"resourcetype": 30}}, {"terms": {"resourceid": ["10001"]}}]}},
            name_clause,
        )

    def test_no_accessible_indices(self):
        """Паттерн не совпал ни с одним доступным индексом → понятная 502, не «пусто»."""
        async def es(index, body):
            return {"_shards": {"total": 0}, "hits": {"total": {"value": 0}, "hits": []}}

        client.es_search = es
        r = self.c.get("/zabbix/t1/audit")
        self.assertEqual(r.status_code, 502)
        self.assertIn("права", r.json()["detail"])

    def test_empty_but_index_exists(self):
        """Индекс есть, записей за период нет — это нормальный пустой ответ."""
        async def es(index, body):
            return {"_shards": {"total": 1}, "hits": {"total": {"value": 0}, "hits": []}}

        client.es_search = es
        self.assertEqual(self.c.get("/zabbix/t1/audit").json()["total"], 0)

    def test_unknown_env_and_bad_period(self):
        self.assertEqual(self.c.get("/zabbix/nope/audit").status_code, 404)
        self.assertEqual(self.c.get("/zabbix/t1/audit?from=вчера").status_code, 400)


if __name__ == "__main__":
    unittest.main()
