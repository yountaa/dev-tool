// Дескриптор модуля Victoria Metrics — то, что видит App и реестр.
// icon — значение атрибута d у <path> (рисуем «график/метрики»).
import VictoriaModule from './VictoriaModule.vue'

export default {
  id: 'victoria',
  title: 'Victoria Metrics',
  subtitle: 'метрики, targets, правила',
  // Простой график-иконка: оси + линия.
  icon: 'M3 3v18h18M7 15l4-5 3 3 5-7',
  component: VictoriaModule,
}
