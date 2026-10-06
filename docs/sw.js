// Routes every request made by the embedded browser frame through the
// WebRTC tunnel owned by client.html.
//
// Proxied pages live under <scope>__p/<scheme>/<host[:port]>/<path>, so
// relative URLs inside them resolve back into the proxy automatically.

const SCOPE = new URL(self.registration.scope);
const PREFIX = SCOPE.pathname + "__p/";

const REQUEST_SKIP = new Set([
  "cookie", "host", "origin", "referer",
  "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user",
  "upgrade-insecure-requests",
]);
const RESPONSE_SKIP = new Set([
  "content-length",
  "content-security-policy",
  "content-security-policy-report-only",
  "cross-origin-embedder-policy",
  "cross-origin-opener-policy",
  "cross-origin-resource-policy",
  "strict-transport-security",
  "x-frame-options",
]);
const NULL_BODY_STATUS = new Set([101, 103, 204, 205, 304]);
const REDIRECT_STATUS = new Set([301, 302, 303, 307, 308]);

// Remote origin of the most recently loaded document. Used for root-relative
// requests ("/style.css") whose client URL does not reveal the target.
let lastBase = null;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

// document.cookie writes from proxied pages, sent to the agent's cookie jar
// with the next request (the page usually navigates right after writing).
const pendingCookieWrites = [];

self.addEventListener("message", event => {
  if (event.data?.type === "base") lastBase = event.data.base;
  if (event.data?.type === "cookie") pendingCookieWrites.push([event.data.url, event.data.cookie]);
});

function toTarget(localUrl) {
  if (!localUrl) return null;
  const u = new URL(localUrl);
  if (u.origin !== SCOPE.origin || !u.pathname.startsWith(PREFIX)) return null;
  const m = u.pathname.slice(PREFIX.length).match(/^(https?)\/([^/]+)(\/.*)?$/);
  if (!m) return null;
  return `${m[1]}://${m[2]}${m[3] || "/"}${u.search}`;
}

function toLocal(targetUrl) {
  const u = new URL(targetUrl);
  return `${SCOPE.origin}${PREFIX}${u.protocol.slice(0, -1)}/${u.host}${u.pathname}${u.search}${u.hash}`;
}

// Files of the tunnel app itself, served by the proxy's own server.
const APP_FILES = new Set(["", "client.html", "sw.js", "favicon.ico"]);

function isAppFile(url) {
  return url.pathname.startsWith(SCOPE.pathname)
    && APP_FILES.has(url.pathname.slice(SCOPE.pathname.length));
}

function rebase(url, base) {
  return new URL(url.pathname + url.search, base).href;
}

// lastBase is lost whenever the browser stops the idle worker, so fall back
// to asking client.html which remote page its frame currently shows.
async function currentBase() {
  if (lastBase) return lastBase;
  const app = await findApp();
  if (!app) return null;
  const { port1, port2 } = new MessageChannel();
  const reply = new Promise(resolve => {
    port1.onmessage = ({ data }) => resolve(data);
    setTimeout(() => resolve(null), 1000);
  });
  app.postMessage({ type: "base?" }, [port2]);
  lastBase = (await reply) || null;
  return lastBase;
}

async function clientDocument(clientId) {
  const client = clientId && await self.clients.get(clientId);
  if (!client) return null;
  return toTarget(client.url) || (client.frameType === "nested" ? await currentBase() : null);
}

// Returns { target, source }: the remote URL to fetch and the remote URL of
// the document that initiated the request (null when typed in the address
// bar), or null if the request does not belong to the embedded browser.
// request.referrer is not enough for the source: for cross-origin requests
// the browser trims it to the proxy's origin.
async function resolveTarget(request, clientId) {
  const url = new URL(request.url);
  const sameOrigin = url.origin === SCOPE.origin;
  const direct = toTarget(request.url);

  if (request.mode === "navigate") {
    const source = toTarget(request.referrer);
    if (direct) return { target: direct, source };
    // Only frame navigations belong to the embedded browser.
    if (!sameOrigin || !["iframe", "frame"].includes(request.destination)) return null;
    const base = source || await currentBase();
    return base ? { target: rebase(url, base), source } : null;
  }

  let source = await clientDocument(clientId);
  if (direct) return { target: direct, source: source || toTarget(request.referrer) };
  // Requests that outlive their document (keepalive fetches, pings) arrive
  // without a client; root-relative ones still belong to the last site.
  if (!source && sameOrigin && !isAppFile(url)) source = await currentBase();
  if (!source) return null;
  return { target: sameOrigin ? rebase(url, source) : request.url, source };
}

