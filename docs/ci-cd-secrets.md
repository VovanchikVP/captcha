# Секреты для CD: подключение к кластеру

Workflow `CD` (`.github/workflows/cd.yml`) собирает образ, заходит в закрытый
кластер k3s через WireGuard и раскатывает оверлей `k8s/`. Чтобы это работало,
в репозитории нужно завести несколько секретов. Здесь — как получить каждый.

Образ публикуется в GHCR под именем репозитория; отдельный токен для этого не
нужен, хватает `GITHUB_TOKEN` с правом `packages: write`.

## Что нужно завести

| Имя | Тип | Назначение |
|---|---|---|
| `WG_CONFIG` | секрет | конфиг WireGuard-пира для раннера, в base64 |
| `KUBE_API` | секрет | адрес API кластера в сети WG, например `10.8.0.2:6443` |
| `KUBE_CONFIG` | секрет | kubeconfig кластера в base64 |
| `DATABASE_URL` | секрет | строка подключения к Postgres → секрет `show-img-db` |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | секреты | ключи Web Push → секрет `show-img-push` |
| `VAPID_SUBJECT` | секрет | контакт для push-сервиса, `mailto:` или `https:` |
| `production` | environment | можно повесить ручное подтверждение выкатки |

Заводятся в **Settings → Secrets and variables → Actions**. Job `deploy`
объявлен с `environment: production`, поэтому секреты читаются и с уровня
репозитория, и с уровня окружения — подойдёт любой.

---

# WG_CONFIG

Кластер не виден из интернета, поэтому раннер GitHub сам поднимается пиром в
сеть WG и уже оттуда ходит в API кластера.

Если пир для раннера уже есть (например, его использует другой репозиторий) и
файл конфига сохранился — можно положить тот же конфиг и перейти к шагу 7.
**Но лучше завести отдельный пир:** при одновременном запуске двух выкаток
сервер держит для одного ключа один endpoint, и соединения будут перебивать
друг друга.

## 1. Сгенерировать ключи пира

Можно на рабочей машине (`brew install wireguard-tools`) или на сервере.
`umask 077` обязателен — иначе приватный ключ ляжет с правами на чтение всем:

```bash
umask 077
wg genkey | tee show-img-runner.key | wg pubkey > show-img-runner.pub
```

## 2. Выбрать свободный адрес в сети WG

На сервере:

```bash
sudo wg show wg0 allowed-ips          # занятые адреса
grep -E "Address|AllowedIPs" /etc/wireguard/wg0.conf
```

Берём любой свободный `/32` из той же сети — например `10.8.0.51/32`.

## 3. Добавить пир на сервере

```bash
# применяется сразу, без перезапуска интерфейса
sudo wg set wg0 peer "$(cat show-img-runner.pub)" allowed-ips 10.8.0.51/32

# и в конфиг, чтобы пережил перезагрузку
sudo tee -a /etc/wireguard/wg0.conf > /dev/null <<EOF

[Peer]
# github actions runner: show-img
PublicKey = $(cat show-img-runner.pub)
AllowedIPs = 10.8.0.51/32
EOF

sudo wg show wg0    # пир должен появиться в списке
```

`Endpoint` для этого пира на сервере не нужен: раннер подключается сам,
входящих соединений к нему нет.

## 4. Собрать данные для конфига раннера

```bash
sudo wg show wg0 public-key     # → PublicKey сервера
sudo wg show wg0 listen-port    # → порт, обычно 51820
```

Плюс внешний адрес сервера (домен или белый IP) и адрес API кластера — тот же,
что пойдёт в `KUBE_API`.

## 5. Написать конфиг раннера

Файл `wg-runner.conf`, в git не коммитим:

```ini
[Interface]
PrivateKey = <содержимое show-img-runner.key>
Address = 10.8.0.51/32

[Peer]
PublicKey = <публичный ключ сервера из шага 4>
Endpoint = vpn.example.com:51820
# что заворачивается в туннель
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
```

Три вещи, на которых это ломается чаще всего:

- **Не писать `DNS = ...`.** `wg-quick` на этой строке зовёт `resolvconf`,
  которого на `ubuntu-latest` нет, и шаг падает с `resolvconf: command not
  found`. Все адреса в `cd.yml` числовые, DNS не нужен.
- **Не писать `AllowedIPs = 0.0.0.0/0`.** Это уведёт в туннель весь трафик
  раннера, включая связь с самим GitHub, — job зависнет.
- Если нода k3s не сама является пиром WG, а живёт в локальной сети за сервером
  (например `192.168.1.132`), допишите её подсеть:
  `AllowedIPs = 10.8.0.0/24, 192.168.1.0/24` — и убедитесь, что на сервере
  включён `net.ipv4.ip_forward=1` и есть правила forward/NAT. Подсети подов и
  сервисов (`10.42.0.0/16`, `10.43.0.0/16`) для `kubectl` не нужны — они
  понадобятся, только если ходить в поды напрямую.

## 6. Проверить туннель локально

