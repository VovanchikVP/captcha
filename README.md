# show-img

Сервис для разметки капчи: показывает картинки из таблицы `images`, у которых не
заполнено поле `answer`, и даёт вписать ответ. После сохранения картинка пропадает
из выдачи. Фронт опрашивает бэкенд раз в 5 секунд.

## Запуск

```bash
uv sync
cp .env.example .env    # и прописать DATABASE_URL
uv run uvicorn app:app --reload
```

Открыть http://127.0.0.1:8000

## Переменные окружения

| Переменная     | Описание                                    | По умолчанию |
|----------------|---------------------------------------------|--------------|
| `DATABASE_URL` | Строка подключения к Postgres (обязательна) | —            |
| `PAGE_SIZE`    | Сколько картинок отдавать за один опрос     | `30`         |

## API

- `GET /api/images` → `[{id, image}]` — картинки, где `answer IS NULL OR answer = ''`
- `POST /api/images/{id}/answer` c телом `{"answer": "..."}` — записать ответ

Поле `image` может содержать голый base64, `data:`-URI или URL — бэкенд приводит
значение к виду, пригодному для `<img src>`.

## Деплой в k3s

Манифесты лежат в `k8s/`: Namespace, Deployment, Service, Ingress (Traefik — он идёт
в k3s из коробки) и пример Secret со строкой подключения.

### 1. Собрать образ и завезти его в кластер

Если реестра нет, образ можно импортировать в containerd узла напрямую:

```bash
docker build -t show-img:latest .
docker save show-img:latest | sudo k3s ctr images import -
```

Если реестр есть — обычный путь, плюс поправить тег в `k8s/kustomization.yaml`:

```bash
docker build -t registry.example.com/show-img:1.0.0 .
docker push registry.example.com/show-img:1.0.0
```

```yaml
images:
  - name: show-img
    newName: registry.example.com/show-img
    newTag: 1.0.0
```

### 2. Создать Secret со строкой подключения

```bash
cp k8s/secret.example.yaml k8s/secret.yaml
# вписать реальный DATABASE_URL; файл в .gitignore и в git не попадёт
```

### 3. Раскатить

```bash
kubectl apply -k k8s/
kubectl -n show-img rollout status deploy/show-img
```

### 4. Открыть

В `k8s/ingress.yaml` указан host `show-img.local` — замените на свой домен и
направьте его на IP узла k3s. Для быстрой проверки без DNS:

```bash
kubectl -n show-img port-forward svc/show-img 8080:80
```

### Что заложено в манифесты

- `readinessProbe` → `/readyz` (ходит в базу): при недоступном Postgres под
  убирается из Service, но **не** перезапускается — за рестарты отвечает
  `livenessProbe` → `/healthz`, который в базу не ходит.
- `startupProbe` даёт до 60 секунд на первый коннект к базе.
- Контейнер работает от uid 10001, с `readOnlyRootFilesystem` и сброшенными
  capabilities; под запись смонтирован только `emptyDir` на `/tmp`.
- `replicas: 1` — реплик можно поднять больше, состояние целиком в Postgres.
  Единственный нюанс: два человека могут получить одну картинку и переписать
  ответ друг друга (последний выигрывает).
