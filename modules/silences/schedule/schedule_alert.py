import json
import sys
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Открываем файл
def build_message (message: dict):

      windows = message.windows
      created_by = message.created_by
      comment = message.comment

      # Поиск дня с учетом прошедшего времени (например silence для сегодня уже прошел, создаём silence на завтра)
      target_days = [d.lower() for d in days]
      check_dt = now_in_tz
      found_day = False

      # Извлекаем время окончания, чтобы проверить, не прошло ли оно сегодня
      end_time = schedule.get('end')
      end_h, end_m = map(int, end_time.split(':')) #Делим время на часы и минуты

      for _ in range(7):  # Ищем максимум на 7 дней вперед
          current_day_name = check_dt.strftime("%a").lower() # %a возвращает краткое имя дня недели (mon, tue, wed...)

          if current_day_name in target_days:
              # Если день совпал с сегодняшним, проверяем: не закончилось ли уже правило?
              if check_dt.date() == now_in_tz.date():
                  # Создаем время окончания для СЕГОДНЯ
                  today_end_dt = check_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

                  # Проверяем, есть ли переход через полночь
                  start_time = schedule.get('start')
                  start_h, start_m = map(int, start_time.split(':'))
                  today_start_dt = check_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

                  if today_end_dt <= today_start_dt:
                      # Если end <= start, значит сайленс заканчивается только на следующий день утром.
                      # Сегодняшнее правило еще актуально, берем его.
                      found_day = True
                      break

                  # Если перехода через полночь нет и текущее время уже больше времени окончания:
                  if now_in_tz >= today_end_dt:
                      check_dt += timedelta(days=1)  # Сегодня уже поздно, шагаем на завтра
                      continue                       # Переходим на следующий круг цикла

              found_day = True
              break

          check_dt += timedelta(days=1)
      # ---------------------------------------------------------

      if found_day:
          # Вытаскиваю не вложенные значения словаря
          item_id = read_file.get('id')
          createdBy = read_file.get('createdBy')
          comment = read_file.get('comment')
          # Собираю сразу коммент с ID в одну переменную
          full_comment = f"id: {item_id} comment: {comment}"
  #-----------------------------------------------------------------------------
          # Вытаскиваю вложенные значения (schedule)
          start_time = schedule.get('start') # Время начала действия silence
          end_time = schedule.get('end') # Время окончания действия silence

          # Парсим время сразу (гугл подсказал)
          start_h, start_m = map(int, start_time.split(':'))
          end_h, end_m = map(int, end_time.split(':'))

          # Собираем полные объекты datetime (привязываем к НАЙДЕННОЙ дате check_dt)
          start_dt = check_dt.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
          end_dt = check_dt.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

          # Если дата end меньше start то end будет на следующий день
          if end_dt <= start_dt:
              end_dt += timedelta(days=1)

          # Перевод в UTC
          start_utc = start_dt.astimezone(ZoneInfo("UTC"))
          end_utc = end_dt.astimezone(ZoneInfo("UTC"))

          # Форматируем для AM
          startsAt = start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
          endsAt = end_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
  #-----------------------------------------------------------------------------
          # Вытаскиваю все поля matchers в цикле
          matchers = read_file.get('matchers', []) # Извлекаем вложенный список
          new_matchers = [] # Создадим пустой список mathers
          for el_matchers in matchers:
              # Создадим словарь для matchers
              matchers_item = {
                  "name": el_matchers['name'],
                  "value": el_matchers['value'],
                  "isRegex": el_matchers['isRegex']
              }
              new_matchers.append(matchers_item) # Добавляем словарь в список

          # Вытаскиваем список (внутри словари) из АМ
          req = requests.get(f"{alertmanager_url}/api/v2/silences") # Запрос в АМ
          data_api_json = req.json() # Это уже список и надо работать со списком
          new_data_api = [] # Пустой список
          for el_req in data_api_json: # Если el_req = data_api_json
              if el_req['status']['state'] in ['active', 'pending']: # Если правило активно или pending
                  clean_api_matchers = [] # Список matchers в котором будт только name, value, isRegex
                  for m in el_req.get('matchers', []):
                      clean_api_matchers.append({
                          "name": m['name'],
                          "value": m['value'],
                          "isRegex": m['isRegex']
                      })

                  # Собираю словарь чтобы в будущем его сравнить
                  el_req_item = {
                      "endsAt": el_req['endsAt'],
                      #"startsAt": el_req['startsAt'],
                      "comment": el_req['comment'],
                      "matchers": clean_api_matchers
                  }
                  new_data_api.append(el_req_item)

          # Сравниваю comment + дату конца из файла и из АМ
          # Временный словарь для сравнения
          current_check = {
              #"startsAt": startsAt,
              "endsAt": endsAt,
              "comment": full_comment,
              "matchers": new_matchers
          }
          if current_check in new_data_api:
              print(f"silence {file_path} уже существует в Alertmanager!")
          else:
              new_data = {
                  "matchers": new_matchers,
                  "startsAt": startsAt,
                  "endsAt": endsAt,
                  "createdBy": createdBy,
                  "comment": full_comment
              }
              # Отправка json в АМ
              response = requests.post(f"{alertmanager_url}/api/v2/silences", json=new_data)
              print("Новый silence создан в Alertmanager")

      else:
          print(f"В расписании файла {file_path} не найдены подходящие дни недели!")
