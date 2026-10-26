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

Узел k3s не видит образы из локального Docker — их нужно класть в containerd
**на самом узле**, иначе kubelet пойдёт за образом в docker.io и упадёт с
`pull access denied, repository does not exist`.

Собирать нужно под архитектуру узла и без provenance-обвязки buildx
(иначе в тарболе окажется вложенный manifest list, который containerd не всегда
разворачивает). Для узла на x86:

```bash
docker build --platform linux/amd64 --provenance=false -t show-img:latest .
```

Если k3s крутится на этой же машине:

```bash
docker save show-img:latest | sudo k3s ctr --namespace k8s.io images import -
```

Если k3s на отдельном сервере — заливаем в два шага:

```bash
docker save show-img:latest -o show-img.tar
scp show-img.tar user@k3s-node:/tmp/
ssh -t user@k3s-node "sudo k3s ctr --namespace k8s.io images import /tmp/show-img.tar && rm /tmp/show-img.tar"
```

Именно в два шага, а не одним пайпом: при `docker save ... | ssh ... "sudo ..."`
stdin занят тарболом, а `sudo` читает пароль из `/dev/tty`, которого в
неинтерактивной сессии нет — команда встанет на запросе авторизации. Выделять
терминал через `ssh -tt` тоже нельзя: tty транслирует CR/LF и портит бинарный
поток. Во втором шаге stdin свободен, `-t` даёт sudo спросить пароль.

Для неинтерактивной выкатки пароль sudo нужно убрать, а не передавать —
на узле через `visudo`:

```
user ALL=(root) NOPASSWD: /usr/local/bin/k3s ctr *
```

Тогда работает и однострочник с пайпом. Если доступен вход под root, sudo не
нужен вовсе:

```bash
docker save show-img:latest | ssh root@k3s-node "k3s ctr --namespace k8s.io images import -"
```

Проверить, что узел действительно видит образ:

```bash
sudo k3s crictl images | grep show-img
```

Узлов несколько — импортировать надо на каждый, иначе под, уехавший на другой
узел, снова не найдёт образ. На этом месте проще поднять реестр:

```bash
docker build --platform linux/amd64 --provenance=false -t registry.example.com/show-img:1.0.0 .
docker push registry.example.com/show-img:1.0.0
```

```yaml
# k8s/kustomization.yaml
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
