// Реестр модулей приложения.
//
// Чтобы подключить новый модуль:
//   1) создаёшь папку src/modules/<имя>/ со своим index.js (см. silences/index.js);
//   2) импортируешь его сюда и добавляешь в массив modules.
// Больше нигде ничего менять не надо — левый рельс и вкладки строятся отсюда.
import silences from './silences/index.js'

export const modules = [
  silences,
]
