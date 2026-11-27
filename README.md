# Социальная сеть — практики 9 и 10

Шесть микросервисов из предыдущих работ дополнены Keycloak, проверкой JWT и ролей,
мониторингом Prometheus и централизованными логами Loki. Grafana показывает метрики
API и шести БД. Docker Compose содержит 24 контейнера.

## Запуск

Нужны Docker Desktop с Linux containers и Python 3.12. Порты прежнего стенда
8001–8006, 55432–55435, 57017–57018, 5672 и 15672 должны быть свободны.

```powershell
docker compose up -d --build
docker compose ps
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_unit_tests.py
.\.venv\Scripts\python.exe verify_practices.py --outages
```

Первый запуск требует загрузки образов, сборки API, импорта realm и миграций Grafana.
Дождитесь healthy у Keycloak и шести API. Скрипт проверки также ожидает готовность
Grafana. Без `--outages` он не останавливает контейнеры. С этим флагом на время
проверки останавливаются Keycloak и CommunityService; блоки finally запускают их
обратно. Протоколы создаются в `test-results` и исключены из Git.

| Интерфейс | Адрес |
|---|---|
| Keycloak | http://localhost:8080 |
| OIDC discovery | http://localhost:8080/realms/social-network/.well-known/openid-configuration |
| Grafana | http://localhost:3000/d/miar-social/miar-social-network |
| Prometheus | http://localhost:9090 |
| Loki API | http://localhost:3100 |
| API шести сервисов | http://localhost:8001 … http://localhost:8006 |
| Метрики каждого API | `/metrics` |

## Keycloak и роли

Realm: `social-network`. Confidential client: `social-api`.
Секрет учебного клиента: `social-api-local-demo-secret`.
Пользователи `student` и `other` имеют роль `user`; `moderator` — `user` и
`moderator`; `viewer` имеет только `viewer`. Пароль всех четырех учебных
пользователей: `Demo-pass-2026!`.

Администратор Keycloak: `admin` / `Admin-demo-2026!`.
Администратор Grafana: `admin` / `Grafana-demo-2026!`.
Для просмотра учебного дашборда включен анонимный Viewer на localhost.
Это локальная учебная конфигурация с HTTP и демонстрационными учетными данными.

JWT передается как `Authorization: Bearer <access_token>`. Бэкенд вызывает
Keycloak introspection с аутентификацией клиента; проверяет active, issuer,
audience, срок действия, тип access token и роль. Для командного демонстрационного
скрипта включен Direct Access Grant с scope `openid`. Standard Flow также включен
в конфигурации, но отдельный браузерный клиент не реализован.

Создание, изменение и удаление требуют `user`. Операции MessengerService и
жалобы требуют `user`; список всех жалоб и принятие решения — `moderator`.
Открыты чтение профилей/публикаций/сообществ/комментариев, `/health` и `/metrics`.
JWT subject заменяет входящий `X-User-ID`; `X-Moderator-Key` больше не дает прав.
Автор публикации обязан совпадать с subject. Изменять профиль или удалять пост
может его владелец либо модератор. Профиль создается с UUID subject из Keycloak.

## Observability

Выбраны две технологии: мониторинг и логирование. Трассировка не реализуется.
Prometheus опрашивает шесть API и шесть DB-exporters раз в 5 секунд. Доступны
`social_http_requests_total`, histogram задержек `social_http_request_duration_seconds`,
`pg_up`, `pg_stat_database_numbackends`, `mongodb_up` и метрики MongoDB serverStatus.
`up` показывает доступность цели опроса, `pg_up`/`mongodb_up` — соединение с БД.
Правило `SocialTargetDown`: `up == 0` в течение 15 секунд.

Alloy обнаруживает только контейнеры этого Compose с `miar.logs=true`, собирает
stdout/stderr API, БД и Keycloak и отправляет их в Loki. JSON-логи API содержат
service, method, route, status, duration_ms и request_id. Авторизационные заголовки,
JWT, пароли и тела сообщений в access log не записываются. `X-Request-ID`
передается при вложенных HTTP-вызовах; пример запроса LogQL:

```logql
{kind="api"} |= "miar-demo-"
```

Дашборд, источники и правила настраиваются из `observability` автоматически.
Loki хранит данные в именованном volume; Docker-логи ротируются по 10 МБ × 3.
Доступ Alloy к Docker socket предназначен для доверенного локального стенда.

## Проверки

`python run_unit_tests.py` — изолированный набор; контейнеры не нужны.
`python verify_practices.py --outages` — реальные HTTP, Keycloak, PostgreSQL,
MongoDB, RabbitMQ, Prometheus, Loki и Grafana. Проверяются 401/403/201, подмена
автора, истечение настоящего JWT, отказ Identity Provider, обнаружение остановки
API, метрики БД, логи всех сервисов и корреляция Content → Users.

Старые файлы `tests/test_*.py` относятся к контракту практик 7–8 с внутренними
заголовками. Они сохранены для сравнения; `pytest.ini` выбирает актуальный unit-набор,
а проверка нового внешнего контракта выполняется через `verify_practices.py`.
Первоначальный проект практик 5–8 находится в отдельной предыдущей версии.

Остановка с сохранением данных: `docker compose down`.
