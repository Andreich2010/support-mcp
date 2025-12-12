трументе auto_resolve_ticket.
📁 docs/faq.md
(чтобы бот мог отвечать на простые вопросы)

markdown
Копировать код
# Часто задаваемые вопросы (FAQ)

### Как посмотреть логи Django?
Внутри контейнера:
```bash
docker compose logs app -f
Как подключиться к PostgreSQL?
bash
Копировать код
docker compose exec postgres psql -U postgres
Где хранить переменные окружения?
В файле .env или в секции environment контейнера.

Как обновить контейнер приложения?
docker build -t support-mcp:latest .

docker tag support-mcp:latest <registry>/support-mcp:v1

docker push <registry>/support-mcp:v1

Обновить deployment в облаке.

