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
 * VERSIONAMENTO: trocar CACHE descarta no activate apenas as versões
 * antigas desta família de caches ("dz23-pwa-*") — nunca caches de
 * outros apps no mesmo domínio nem o marcador de push pendente.
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

// V9-PWA-06: a purga é ESCOPADA à família de caches deste app
// ("dz23-pwa-*"). Um cache de outro app no mesmo domínio e o marcador
// persistente da reinscrição (dz23-push-pendente) sobrevivem à ativação;
// só versões antigas deste Service Worker são descartadas.
self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((chaves) =>
        Promise.all(
          chaves
            .filter((k) => k.startsWith("dz23-pwa-") && k !== CACHE)
            .map((k) => caches.delete(k))
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

// V9-PWA-05: o push service pode TROCAR a inscrição (expiração do
// endpoint) sem a página aberta. Sem tratamento, o destino novo nunca
// chega ao servidor e o aparelho para de receber em silêncio. O SW
// re-inscreve com a MESMA chave VAPID (a do servidor) e deixa o destino
// novo num marcador persistente — o painel autenticado (único que tem
// token) registra no servidor na primeira abertura; nada é apresentado
// como ativo antes disso.
const MARCADOR_PENDENTE = "dz23-push-pendente";
self.addEventListener("pushsubscriptionchange", (evento) => {
  evento.waitUntil(
    (async () => {
      let nova = evento.newSubscription || null;
      const antiga = evento.oldSubscription || null;
      if (
        !nova &&
        antiga &&
        antiga.options &&
        antiga.options.applicationServerKey
      ) {
        try {
          nova = await self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: antiga.options.applicationServerKey,
          });
        } catch (erro) {
          nova = null;
        }
      }
      if (!nova) {
        return;
      }
      const json = nova.toJSON ? nova.toJSON() : null;
      if (!json || !json.endpoint) {
        return;
      }
      try {
        const cache = await caches.open(MARCADOR_PENDENTE);
        await cache.put(
          "/pendente",
          new Response(
            JSON.stringify({ endpoint: json.endpoint, keys: json.keys || {} }),
            { headers: { "content-type": "application/json" } }
          )
        );
      } catch (erro) {
        // Marcador é melhor-esforço; sem ele o painel reconcilia pelo
        // estado real (o aparelho aparece sem envios, nunca como ativo).
      }
      const janelas = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const janela of janelas) {
        janela.postMessage({ tipo: "push-reinscrito" });
      }
    })()
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
