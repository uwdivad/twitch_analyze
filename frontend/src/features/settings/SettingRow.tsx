import React from 'react';
import { RotateCcw } from 'lucide-react';

import type { SettingApply, SettingField, SettingValue } from '../../types';

const APPLY_LABELS: Record<SettingApply, { text: string; title: string }> = {
  live: { text: 'Instant', title: 'Applies to the next request or job' },
  ingestion: { text: 'Restarts ingestion', title: 'Twitch ingestion reconnects when you save' },
  restart: { text: 'API restart', title: 'Read at API startup; restart the backend to apply' },
  worker: { text: 'Worker restart', title: 'Read by a standalone worker; restart it to apply' }
};

export function formatSettingValue(value: SettingValue): string {
  if (value === null || value === '') {
    return 'empty';
  }
  if (typeof value === 'boolean') {
    return value ? 'on' : 'off';
  }
  return String(value);
}

// Returns a message when a drafted value can't be saved; the backend validates again.
export function validateSetting(field: SettingField, value: SettingValue): string {
  if (value === null || (field.kind !== 'int' && field.kind !== 'float')) {
    return '';
  }
  const text = String(value).trim();
  const number = Number(text);
  if (text === '' || !Number.isFinite(number)) {
    return 'Enter a number';
  }
  if (field.kind === 'int' && !Number.isInteger(number)) {
    return 'Enter a whole number';
  }
  if (field.minimum !== null && number < field.minimum) {
    return `Minimum ${field.minimum}`;
  }
  if (field.maximum !== null && number > field.maximum) {
    return `Maximum ${field.maximum}`;
  }
  return '';
}

type SettingRowProps = {
  field: SettingField;
  // Present when the row has an unsaved change; null means "clear the override".
  draft: SettingValue | undefined;
  hasDraft: boolean;
  onChange: (field: SettingField, value: SettingValue) => void;
  onDiscard: (key: string) => void;
};

export const SettingRow = React.memo(function SettingRow({ field, draft, hasDraft, onChange, onDiscard }: SettingRowProps) {
  const controlId = React.useId();
  const descriptionId = `${controlId}-description`;
  const resetting = hasDraft && draft === null;
  // What the control shows: the drafted value, the .env value a pending reset
  // returns to, or the saved value.
  const shown: SettingValue = resetting ? field.env_value : hasDraft ? draft ?? null : field.value;
  const error = hasDraft ? validateSetting(field, draft ?? null) : '';
  const apply = APPLY_LABELS[field.apply];

  let control: React.ReactNode;
  if (field.kind === 'bool') {
    const checked = Boolean(shown);
    control = (
      <div className="switch-wrapper settings-switch">
        <label className="switch">
          <input
            id={controlId}
            aria-describedby={descriptionId}
            checked={checked}
            onChange={(event) => onChange(field, event.target.checked)}
            type="checkbox"
          />
          <span className="slider"></span>
        </label>
        <span className="switch-state">{checked ? 'On' : 'Off'}</span>
      </div>
    );
  } else if (field.kind === 'select') {
    const current = String(shown ?? '');
    const options = field.options.includes(current) || current === '' ? field.options : [current, ...field.options];
    control = (
      <select
        id={controlId}
        aria-describedby={descriptionId}
        className="field"
        value={current}
        onChange={(event) => onChange(field, event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  } else if (field.kind === 'secret') {
    control = (
      <div className="settings-secret">
        <input
          id={controlId}
          aria-describedby={descriptionId}
          autoComplete="off"
          className="field"
          onChange={(event) => onChange(field, event.target.value)}
          placeholder={resetting ? 'Reverts to .env value' : field.is_set ? 'Saved · type to replace' : 'Not set'}
          spellCheck={false}
          type="password"
          value={hasDraft && draft !== null ? String(draft) : ''}
        />
        {field.is_set && !hasDraft ? (
          <button className="btn btn-ghost btn-sm" onClick={() => onChange(field, '')} type="button">
            Clear
          </button>
        ) : null}
      </div>
    );
  } else {
    const numeric = field.kind === 'int' || field.kind === 'float';
    control = (
      <input
        id={controlId}
        aria-describedby={descriptionId}
        aria-invalid={error ? true : undefined}
        autoComplete="off"
        className="field"
        inputMode={numeric ? 'decimal' : undefined}
        max={field.maximum ?? undefined}
        min={field.minimum ?? undefined}
        onChange={(event) => onChange(field, event.target.value)}
        placeholder={field.kind === 'list' ? 'comma,separated' : undefined}
        spellCheck={false}
        step={field.kind === 'float' ? 'any' : numeric ? 1 : undefined}
        type={numeric ? 'number' : 'text'}
        value={String(shown ?? '')}
      />
    );
  }

  let source: React.ReactNode = null;
  if (resetting) {
    source = <span className="settings-source is-pending">Reverts to .env on save</span>;
  } else if (hasDraft) {
    source = <span className="settings-source is-pending">Unsaved</span>;
  } else if (field.overridden) {
    source = (
      <span className="settings-source">
        Saved here
        {field.secret ? '' : ` · .env: ${formatSettingValue(field.env_value)}`}
      </span>
    );
  } else if (!field.secret && field.default !== null && field.value !== field.default) {
    source = <span className="settings-source">From .env · default {formatSettingValue(field.default)}</span>;
  }

  return (
    <div className={`settings-row${hasDraft ? ' is-dirty' : ''}`} data-setting={field.key}>
      <div className="settings-row-text">
        <div className="settings-row-title">
          <label htmlFor={controlId}>{field.label}</label>
          <span className={`pill pill-sm settings-apply is-${field.apply}`} title={apply.title}>
            {apply.text}
          </span>
          {field.pending_restart ? (
            <span className="pill pill-sm settings-apply is-warning" title="Saved, but not active until a restart">
              Restart pending
            </span>
          ) : null}
        </div>
        {field.description ? (
          <p className="settings-row-description" id={descriptionId}>
            {field.description}
          </p>
        ) : null}
        <div className="settings-row-meta">
          <code>{field.env_var}</code>
          {source}
        </div>
      </div>
      <div className="settings-row-control">
        {control}
        <div className="settings-row-actions">
          {error ? (
            <span className="settings-error" role="alert">
              {error}
            </span>
          ) : null}
          {hasDraft ? (
            <button className="btn btn-ghost btn-sm" onClick={() => onDiscard(field.key)} type="button">
              Undo
            </button>
          ) : field.overridden ? (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => onChange(field, null)}
              title="Remove the saved value and use the .env value"
              type="button"
            >
              <RotateCcw size={14} />
              Use .env
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
});
