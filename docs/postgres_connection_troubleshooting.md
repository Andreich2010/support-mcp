# Ошибки подключения Django к PostgreSQL — руководство по диагностике

Этот документ содержит типовые причины и способы устранения ошибок подключения Django-приложений к базе PostgreSQL.

---

## 1. Типичная ошибка: `password authentication failed`

Пример лога:

psycopg2.OperationalError: connection to server failed:
FATAL: password authentication failed for user "support_user"

markdown
Копировать код

Причины:

- неверный пароль для пользователя;
- пользователь не создан;
- нет прав на доступ к базе.

Что проверить:

1. Переменные окружения:
   - DB_HOST
   - DB_PORT
   - DB_NAME
   - DB_USER
   - DB_PASSWORD

2. Создан ли пользователь:
   ```sql
   \du
Создана ли база:

sql
Копировать код
\l
Может ли пользователь подключиться:

bash
Копировать код
psql -h postgres -U support_user -d supportdb
2. Проблема: контейнер PostgreSQL ещё не запустился
Ошибка:

nginx
Копировать код
connection refused
Решение:

добавить depends_on в docker-compose,

увеличить время ожидания запуска БД,

убедиться, что порт 5432 открыт.

3. Ошибка: database does not exist
Проверьте:

POSTGRES_DB в docker-compose,

имя базы в переменных окружения Django.

4. Ошибка: no pg_hba.conf entry
Возникает при подключении с внешнего хоста.
В Docker встречается редко.

5. Рекомендованный docker-compose для Django + PostgreSQL
yaml
Копировать код
services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_DB: supportdb
      POSTGRES_USER: support_user
      POSTGRES_PASSWORD: strongpass
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  app:
    build: .
    environment:
      DB_HOST: postgres
      DB_USER: support_user
      DB_PASSWORD: strongpass
      DB_NAME: supportdb
    depends_on:
      - postgres

volumes:
  postgres_data:
6. Частый алгоритм исправления
Проверить переменные окружения.

Пересоздать контейнер PostgreSQL.

Проверить возможность ручного подключения.

Перезапустить приложение.

