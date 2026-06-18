// Дескриптор модуля — то, что видит App и реестр.
// icon — это значение атрибута d у <path> (рисуем колокольчик).
import SilencesModule from './SilencesModule.vue'

export default {
  id: 'silences',
  title: 'Silence Manager',
  subtitle: 'модуль silences',
  icon: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  component: SilencesModule,
}