async function findApp() {
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return windows.find(c => c.frameType === "top-level" && !toTarget(c.url)) || null;
}

function errorPage(status, message) {
  const html = `<!doctype html><meta charset="utf-8"><title>Proxy error</title>
<body style="font:15px system-ui;padding:40px;color:#333">
<h2>Cannot load page</h2><p>${message.replace(/[<&]/g, c => (c === "<" ? "&lt;" : "&amp;"))}</p></body>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function injectedScript(cookies) {
  const initialCookies = JSON.stringify(cookies).replace(/</g, "\\u003c");
  return `<script>(() => {
  const PREFIX = ${JSON.stringify(PREFIX)};
  const toLocal = href => {
    const u = new URL(href);
    return location.origin + PREFIX + u.protocol.slice(0, -1) + "/" + u.host + u.pathname + u.search + u.hash;
  };
  const m = location.pathname.startsWith(PREFIX)
    && location.pathname.slice(PREFIX.length).match(/^(https?)\\/([^/]+)/);
  const REMOTE = m ? m[1] + "://" + m[2] : null;
  // Root-relative URLs ("/search") resolve against the proxy's origin and
  // would escape the proxy; map them back onto the remote site.
  const fix = raw => {
    let u;
    try { u = new URL(raw, document.baseURI); } catch { return null; }
    if (!/^https?:$/.test(u.protocol)) return null;
    if (u.origin === location.origin) {
      if (u.pathname.startsWith(PREFIX) || !REMOTE) return null;
      u = new URL(u.pathname + u.search + u.hash, REMOTE);
    }
    return toLocal(u.href);
  };
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented) return;
    if (a.target && a.target !== "_self") a.target = "_self";
    const local = fix(a.href);
    if (local) { e.preventDefault(); location.href = local; }
  }, true);
  document.addEventListener("submit", e => {
    const f = e.target;
    f.target = "_self";
    const local = fix(f.action);
    if (local) f.action = local;
  }, true);
  window.open = url => { if (url) location.href = fix(url) || url; return null; };
  // Beacons sent while the page unloads reach the service worker after the
  // document is gone, so the URL itself must name the target site.
  const sendBeacon = navigator.sendBeacon.bind(navigator);
  navigator.sendBeacon = (url, data) => sendBeacon(fix(url) || url, data);

  // document.cookie would store cookies for the proxy's origin, where the
  // target site never sees them. Keep the site's script-visible cookies here
  // (seeded by the agent) and forward every write to the agent's cookie jar.
  const PAGE = REMOTE && REMOTE + (location.pathname.slice(PREFIX.length + m[0].length) || "/") + location.search;
  const jar = new Map();
  for (const pair of ${initialCookies}.split("; ")) {
    const i = pair.indexOf("=");
    if (i > 0) jar.set(pair.slice(0, i), pair.slice(i + 1));
  }
  Object.defineProperty(Document.prototype, "cookie", {
    configurable: true,
    get() { return [...jar].map(([k, v]) => k + "=" + v).join("; "); },
    set(raw) {
      const text = String(raw);
      const pair = text.split(";")[0];
      const i = pair.indexOf("=");
      const name = i > 0 ? pair.slice(0, i).trim() : "";
      if (!name || !PAGE) return;
      const attrs = text.slice(pair.length).toLowerCase();
      const maxAge = /;\\s*max-age=\\s*(-?\\d+)/.exec(attrs);
      const expires = /;\\s*expires=([^;]+)/.exec(attrs);
      const expired = maxAge ? Number(maxAge[1]) <= 0 : expires ? Date.parse(expires[1]) <= Date.now() : false;
      if (expired) jar.delete(name);
      else jar.set(name, pair.slice(i + 1).trim());
      navigator.serviceWorker && navigator.serviceWorker.controller
        && navigator.serviceWorker.controller.postMessage({ type: "cookie", url: PAGE, cookie: text });
    },
  });
})();</script>`;
}

// Works on raw bytes so non-UTF-8 pages are passed through untouched.
async function injectIntoHtml(stream, cookies) {
  const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
  let head = "";
  for (let i = 0; i < Math.min(bytes.length, 65536); i += 8192) {
    head += String.fromCharCode(...bytes.subarray(i, Math.min(i + 8192, bytes.length, 65536)));
  }
  const m = /<head[^>]*>/i.exec(head) || /<!doctype[^>]*>/i.exec(head);
  const at = m ? m.index + m[0].length : 0;
  const script = new TextEncoder().encode(injectedScript(cookies));
  const out = new Uint8Array(bytes.length + script.length);
  out.set(bytes.subarray(0, at), 0);
  out.set(script, at);
  out.set(bytes.subarray(at), at + script.length);
  return out;
}

async function buildResponse(head, stream, request, target) {
  const headers = new Headers();
  let location = null;
  for (const [name, value] of head.headers) {
    const lower = name.toLowerCase();
    if (RESPONSE_SKIP.has(lower)) continue;
    if (lower === "location") {
      location = value;
      continue;
    }
    try { headers.append(name, value); } catch { /* invalid header value */ }
  }

  if (location && REDIRECT_STATUS.has(head.status)) {
    stream.cancel();
    return Response.redirect(toLocal(new URL(location, target).href), head.status);
  }

  let body = NULL_BODY_STATUS.has(head.status) || request.method === "HEAD" ? null : stream;
  const isHtml = (headers.get("content-type") || "").includes("text/html");
  if (body && isHtml && request.mode === "navigate") {
    body = await injectIntoHtml(stream, head.cookies || "");
  }
  return new Response(body, {
    status: head.status,
    statusText: head.statusText,
    headers,
  });
}

// Approximates the registrable domain without the public suffix list.
function siteOf(url) {
  return url.hostname.split(".").slice(-2).join(".");
}

// Rebuild the headers the real browser would send to the target site, as if
// the page were loaded top-level from its own origin instead of the proxy.
function upstreamHeaders(request, target, source) {
  const headers = [...request.headers].filter(([name]) => !REQUEST_SKIP.has(name));
  const to = new URL(target);
  const from = source && new URL(source);

  let site = "none";
  if (from) {
    if (from.origin === to.origin) site = "same-origin";
    else if (siteOf(from) === siteOf(to)) site = "same-site";
    else site = "cross-site";
  }

  if (from) {
    headers.push(["referer", site === "same-origin" ? from.href : from.origin + "/"]);
    const sendsOrigin = !["GET", "HEAD"].includes(request.method)
      || (request.mode === "cors" && site !== "same-origin");
    if (sendsOrigin) headers.push(["origin", from.origin]);
  }

  const navigate = request.mode === "navigate";
  headers.push(
    ["sec-fetch-site", site],
    ["sec-fetch-mode", request.mode],
    ["sec-fetch-dest", navigate ? "document" : request.destination || "empty"],
  );
  if (navigate) {
    headers.push(["sec-fetch-user", "?1"], ["upgrade-insecure-requests", "1"]);
  }
  return headers;
}

async function proxy(request, { target, source }) {
  const app = await findApp();
  if (!app) return errorPage(502, "The tunnel page (client.html) is not open.");

  const body = ["GET", "HEAD"].includes(request.method) ? null : await request.arrayBuffer();
  const headers = upstreamHeaders(request, target, source);

  const { port1, port2 } = new MessageChannel();
  return new Promise(resolve => {
    let controller;
    let resolved = false;
    const stream = new ReadableStream({
      start(c) { controller = c; },
      cancel() {
        port1.postMessage({ type: "cancel" });
        port1.close();
      },
    });

    port1.onmessage = ({ data }) => {
      switch (data.type) {
        case "head":
          resolved = true;
          resolve(buildResponse(data, stream, request, target)
            .catch(e => errorPage(502, String(e))));
          break;
        case "chunk":
          controller.enqueue(new Uint8Array(data.data));
          break;
        case "end":
          controller.close();
          port1.close();
          break;
        case "error":
          if (resolved) controller.error(new Error(data.message));
          else resolve(errorPage(502, data.message));
          port1.close();
          break;
      }
    };

    app.postMessage(
      {
        type: "fetch",
        method: request.method,
        url: target,
        headers,
        body,
        cookieWrites: pendingCookieWrites.splice(0),
      },
      body ? [port2, body] : [port2],
    );
  });
}

async function handle(event) {
  const request = event.request;
  const resolved = await resolveTarget(request, event.clientId);
  if (!resolved) return fetch(request);
  if (request.mode === "navigate" && !toTarget(request.url)) {
    // Keep frame URLs inside the prefix so relative links keep working;
    // 307 preserves the method and body of form submissions.
    return Response.redirect(toLocal(resolved.target), 307);
  }
  return proxy(request, resolved);
}

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  const proxied = toTarget(request.url);
  if (!proxied && request.mode === "navigate" && request.destination === "document") return;
  const orphan = !event.clientId && request.mode !== "navigate";
  if (!proxied && orphan && (url.origin !== SCOPE.origin || isAppFile(url))) return;
  event.respondWith(handle(event));
});
