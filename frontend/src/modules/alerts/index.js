// Дескриптор модуля — то, что видит App и реестр.
// icon — значение атрибута d у <path>: рисуем «сирену» (треугольник с восклицанием).
import AlertsModule from './AlertsModule.vue'

export default {
  id: 'alerts',
  title: 'Alert Constructor',
  subtitle: 'модуль alerts',
  icon: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0M12 9v4M12 17h.01',
  component: AlertsModule,
}
