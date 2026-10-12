// Loader for the Twitch interactive embed script (window.Twitch.Player), shared by
// the VOD player and the Streams view's live channel player.

const EMBED_SCRIPT_URL = 'https://player.twitch.tv/js/embed/v1.js';

let embedPromise: Promise<void> | null = null;

// Appends the Twitch embed script once per page and resolves when
// window.Twitch.Player is available. A failed load clears the cache so a later
// mount can retry.
export function loadTwitchEmbed(): Promise<void> {
  if (window.Twitch?.Player) {
    return Promise.resolve();
  }
  if (embedPromise) {
    return embedPromise;
  }
  embedPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = EMBED_SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      if (window.Twitch?.Player) {
        resolve();
      } else {
        reject(new Error('Twitch embed script loaded without window.Twitch.Player'));
      }
    };
    script.onerror = () => {
      script.remove();
      reject(new Error('Failed to load the Twitch embed script'));
    };
    document.head.appendChild(script);
  }).catch((err: unknown) => {
    embedPromise = null;
    throw err;
  });
  return embedPromise;
}
