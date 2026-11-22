# Социальная сеть — практики 5–8

Реализованы **шесть** самостоятельных микросервисов, по четыре бизнес-операции в каждом.
Каждый сервис собирается собственным Dockerfile и использует только свою БД.

| Сервис | Порт API | Хранилище | Порт БД на хосте |
|---|---:|---|---:|
| UsersService | 8001 | PostgreSQL users | 55432 |
| ContentService | 8002 | MongoDB social_content | 57017 |
| InteractionService | 8003 | PostgreSQL interactions | 55433 |
| CommunityService | 8004 | PostgreSQL communities | 55434 |
| MessengerService | 8005 | MongoDB social_messenger | 57018 |
| ModerationService | 8006 | PostgreSQL moderation | 55435 |

## Запуск

Нужны Docker Desktop с Linux containers, Docker Compose и Python 3.12.
Откройте эту папку в VS Code. Из корня проекта выполните:

```powershell
docker compose up -d --build
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_unit_tests.py
```

Дождитесь готовности контейнеров (`docker compose ps`). Порты опубликованы только
на `127.0.0.1`. Панель RabbitMQ: http://127.0.0.1:15672, логин/пароль `social` / `social`.
Остановка без удаления данных: `docker compose down`. Именованные volumes сохраняются.
В составе проекта 13 контейнеров: 6 API, 6 БД и RabbitMQ.

## Практика 5

Для очередей запустите сначала получатель, затем отправитель в другом терминале:

```powershell
python practice5/queue_receiver.py exclusive
python practice5/queue_sender.py exclusive
```

Аналогично используйте аргументы `durable` и `auto`. Для завершения получателя нажмите Ctrl+C.
Эксклюзивная очередь удалится с закрытием соединения, auto — после отключения последнего потребителя.


```powershell
python practice5/rabbit_demo.py queues
python practice5/rabbit_demo.py publish --mode fanout --workload "###"
python practice5/rabbit_demo.py worker --mode fanout --queue social.fanout.feed --once
python practice5/rabbit_demo.py worker --mode fanout --queue social.fanout.audit --once
python practice5/rabbit_demo.py publish --mode direct --key moderation --workload "**"
python practice5/rabbit_demo.py worker --mode direct --queue social.direct.moderation --once
python practice5/rabbit_demo.py publish --mode topic --key content.post.created --workload "--"
python practice5/rabbit_demo.py worker --mode topic --queue social.topic.posts --once
python practice5/rabbit_demo.py worker --mode topic --queue social.topic.all_content --once
```

Для распределения нагрузки запустите несколько workers одной очереди в разных терминалах.
Без `--once` потребитель работает непрерывно. Один символ нагрузки означает одну секунду.
Автопроверка ускоряет сон до 0.01 секунды на символ, сохраняя проверку маршрутизации.
Повторный запуск verify_broker очищает только демонстрационные очереди этого проекта.

Проверка перезапуска (не запускайте consumers между двумя фазами):

```powershell
python practice5/check_restart.py prepare
docker compose restart rabbitmq
# Дождитесь healthy у rabbitmq в docker compose ps
python practice5/check_restart.py verify
```

Фаза prepare очищает собственные демонстрационные очереди. Verify подтверждает сохранение
persistent-сообщений fanout/topic и исчезновение non-durable direct-очереди.

## Практики 7–8 — реализация и тесты

В каждой папке `*_service` находятся `app.py` и собственный `Dockerfile`.
Модуль `common/api.py` содержит только HTTP-проверки зависимостей, без общей БД.

Это внутренний учебный срез. Внешняя аутентификация AuthService/Gateway остается
проектом из практики 6. Заголовок `X-User-ID` моделирует идентификатор, проверенный
доверенным шлюзом; это не реализация проверки пользовательского токена.
Доступ к списку жалоб и решениям ограничен заголовком `X-Moderator-Key`;
демонстрационное значение в Compose — `social-demo-moderator`.

### Взаимодействия и инварианты

- Content проверяет автора в Users перед записью поста.
- Interaction проверяет пользователя в Users и публикацию в Content;
  повторный лайк не создает дубликат.
- Community проверяет пользователя в Users; сообщество и роль owner создаются
  одной транзакцией, повторное вступление идемпотентно, выход owner дает 409.
- Messenger проверяет участников в Users; читать/отправлять сообщения могут
  только участники диалога. `$inc` задает порядок сообщений, `$max` не позволяет
  сдвинуть курсор прочтения назад.
- Moderation проверяет заявителя в Users и объект жалобы в Users/Content/Interaction.
  Решение и outbox записываются одной транзакцией. Событие отправляется в durable-очередь
  `social.moderation.decisions` с delivery_mode=2 и publisher confirm.
  Повтор того же решения не создает новое событие; другое решение дает 409.
  Сервис сохраняет решение и публикует событие; автоматическое применение блокировки
  в сервисе-владельце данных в этом учебном срезе не реализовано.

Если RabbitMQ недоступен, решение остается записанным, а событие — pending.
Повторная отправка:

```powershell
docker compose exec moderation-service python -m moderation_service.publish_outbox --once
```

Без `--once` команда проверяет outbox каждые 5 секунд. Доставка at least once:
потребителю нужно устранять повторы по `event_id`.

### Unit-тесты

Docker, БД и RabbitMQ для этого набора не нужны. После установки зависимостей:

```powershell
python run_unit_tests.py
```

112 проверок вызывают функции исходных сервисов напрямую. `unittest.mock`
заменяет PostgreSQL, MongoDB, HTTP и AMQP. Попытка открыть реальное сетевое
соединение завершает unit-тест ошибкой. Конструкторы хранилищ подменяются до
импорта; для каждого теста создаются новые mock-объекты. Протокол JUnit
сохраняется в `test-results/unit-tests.xml` и не включается в Git.

| Модуль | Проверок |
|---|---:|
| UsersService | 16 |
| ContentService | 15 |
| InteractionService | 13 |
| CommunityService | 14 |
| MessengerService | 17 |
| ModerationService | 26 |
| common/api.py | 11 |
| Всего | 112 |

Набор проверяет бизнес-правила, валидацию, права доступа, параметры обращения
к хранилищу, обработку отказов и формирование событий. Реальные ограничения БД,
атомарность операций MongoDB, сетевые контракты и доставка сообщений проверяются
интеграционными тестами отдельно.

### Интеграционные тесты

При работающем Docker Compose запускайте только файлы этого уровня:

```powershell
python -m pytest tests --ignore=tests/unit -v --junitxml=test-results/integration.xml
```

Набор включает 73 проверки HTTP-интерфейсов, собственных БД сервисов,
межсервисной цепочки и обработки отказов. Тестовые UUID уникальны; фикстуры
удаляют только записи своего запуска. Скрипты RabbitMQ находятся в `practice5`.