```bash
sudo wg-quick up ./wg-runner.conf
sudo wg show wg0                      # должен появиться latest handshake
nc -z -w 5 10.8.0.2 6443 && echo "API виден"
sudo wg-quick down ./wg-runner.conf
```

Handshake пустой — не сходятся ключи или `Endpoint`. Handshake есть, а порт не
отвечает — дело в маршрутах или форвардинге на сервере, конфиг раннера тут ни
при чём.

## 7. Закодировать и положить в секрет

```bash
base64 -i wg-runner.conf | tr -d '\n' | pbcopy
```

`tr -d '\n'` — чтобы значение было одной строкой: GitHub переносы переживёт, но
однострочник не даёт случайно обрезать хвост при вставке.

Секрет `WG_CONFIG` ← из буфера.

## 8. Прибрать ключи

`show-img-runner.key` и `wg-runner.conf` — это приватный ключ пира. Либо в
менеджер паролей, либо `rm`: пир всегда пересоздаётся шагами 1–3, перевыпуск
ключа ничего в кластере не ломает.

---

# KUBE_API

Адрес API кластера, видимый **из сети WG**, в виде `host:port` — например
`10.8.0.2:6443`. Не `127.0.0.1`: для раннера это его собственный localhost.

Workflow использует его только для быстрой проверки доступности (`nc -z`) до
того, как за дело возьмётся `kubectl`, — чтобы в логе было видно, отвалился
туннель или что-то другое.

---

# KUBE_CONFIG

Это kubeconfig ноды k3s, перенацеленный на WG-адрес и закодированный в base64.

## 1. Забрать kubeconfig с ноды

На k3s он лежит в `/etc/rancher/k3s/k3s.yaml` и читается только от root:

```bash
ssh user@k3s-node "sudo cat /etc/rancher/k3s/k3s.yaml" > k3s.yaml
```

Если вход под root — без `sudo`. Писать `sudo cat ... > файл` на самой ноде не
надо: перенаправление выполняет shell обычного пользователя, и получится
`Permission denied`.

Брать `~/.kube/config` с рабочей машины можно, только если он самодостаточный
(шаг 3) — чаще там несколько контекстов и внешние хелперы, на раннере это не
заведётся.

## 2. Подменить адрес сервера

В файле стоит `server: https://127.0.0.1:6443`. Нужен адрес API в сети WG, тот
же, что в `KUBE_API`:

```bash
sed -i '' 's#server: https://127.0.0.1:6443#server: https://10.8.0.2:6443#' k3s.yaml
grep server: k3s.yaml
```

На macOS у `sed -i` обязателен аргумент `''`, иначе он съест следующий флаг как
суффикс бэкапа. На Linux — просто `sed -i`.

## 3. Убедиться, что конфиг самодостаточный

```bash
grep -E "certificate-authority-data|client-certificate-data|client-key-data|exec:|command:" k3s.yaml
```

Должны быть три строки `*-data` и ни одного `exec:`/`command:`. `*-data` — это
встроенные base64-сертификаты, они уедут вместе с файлом. `exec:` означает, что
конфиг зовёт внешний бинарник (`aws`, `gke-gcloud-auth-plugin` и подобные),
которого на раннере нет. У k3s по умолчанию всё встроено.

## 4. Проверить через туннель

```bash
sudo wg-quick up ./wg-runner.conf
KUBECONFIG=./k3s.yaml kubectl get nodes
KUBECONFIG=./k3s.yaml kubectl -n rtt get deploy
sudo wg-quick down ./wg-runner.conf
```

Ошибка `x509: certificate is valid for ..., not 10.8.0.2` чинится не в
kubeconfig, а на ноде — см. «Сертификат API должен знать WG-адрес» ниже.

## 5. Закодировать и положить в секрет

```bash
base64 -i k3s.yaml | tr -d '\n' | pbcopy
```

Секрет `KUBE_CONFIG` ← из буфера.

base64 здесь не ради секретности, а чтобы многострочный YAML с отступами
гарантированно пережил подстановку в shell — workflow раскодирует его обратно
в `~/.kube/config`.

---

# Сертификат API должен знать WG-адрес

По умолчанию сертификат k3s выписан на адреса ноды в её локальной сети. При
обращении по WG-адресу `kubectl` падает так:

```
tls: failed to verify certificate: x509: certificate is valid for
10.43.0.1, 127.0.0.1, 192.168.1.132, ::1, not 10.8.0.2
```

Лечится добавлением WG-адреса в SAN на ноде:

```yaml
# /etc/rancher/k3s/config.yaml
tls-san:
  - 10.8.0.2
```

```bash
sudo systemctl restart k3s
```

k3s перевыпускает сертификат при изменении списка SAN. Если адрес не появился —
удалите серверный сертификат и перезапустите ещё раз, он создастся заново:

```bash
sudo rm /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.crt \
        /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.key
sudo systemctl restart k3s
```

Проверка:

```bash
openssl s_client -connect 10.8.0.2:6443 </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"
```

