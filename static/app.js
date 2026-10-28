const POLL_MS = 5000;
const grid = document.getElementById('grid');
const statusEl = document.getElementById('status');
const cards = new Map();          // id -> элемент карточки
const resolved = new Set();       // id, уже отправленные на сервер

function makeCard(item) {
  const card = document.createElement('div');
  card.className = 'card';

  const img = document.createElement('img');
  img.src = item.image;
  img.alt = 'captcha';

  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'Ответ + Enter';
  input.autocomplete = 'off';
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submit(item.id, card, input);
  });

  card.append(img, input);
  return card;
}

async function submit(id, card, input) {
  const answer = input.value.trim();
  if (!answer) return;

  card.classList.add('saving');
  card.classList.remove('error');
  try {
    const res = await fetch(`/api/images/${id}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer }),
    });
    if (!res.ok) throw new Error(await res.text());
    resolved.add(id);
    removeCard(id);
  } catch (err) {
    card.classList.remove('saving');
    card.classList.add('error');
    console.error(err);
  }
}

function removeCard(id) {
  const card = cards.get(id);
  if (card) { card.remove(); cards.delete(id); }
  render();
}

function showSpinner() {
  statusEl.innerHTML = '<span class="spinner" role="status" aria-label="ожидание данных"></span>';
}

function showOffline() {
  statusEl.innerHTML =
    '<svg class="offline" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'role="img" aria-label="нет связи с сервером">' +
    '<title>нет связи с сервером</title>' +
    '<path d="M3 3 L21 21"/>' +
    '<path d="M2 8.5a16 16 0 0 1 5.5-3.2"/>' +
    '<path d="M11.5 4.1A16 16 0 0 1 22 8.5"/>' +
    '<path d="M5 12.5a11 11 0 0 1 3-1.9"/>' +
    '<path d="M19 12.5a11 11 0 0 0-5.6-2.3"/>' +
    '<path d="M8.5 16.3a6 6 0 0 1 2-1.1"/>' +
    '<path d="M15.5 16.3a6 6 0 0 0-1.6-1"/>' +
    '<path d="M12 20h.01"/>' +
    '</svg>';
}

function render() {
  if (!cards.size) {
    // данных пока нет — показываем ожидание, опрос продолжается
    showSpinner();
  } else {
    statusEl.textContent = `картинок: ${cards.size}`;
  }
}

async function poll() {
  try {
    const items = await (await fetch('/api/images')).json();
    const seen = new Set();

    for (const item of items) {
      if (resolved.has(item.id)) continue;   // ответ уже ушёл, ждём пока исчезнет из выдачи
      seen.add(item.id);
      if (!cards.has(item.id)) {
        const card = makeCard(item);
        cards.set(item.id, card);
        grid.append(card);
      }
    }

    // убираем то, что получило ответ (в том числе из другой вкладки)
    for (const [id, card] of cards) {
      if (!seen.has(id) && card !== document.activeElement?.closest('.card')) {
        card.remove();
        cards.delete(id);
      }
    }
    // сервер больше не отдаёт эти id — можно забыть про них
    for (const id of resolved) {
      if (!items.some((i) => i.id === id)) resolved.delete(id);
    }
    render();
  } catch (err) {
    showOffline();
    console.error(err);
  }
}

poll();
setInterval(poll, POLL_MS);

// --- уведомления о новых картинках -----------------------------------------

const pushBtn = document.getElementById('push-toggle');

// Push работает только в защищённом контексте: по http кнопку прячем,
// иначе пользователь жмёт её и ничего не происходит
const pushSupported =
  window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window;

function urlBase64ToUint8Array(base64) {
  const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const raw = atob(padded);
  return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
}

function setPushButton(state, text) {
  pushBtn.dataset.state = state;
  pushBtn.textContent = text;
  pushBtn.disabled = state === 'busy' || state === 'blocked';
}

async function getRegistration() {
  return navigator.serviceWorker.register('/sw.js');
}

async function initPush() {
  if (!pushSupported) {
    // страница открыта по http (или совсем старый браузер) — молча прячем кнопку
    pushBtn.hidden = true;
    return;
  }

  const { key } = await (await fetch('/api/push/public-key')).json();
  if (!key) {
    pushBtn.hidden = true;   // сервер без VAPID-ключей, подписываться некуда
    return;
  }
  pushBtn.dataset.key = key;

  if (Notification.permission === 'denied') {
    setPushButton('blocked', 'уведомления запрещены в браузере');
    return;
  }

  const reg = await getRegistration();
  const sub = await reg.pushManager.getSubscription();
  setPushButton(sub ? 'on' : 'off', sub ? 'уведомления включены' : 'включить уведомления');
}

async function enablePush() {
  setPushButton('busy', 'подключение…');

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    setPushButton(
      permission === 'denied' ? 'blocked' : 'off',
      permission === 'denied' ? 'уведомления запрещены в браузере' : 'включить уведомления',
    );
    return;
  }

  const reg = await getRegistration();
  // ждём активации: сразу после register() pushManager ещё может быть не готов
  await navigator.serviceWorker.ready;

  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(pushBtn.dataset.key),
  });

  const res = await fetch('/api/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sub.toJSON()),
  });
  if (!res.ok) throw new Error(await res.text());

  setPushButton('on', 'уведомления включены');
}

async function disablePush() {
  setPushButton('busy', 'отключение…');

  const reg = await getRegistration();
  const sub = await reg.pushManager.getSubscription();
  if (sub) {
    await fetch('/api/push/unsubscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    await sub.unsubscribe();
  }

  setPushButton('off', 'включить уведомления');
}

pushBtn.addEventListener('click', async () => {
  try {
    if (pushBtn.dataset.state === 'on') {
      await disablePush();
    } else {
      await enablePush();
    }
  } catch (err) {
    setPushButton('off', 'не удалось включить');
    console.error(err);
  }
});

initPush().catch((err) => {
  pushBtn.hidden = true;
  console.error(err);
});
