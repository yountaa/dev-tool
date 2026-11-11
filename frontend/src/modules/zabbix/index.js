// Дескриптор модуля Zabbix Audit — то, что видит App и реестр.
// icon — значение атрибута d у <path> (планшет со списком: журнал действий).
import ZabbixModule from './ZabbixModule.vue'

export default {
  id: 'zabbix', // совпадает с KNOWN_MODULES и access_zabbix
  title: 'Zabbix Audit',
  subtitle: 'просмотр изменений',
  icon: 'M9 4h6v3H9zM9 5.5H6a1 1 0 00-1 1V20a1 1 0 001 1h12a1 1 0 001-1V6.5a1 1 0 00-1-1h-3M8.5 12h7M8.5 16h5',
  component: ZabbixModule,
}
