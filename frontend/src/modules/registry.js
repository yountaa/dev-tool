// Реестр модулей приложения.
//
// Чтобы подключить новый модуль:
//   1) создаёшь папку src/modules/<имя>/ со своим index.js (см. silences/index.js);
//   2) импортируешь его сюда и добавляешь в массив modules.
// Больше нигде ничего менять не надо — левый рельс и вкладки строятся отсюда.
// (RBAC: id модуля должен совпадать с тем, что бэкенд отдаёт в /access/me.modules
//  и с access_<id> в окружении — тогда вкладка скрывается у тех, кому не положено.)
import silences from './silences/index.js'
import victoria from './victoria/index.js'

export const modules = [
  silences,
  victoria,
]