CA при этом не меняется, поэтому `KUBE_CONFIG` переделывать не нужно.
Перезапуск k3s ненадолго гасит только API — поды продолжают работать.

---

# Секреты приложения

Эти четыре значения не уходят в кластер напрямую: на шаге «Сформировать
k8s/secret.yaml» раннер собирает из них файл `k8s/secret.yaml` — ровно тот,
что при ручной выкатке делается из `k8s/secret.example.yaml`, — и применяет его
через `kubectl apply -f`. В репозитории файла нет и не будет: он в `.gitignore`
и удаляется с раннера сразу после выкатки.

Файл собирается не шаблоном, а через `kubectl create secret --dry-run=client
-o yaml`: kubectl кодирует значения в base64 и кладёт их в `data`, поэтому
пароль с `:`, `#` или переносом строки не ломает документ. В лог при этом
ничего не печатается — маскировка GitHub на base64 не распространяется.

`DATABASE_URL` — строка подключения к Postgres, та же, что в
`k8s/secret.example.yaml`. Попадает в секрет `show-img-db` в namespace `rtt`.
Если секрет в GitHub пуст, выкатка останавливается с ошибкой: без строки
подключения под всё равно не стартует.

`VAPID_PUBLIC_KEY` и `VAPID_PRIVATE_KEY` — ключи Web Push, генерируются
командой:

```bash
uv run python -m src.services.vapid
```

Оставить их незаданными можно: тогда сервис поднимется без уведомлений, кнопка
подписки на странице будет скрыта.

`VAPID_SUBJECT` — контакт для push-сервиса, обязан быть `mailto:` или
`https:`. Если не задан, подставляется `mailto:admin@example.com`.

Второй документ в `secret.yaml` — `show-img-push` — появляется, только если
`VAPID_PRIVATE_KEY` непустой. Менять ключи у живого сервиса нельзя: все
выданные подписки станут мёртвыми, и браузеры на телефонах придётся
переподписывать вручную. Поэтому при пустых ключах секрет в файл не
записывается и то, что уже лежит в кластере, остаётся нетронутым. Если же его
там ещё нет, workflow создаёт пустой: `show-img-push` примонтирован через
`envFrom` без `optional`, и без него под не запустится вовсе.

---

# Проверка

Actions → CD → **Run workflow**. По шагам видно, где остановилось:

| Шаг | Что должно быть в логе |
|---|---|
| Подключиться к сети WireGuard | `latest handshake: N seconds ago` |
| Проверить, что API кластера виден | `кластер доступен: 10.8.0.2:6443` |
| Подложить kubeconfig | блок `serverVersion` от `kubectl version` |
| Сформировать k8s/secret.yaml | `show-img-db`, `show-img-push` в списке имён |
| Дождаться выкатки | `deployment "show-img" successfully rolled out` |

При падении выкатки шаг «Логи при сбое» кладёт в вывод последние 100 строк
пода, а «Состояние после выкатки» — `kubectl get pods`.

# Обслуживание

**Срок жизни kubeconfig.** Клиентский сертификат в `k3s.yaml` выписан на год.
k3s перевыпускает его при рестарте, когда до конца остаётся меньше 90 дней, но
копия в секрете от этого не обновляется — примерно раз в год CD начнёт падать
на `certificate has expired`, и шаги `KUBE_CONFIG` нужно повторить.

**Права.** `k3s.yaml` — это cluster-admin. Для домашнего кластера нормально.
Урезать до отдельного ServiceAccount в namespace `rtt` можно, но учтите:
`kustomize build k8s` применяет в том числе `namespace.yaml`, а это ресурс
кластерного уровня — одной Role в namespace не хватит, понадобится ещё
ClusterRole на `namespaces`. Либо так, либо выкинуть `namespace.yaml` из
оверлея и создавать namespace руками один раз.

**Доступ к образу.** Пакет в GHCR по умолчанию приватный, и узел k3s его не
скачает. Проще всего сделать пакет публичным в настройках на GitHub. Если образ
должен остаться закрытым — завести pull-секрет и сослаться на него в Deployment:

```bash
kubectl -n rtt create secret docker-registry ghcr \
  --docker-server=ghcr.io \
  --docker-username=<логин> \
  --docker-password=<PAT с правом read:packages>
```

```yaml
# k8s/deployment.yaml, в spec.template.spec
imagePullSecrets:
  - name: ghcr
```

# Если не хочется пускать CI в VPN

Поставьте self-hosted runner на машину внутри сети (сервер WG или нода
кластера) — он сам ходит к GitHub наружу, входящие соединения не нужны. Тогда
в `cd.yml` у job `deploy`:

- `runs-on: self-hosted` вместо `ubuntu-latest`;
- шаги «Подключиться к сети WireGuard», «Проверить, что API кластера виден» и
  «Отключиться от WireGuard» удалить;
- секреты `WG_CONFIG` и `KUBE_API` перестают быть нужны.

Job `build` при этом остаётся на `ubuntu-latest`.
