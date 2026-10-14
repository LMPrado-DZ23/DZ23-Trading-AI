/* Service Worker do PWA DZ23 Trading AI — push real + cache MÍNIMO.
 *
 * O QUE ESTE SW FAZ:
 *  - recebe o push do servidor 24/7 (VAPID) e mostra a notificação;
 *  - cacheia APENAS estáticos públicos da marca (ícones + manifesto).
 *
 * O QUE ELE NUNCA FAZ (regra do produto):
 *  - cachear /v1 (dados financeiros), páginas autenticadas ou qualquer
 *    resposta com credencial — nenhum segredo entra no Cache Storage;
 *  - operar na corretora: o toque na notificação só abre o painel.
 *
 * VERSIONAMENTO: trocar CACHE descarta os caches antigos no activate.
 */
const CACHE = "dz23-pwa-v1";
const ESTATICOS = [
  "/manifest.webmanifest",
  "/static/marca/dz23-simbolo-192.png",
  "/static/marca/dz23-simbolo-512.png",
  "/static/marca/dz23-simbolo-64.png",
  "/static/marca/favicon-32.png",
  "/static/marca/favicon-64.png",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches
      .open(CACHE)
      // Um ícone ausente não pode derrubar a instalação do app inteiro.
      .then((cache) =>
        Promise.all(ESTATICOS.map((url) => cache.add(url).catch(() => null)))
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((chaves) =>
        Promise.all(
          chaves.filter((k) => k !== CACHE).map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

// Só estáticos públicos passam pelo cache. Qualquer outra rota (inclusive
// /v1 e o painel autenticado) vai direto para a rede, sem cópia local.
self.addEventListener("fetch", (evento) => {
  const url = new URL(evento.request.url);
  if (evento.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }
  const publico =
    url.pathname === "/manifest.webmanifest" ||
    url.pathname.startsWith("/static/marca/");
  if (!publico) {
    return;
  }
  evento.respondWith(
    caches.match(evento.request).then((emCache) => emCache || fetch(evento.request))
  );
});

self.addEventListener("push", (evento) => {
  let dados = {};
  try {
    dados = evento.data ? evento.data.json() : {};
  } catch (erro) {
    dados = {};
  }
  const urgente = dados.severidade === "urgente" || dados.severidade === "emergencia";
  const titulo = dados.titulo || "DZ23 Trading AI";
  evento.waitUntil(
    self.registration.showNotification(titulo, {
      body: dados.corpo || "",
      // tag = id do alerta: reenvio do MESMO alerta substitui, não duplica.
      tag: dados.id || "dz23-alerta",
      renotify: false,
      requireInteraction: urgente,
      icon: "/static/marca/dz23-simbolo-192.png",
      badge: "/static/marca/dz23-simbolo-64.png",
      // Caminho + fragmento: o fragmento não viaja ao servidor e nenhum
      // dado financeiro entra na URL da notificação.
      data: { url: dados.url || "/app#notificacoes" },
    })
  );
});

self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  const url =
    (evento.notification.data && evento.notification.data.url) ||
    "/app#notificacoes";
  // Abre a área AUTENTICADA já existente (o login acontece lá).
  evento.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((janelas) => {
        for (const janela of janelas) {
          if (new URL(janela.url).origin === self.location.origin) {
            janela.focus();
            if (janela.navigate) {
              janela.navigate(url);
            }
            return undefined;
          }
        }
        return self.clients.openWindow(url);
      })
  );
});
