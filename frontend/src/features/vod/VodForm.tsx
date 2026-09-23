import React from 'react';

type VodFormProps = {
  isStarting: boolean;
  onAnalyze: (video: string, force: boolean) => void;
};

export function VodForm({ isStarting, onAnalyze }: VodFormProps) {
  const [video, setVideo] = React.useState('');
  const [force, setForce] = React.useState(false);
  const inputId = React.useId();
  const canSubmit = video.trim().length > 0 && !isStarting;

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (canSubmit) {
      onAnalyze(video.trim(), force);
    }
  };

  return (
    <form className="vod-form" onSubmit={handleSubmit}>
      <label className="vod-visually-hidden" htmlFor={inputId}>
        Twitch VOD URL or video id
      </label>
      <input
        id={inputId}
        className="field vod-form-input"
        type="text"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        placeholder="https://www.twitch.tv/videos/1234567890 or 1234567890"
        value={video}
        onChange={(event) => setVideo(event.target.value)}
      />
      <label className="vod-form-check">
        <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
        Force re-import
      </label>
      <button
        type="submit"
        className={`btn btn-accent vod-form-submit${isStarting ? ' is-busy' : ''}`}
        disabled={!canSubmit}
        aria-busy={isStarting}
      >
        {isStarting ? 'Starting…' : 'Analyze'}
      </button>
    </form>
  );
}
