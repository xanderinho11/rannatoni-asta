const CACHE='rannatoni-shell-v10';
const ASSETS=[
  '/static/style.css',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/manifest.webmanifest'
];

self.addEventListener('install',event=>{
  event.waitUntil(
    caches.open(CACHE)
      .then(cache=>cache.addAll(ASSETS))
      .then(()=>self.skipWaiting())
  );
});

self.addEventListener('activate',event=>{
  event.waitUntil(
    caches.keys()
      .then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))
      .then(()=>self.clients.claim())
  );
});

// Online-first: durante l'asta vogliamo sempre HTML/CSS aggiornati. La cache
// serve solo come fallback per le risorse statiche se la rete cade per un attimo.
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET') return;
  const url=new URL(event.request.url);
  if(url.pathname.startsWith('/api/') || url.pathname==='/ws') return;
  event.respondWith(
    fetch(event.request).catch(()=>caches.match(event.request))
  );
});


self.addEventListener('push',event=>{
  let data={};
  try{data=event.data?event.data.json():{}}catch(e){data={body:event.data?event.data.text():''}}
  event.waitUntil((async()=>{
    const windows=await clients.matchAll({type:'window',includeUncontrolled:true});
    if(windows.some(client=>client.visibilityState==='visible')) return;
    await self.registration.showNotification(data.title||'Rannatoni',{
      body:data.body||'',
      icon:'/static/icon-192.png',
      badge:'/static/icon-192.png',
      tag:data.tag||'rannatoni',
      renotify:true,
      data:{url:data.url||'/auction'}
    });
  })());
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  event.waitUntil(
    clients.matchAll({type:'window',includeUncontrolled:true}).then(async list=>{
      const url=(event.notification.data&&event.notification.data.url)||'/auction';
      for(const client of list){
        if('navigate' in client) await client.navigate(url);
        if('focus' in client) return client.focus();
      }
      return clients.openWindow(url);
    })
  );
});
