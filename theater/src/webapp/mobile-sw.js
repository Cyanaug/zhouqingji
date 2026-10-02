/* 昼青集 Android 掌中册：程序外壳离线，私人快照由 app.js 保存到 IndexedDB。 */
"use strict";

const SHELL_CACHE = "zhouqingji-pocket-v2";
const SHELL = [
  "./mobile.html",
  "./style.css",
  "./app.js",
  "./mobile.webmanifest",
  "./mobile-shell.json",
  "./favicon.png",
  "./apple-touch-icon.png",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith("zhouqingji-pocket-") && key !== SHELL_CACHE)
      .map(key => caches.delete(key))
  )));
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  const fallback = event.request.mode === "navigate" ? "./mobile.html" : event.request;
  const fresh = fetch(event.request).then(response => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(SHELL_CACHE).then(cache => cache.put(fallback, copy));
    }
    return response;
  });
  event.waitUntil(fresh.then(() => null).catch(() => null));
  // 离线优先：已有壳立即显示；联网时在后台换成新壳，下次启动即生效。
  event.respondWith(caches.match(fallback).then(cached => {
    return cached || fresh;
  }));
});
