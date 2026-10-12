type IframePlayerProps = {
  src: string;
  title: string;
};

// Plain iframe embed for platforms without a JS player API (Kick, YouTube).
// Parents key this on the stream identity so a status refresh never reloads it.
export function IframePlayer({ src, title }: IframePlayerProps) {
  return (
    <div className="streams-player">
      <iframe
        className="streams-player-frame"
        src={src}
        title={title}
        allow="autoplay; fullscreen; picture-in-picture; encrypted-media"
        allowFullScreen
        referrerPolicy="strict-origin-when-cross-origin"
        loading="lazy"
      />
    </div>
  );
}

export function kickEmbedUrl(slug: string): string {
  return `https://player.kick.com/${encodeURIComponent(slug)}?autoplay=true&muted=true`;
}

export function youtubeEmbedUrl(videoId: string): string {
  return `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=1&mute=1&playsinline=1`;
}
