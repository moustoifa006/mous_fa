// Petites fonctions utilitaires partagées entre les pages.
// La logique spécifique à chaque page (AJAX, CRUD) est définie
// directement dans le bloc {% block scripts %} de chaque template.

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

function arrayBufferToBase64(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

async function activerNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    alert("Les notifications push ne sont pas supportées sur ce navigateur/appareil.");
    return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const keyRes = await fetch('/api/push/vapid-public-key');
    const { key } = await keyRes.json();
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });
    }
    await fetch('/api/push/subscribe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        endpoint: sub.endpoint,
        keys: {
          p256dh: arrayBufferToBase64(sub.getKey('p256dh')),
          auth: arrayBufferToBase64(sub.getKey('auth')),
        },
      }),
    });
    document.querySelectorAll('.notif-banner').forEach((el) => el.remove());
    const statutEl = document.getElementById('notif-statut');
    if (statutEl) statutEl.textContent = '✅ Les notifications sont activées sur cet appareil.';
    alert('🔔 Notifications activées !');
  } catch (e) {
    alert("Impossible d'activer les notifications sur cet appareil.");
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // Bannière discrète proposant d'activer les notifications, une seule fois par session.
  if ('Notification' in window && Notification.permission === 'default' && !sessionStorage.getItem('notifBannerVue')) {
    const banner = document.createElement('div');
    banner.className = 'notif-banner';
    banner.innerHTML =
      '🔔 Active les notifications pour être averti·e même quand l\'appli est fermée. ' +
      '<button onclick="activerNotifications(); this.parentElement.remove();">Activer</button> ' +
      '<button onclick="this.parentElement.remove();">Plus tard</button>';
    document.body.prepend(banner);
    sessionStorage.setItem('notifBannerVue', '1');
  }
});
