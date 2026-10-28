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
| `UVICORN_PORT` | Порт, который слушает процесс               | `8000`       |
| `UVICORN_HOST` | Адрес привязки                              | `0.0.0.0`    |
| `VAPID_PUBLIC_KEY`  | Публичный ключ Web Push                | —            |
| `VAPID_PRIVATE_KEY` | Приватный ключ Web Push (секрет)       | —            |
| `VAPID_SUBJECT`     | Контакт для push-сервиса (`mailto:` или `https:`) | `mailto:admin@example.com` |
| `WATCH_INTERVAL_SEC` | Как часто проверять появление новых картинок, сек | `10`  |
| `WATCH_LIMIT`  | Потолок на размер сверяемого списка id       | `1000`       |

Если `VAPID_PUBLIC_KEY` и `VAPID_PRIVATE_KEY` не заданы, сервис работает как
раньше, только без уведомлений: фоновая задача не запускается, а кнопка
подписки на странице скрыта.

Меняя порт в `k8s/deployment.yaml`, правьте и `containerPort`, и `UVICORN_PORT`:
`containerPort` — только метаданные, сам по себе он ничего не перенастраивает,
и при расхождении пробы получают `connection refused`.

## API

- `GET /api/images` → `[{id, image}]` — картинки, где `answer IS NULL OR answer = ''`
- `POST /api/images/{id}/answer` c телом `{"answer": "..."}` — записать ответ
- `GET /api/push/public-key` → `{"key": "..."}` — публичный VAPID-ключ
  (пустая строка означает, что уведомления не настроены)
- `POST /api/push/subscribe` с телом `PushSubscription.toJSON()` — оформить подписку
- `POST /api/push/unsubscribe` с телом `{"endpoint": "..."}` — отписаться

Поле `image` может содержать голый base64, `data:`-URI или URL — бэкенд приводит
значение к виду, пригодному для `<img src>`.

## Уведомления о новых картинках

На телефоне страницу можно подписать на push: когда в базе появляются картинки
без ответа, прилетает уведомление — даже если вкладка закрыта, а телефон лежит
в кармане.

Как это устроено:

- фоновая задача в `app.py` раз в `WATCH_INTERVAL_SEC` секунд забирает id
  неотвеченных картинок и сравнивает с предыдущим снимком. В таблице нет ни
  счётчика, ни времени создания (`id` — uuid), поэтому «новое» определяется
  именно диффом множества id;
- при первом проходе после старта снимок просто запоминается — на уже лежащие
  в базе картинки уведомление не приходит;
- рассылка идёт через Web Push (`pywebpush`) всем подписчикам из таблицы
  `push_subscription` — она создаётся автоматически при первой подписке;
- подписки, на которые push-сервис ответил `404`/`410`, удаляются сами:
  это значит, что подписку отозвали на устройстве.

### Включение

1. Сгенерировать пару ключей и положить в `.env`:

   ```bash
   uv run python gen_vapid_keys.py >> .env
   ```

   Ключи менять нельзя после того, как кто-то подписался: старые подписки
   станут недействительными, их придётся оформлять заново.

2. Открыть страницу и нажать «включить уведомления», разрешить показ
   уведомлений в браузере.

### HTTPS обязателен

Service Worker и Notification API работают только в защищённом контексте.
Исключение — `http://localhost` и `http://127.0.0.1`, они считаются защищёнными,
поэтому локально всё проверяется без возни с сертификатами. А вот по
`http://rtt-capcha.home` с телефона кнопка подписки просто не появится — это не
ошибка, а требование браузера. Как выдать домену сертификат — ниже в деплое.

### Чего ожидать на телефоне

- Android (Chrome/Firefox) — работает во вкладке, устанавливать на домашний
  экран не обязательно.
- Уведомление показывается, даже когда страница открыта и вы на неё смотрите:
  подписка оформляется с `userVisibleOnly: true`, браузер обязывает показать
  уведомление на каждый push. Все уведомления идут с одним тегом, поэтому
  заменяют друг друга, а не копятся пачкой.
- Интервал опроса базы (`WATCH_INTERVAL_SEC`) — это и есть задержка
  уведомления, по умолчанию до 10 секунд.

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

В том же файле лежит второй Secret — `show-img-push` с VAPID-ключами. Заполнить
его выводом генератора:

```bash
uv run python gen_vapid_keys.py
```

Оставить ключи пустыми можно: тогда сервис поднимется без уведомлений.

### 2a. Выдать домену сертификат

Без HTTPS уведомления не заработают — браузер не даёт Service Worker в
незащищённом контексте. `rtt-capcha.home` — домен внутренний, Let's Encrypt его
не подтвердит, поэтому нужен самоподписанный сертификат со своим CA.

```bash
# свой CA (один раз)
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout ca.key -out ca.crt -subj "/CN=home-ca"

# сертификат для домена
openssl req -newkey rsa:2048 -nodes -keyout tls.key -out tls.csr \
  -subj "/CN=rtt-capcha.home"
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out tls.crt -days 825 -sha256 \
  -extfile <(printf "subjectAltName=DNS:rtt-capcha.home")

kubectl -n rtt create secret tls show-img-tls --cert=tls.crt --key=tls.key
```

Секрет создаётся отдельной командой и в `kustomization.yaml` намеренно не
включён — чтобы ключ не лежал в репозитории.

На телефоне `ca.crt` нужно поставить в доверенные: Android → Настройки →
Безопасность → Шифрование и учётные данные → Установить сертификат → Сертификат
ЦС. Без этого Chrome посчитает соединение небезопасным и Service Worker не
зарегистрируется.

Если домен внешний и доступен из интернета — проще поставить cert-manager и
выпустить сертификат Let's Encrypt в тот же секрет `show-img-tls`, тогда возня с
доверием CA на телефоне не нужна.

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
  Нюансы два: два человека могут получить одну картинку и переписать ответ друг
  друга (последний выигрывает), а следилка за новыми картинками живёт в каждом
  поде своя — на N реплик придёт N одинаковых уведомлений. Если поднимаете
  реплики, уведомления стоит вынести в отдельный Deployment с `replicas: 1`.
- Ingress требует HTTPS и уводит http-заходы на https: без защищённого
  контекста браузер не даст включить уведомления.
