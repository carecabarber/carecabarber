// ══════════════════════════════════════════════════════════════
//  CarecaBarber — Service Worker
//  Estratégias:
//    • Static assets  → Cache First (CSS, JS, imagens, fontes)
//    • HTML pages     → Network First + fallback para cache → /offline
//    • /api/*         → Network Only (dados em tempo real)
//    • POST/mutations → Network Only (nunca em cache)
// ══════════════════════════════════════════════════════════════

const APP_VERSION    = 'v14';          // Incrementar sempre que o conteúdo mudar
const STATIC_CACHE   = `cb-static-${APP_VERSION}`;
const DYNAMIC_CACHE  = `cb-dynamic-${APP_VERSION}`;
const OFFLINE_URL    = '/offline';

// Ficheiros a pré-carregar na instalação
const STATIC_PRECACHE = [
    '/static/style.css',
    '/static/app.js',
    '/static/favicon.svg',
    '/static/favicon.png',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/static/logo.svg',
    '/offline',
];

// ── Install: pré-cache dos assets estáticos ──────────────────
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then(cache => cache.addAll(STATIC_PRECACHE))
            .catch(err => console.warn('[SW] Pré-cache falhou (alguns assets podem estar em falta):', err))
            // skipWaiting() SEMPRE — mesmo se addAll falhar (assets em falta não devem bloquear a activação)
            .finally(() => self.skipWaiting())
    );
});

// ── Activate: limpar caches de versões antigas ───────────────
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys
                    .filter(k => k !== STATIC_CACHE && k !== DYNAMIC_CACHE)
                    .map(k => {
                        console.log('[SW] A apagar cache antigo:', k);
                        return caches.delete(k);
                    })
            ))
            .then(() => self.clients.claim())  // controla tabs já abertas
    );
});

// ── Fetch: routing de pedidos ────────────────────────────────
self.addEventListener('fetch', event => {
    const req = event.request;
    const url = new URL(req.url);

    // 1. Ignorar pedidos não-GET, cross-origin, e extensões de browser
    if (req.method !== 'GET') return;
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith('/static/logos/')) return; // logos dinâmicos — não cachear

    // 2. API, rotas de autenticação, páginas pessoais do cliente, e páginas de staff → Network Only
    //    (dados em tempo real ou dados pessoais — nunca devem vir de cache)
    //    As páginas de staff contêm dados privados da barbearia e não devem ser guardadas
    //    no DYNAMIC_CACHE (risco de fuga de dados entre utilizadores no mesmo dispositivo).
    const STAFF_PAGES = ['/', '/historico', '/estatisticas', '/perfil', '/configuracoes',
                         '/barbeiros', '/servicos', '/walkin', '/novo', '/root'];
    if (
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/webauthn/') ||
        url.pathname.startsWith('/cliente/') ||  // páginas pessoais do cliente (dados privados)
        url.pathname.startsWith('/ag/') ||        // páginas de ação via QR (dados da marcação)
        url.pathname.startsWith('/mesa/') ||      // páginas de mesa (dados privados)
        url.pathname === '/sw.js' ||
        url.pathname === '/manifest.json' ||
        STAFF_PAGES.includes(url.pathname) ||     // páginas de staff (dados privados da barbearia)
        url.pathname.startsWith('/bloquear') ||
        url.pathname.startsWith('/cancelar/') ||
        url.pathname.startsWith('/avaliar/')
    ) {
        return; // deixa o browser tratar normalmente
    }

    // 3. Assets estáticos → Cache First
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(cacheFirst(req));
        return;
    }

    // 4. Páginas HTML → Network First com fallback
    event.respondWith(networkFirstWithOffline(req));
});

// ── Estratégia: Cache First ──────────────────────────────────
async function cacheFirst(req) {
    const cached = await caches.match(req);
    if (cached) return cached;

    try {
        const response = await fetch(req);
        if (response.ok) {
            const cache = await caches.open(STATIC_CACHE);
            cache.put(req, response.clone());
        }
        return response;
    } catch {
        // Asset offline e não em cache — retorna resposta vazia
        return new Response('', { status: 503, statusText: 'Offline' });
    }
}

// ── Estratégia: Network First + fallback offline ─────────────
const DYNAMIC_CACHE_MAX = 20;   // máximo de páginas no cache dinâmico (FIFO)

async function _trimDynamicCache() {
    const cache   = await caches.open(DYNAMIC_CACHE);
    const keys    = await cache.keys();
    if (keys.length > DYNAMIC_CACHE_MAX) {
        // Apagar as mais antigas (as primeiras na lista, que foram inseridas primeiro)
        const toDelete = keys.slice(0, keys.length - DYNAMIC_CACHE_MAX);
        await Promise.all(toDelete.map(k => cache.delete(k)));
    }
}

async function networkFirstWithOffline(req) {
    try {
        const response = await fetch(req);

        // Guardar em cache dinâmico apenas respostas HTML válidas
        if (response.ok && response.headers.get('content-type')?.includes('text/html')) {
            const cache = await caches.open(DYNAMIC_CACHE);
            cache.put(req, response.clone());
            _trimDynamicCache();   // limpar entradas excedentes (fire-and-forget)
        }

        return response;
    } catch {
        // Sem rede — tentar cache
        const cached = await caches.match(req);
        if (cached) return cached;

        // Sem rede e sem cache → página offline
        const offlinePage = await caches.match(OFFLINE_URL);
        return offlinePage || new Response(
            '<h1>Sem ligação</h1><p>Verifica a tua ligação à internet.</p>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
    }
}

// ── Notificações push (para futuras notificações nativas) ────
self.addEventListener('push', event => {
    if (!event.data) return;
    let data = {};
    try { data = event.data.json(); } catch { data = { title: 'CarecaBarber', body: event.data.text() }; }

    const options = {
        body:    data.corpo   || data.body || 'Nova notificação',
        icon:    '/static/icon-192.png',
        badge:   '/static/icon-192.png',
        vibrate: [100, 50, 100],
        data:    { url: data.url || '/' },
        actions: data.actions || [],
    };

    event.waitUntil(
        self.registration.showNotification(data.titulo || data.title || 'CarecaBarber', options)
    );
});

// Clicar numa notificação abre a app na URL correcta
self.addEventListener('notificationclick', event => {
    event.notification.close();
    // Validar URL: apenas caminhos relativos (começam com /) ou mesma origem — previne open redirect
    const rawUrl = event.notification.data?.url;
    let target = '/';
    if (typeof rawUrl === 'string') {
        try {
            const parsed = new URL(rawUrl, self.location.origin);
            if (parsed.origin === self.location.origin) {
                target = parsed.pathname + parsed.search + parsed.hash;
            }
        } catch {
            // URL inválida — usar fallback '/'
        }
    }
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clients => {
                const existing = clients.find(c => new URL(c.url).origin === self.location.origin);
                if (existing) {
                    existing.focus();
                    existing.navigate(target);
                } else {
                    self.clients.openWindow(target);
                }
            })
    );
});

// ── Mensagens do cliente (força update manual) ───────────────
self.addEventListener('message', event => {
    if (event.data?.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
