import React from 'react';

type StreamSearchFormProps = {
  initialName: string;
  isBusy: boolean;
  onSearch: (name: string) => void;
};

export function StreamSearchForm({ initialName, isBusy, onSearch }: StreamSearchFormProps) {
  const [name, setName] = React.useState(initialName);
  const inputId = React.useId();
  const canSubmit = name.trim().length > 0 && !isBusy;

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (canSubmit) {
      onSearch(name.trim());
    }
  };

  return (
    <form className="streams-form" onSubmit={handleSubmit}>
      <label className="streams-visually-hidden" htmlFor={inputId}>
        Streamer account name
      </label>
      <input
        id={inputId}
        className="field streams-form-input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder="account name, e.g. lofigirl"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <button
        type="submit"
        className={`btn btn-accent streams-form-submit${isBusy ? ' is-busy' : ''}`}
        disabled={!canSubmit}
        aria-busy={isBusy}
      >
        {isBusy ? 'Checking…' : 'Check'}
      </button>
    </form>
  );
}
