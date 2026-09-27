import React from 'react';
import { RefreshCw, Search } from 'lucide-react';

import { HttpError, readApiKey, writeApiKey } from '../../api/client';
import { loadSettings, saveSettings } from '../../api/settings';
import { Segmented } from '../../components/ui/Segmented';
import { LIVE_UPDATE_INTERVAL_OPTIONS, VOLUME_WINDOW_OPTIONS } from '../../config';
import type { Theme } from '../../hooks/useTheme';
import type { Preferences, SetPreference } from '../../hooks/usePreferences';
import type { FeatureFlags, SettingField, SettingsResponse, SettingValue } from '../../types';
import { SettingRow, validateSetting } from './SettingRow';
import './settings.css';

const BROWSER_GROUP_ID = 'browser';
const BROWSER_KEYWORDS = ['this browser', 'theme', 'time window', 'update every', 'live updates', 'hide bots', 'api key'];

const THEME_OPTIONS: ReadonlyArray<{ value: Theme; label: string }> = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' }
];

type SettingsViewProps = {
  preferences: Preferences;
  onPreferenceChange: SetPreference;
  onResetPreferences: () => void;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  onFeaturesChange: (features: FeatureFlags) => void;
};

type Draft = Record<string, SettingValue>;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function matchesFilter(field: SettingField, filter: string): boolean {
  if (!filter) {
    return true;
  }
  const haystack = `${field.label} ${field.key} ${field.env_var} ${field.description}`.toLowerCase();
  return haystack.includes(filter);
}

// Numbers are edited as strings; send them as numbers once they validate.
function toPayloadValue(field: SettingField | undefined, value: SettingValue): SettingValue {
  if (value === null || !field || (field.kind !== 'int' && field.kind !== 'float')) {
    return value;
  }
  const number = Number(String(value).trim());
  return Number.isFinite(number) ? number : value;
}

export function SettingsView({
  preferences,
  onPreferenceChange,
  onResetPreferences,
  theme,
  onThemeChange,
  onFeaturesChange
}: SettingsViewProps) {
  const [data, setData] = React.useState<SettingsResponse | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState('');
  const [needsApiKey, setNeedsApiKey] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft>({});
  const [isSaving, setIsSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState('');
  const [notice, setNotice] = React.useState('');
  const [filter, setFilter] = React.useState('');
  const [apiKey, setApiKey] = React.useState(readApiKey);

  const fieldsByKey = React.useMemo(
    () => new Map((data?.settings ?? []).map((field) => [field.key, field])),
    [data]
  );

  const applyResponse = React.useCallback(
    (response: SettingsResponse) => {
      setData(response);
      onFeaturesChange(response.features);
    },
    [onFeaturesChange]
  );

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await loadSettings();
      applyResponse(response);
      setLoadError('');
      setNeedsApiKey(false);
    } catch (err) {
      setNeedsApiKey(err instanceof HttpError && err.status === 401);
      setLoadError(errorMessage(err, 'Failed to load settings'));
    } finally {
      setIsLoading(false);
    }
  }, [applyResponse]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const dirtyKeys = Object.keys(draft);
  const invalidKeys = dirtyKeys.filter((key) => {
    const field = fieldsByKey.get(key);
    return field ? validateSetting(field, draft[key]) !== '' : false;
  });

  // Warn before a reload/close drops unsaved server settings.
  React.useEffect(() => {
    if (dirtyKeys.length === 0) {
      return;
    }
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [dirtyKeys.length]);

  const handleChange = React.useCallback((field: SettingField, value: SettingValue) => {
    setNotice('');
    setDraft((current) => {
      const next = { ...current };
      const unchanged =
        value !== null &&
        (field.secret ? value === '' && !field.is_set : String(value) === String(field.value ?? ''));
      if (unchanged) {
        delete next[field.key];
      } else {
        next[field.key] = value;
      }
      return next;
    });
  }, []);

  const handleDiscard = React.useCallback((key: string) => {
    setDraft((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }, []);

  const handleSave = async () => {
    if (dirtyKeys.length === 0 || invalidKeys.length > 0) {
      return;
    }
    setIsSaving(true);
    setSaveError('');
    try {
      const values: Draft = {};
      for (const [key, value] of Object.entries(draft)) {
        values[key] = toPayloadValue(fieldsByKey.get(key), value);
      }
      const response = await saveSettings(values);
      applyResponse(response);
      setDraft({});
      const count = response.changed.length;
      setNotice(
        [
          count === 0 ? 'Saved. No values changed.' : `Saved ${count} ${count === 1 ? 'setting' : 'settings'}.`,
          response.ingestion_restarted ? 'Twitch ingestion restarted.' : ''
        ]
          .filter(Boolean)
          .join(' ')
      );
    } catch (err) {
      if (err instanceof HttpError && err.status === 401) {
        setNeedsApiKey(true);
      }
      setSaveError(errorMessage(err, 'Failed to save settings'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleApiKeySave = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    writeApiKey(apiKey.trim());
    setApiKey(apiKey.trim());
    void refresh();
  };

  const normalizedFilter = filter.trim().toLowerCase();
  const groups = React.useMemo(() => {
    if (!data) {
      return [];
    }
    return data.groups
      .map((group) => ({
        ...group,
        fields: data.settings.filter((field) => field.group === group.id && matchesFilter(field, normalizedFilter))
      }))
      .filter((group) => group.fields.length > 0);
  }, [data, normalizedFilter]);

  const showBrowserGroup =
    !normalizedFilter || BROWSER_KEYWORDS.some((keyword) => keyword.includes(normalizedFilter));
  const pendingRestart = (data?.settings ?? []).filter((field) => field.pending_restart);

  const scrollToGroup = (id: string) => {
    document.getElementById(`settings-group-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <section className="settings-view">
      <header className="page-header settings-header">
        <h1>
          Settings <span className="muted">configuration and feature flags</span>
        </h1>
        <div className="settings-header-tools">
          <label className="settings-filter">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label="Filter settings"
              className="field"
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter settings"
              type="search"
              value={filter}
            />
          </label>
          <button
            className={`btn btn-ghost${isLoading ? ' is-busy' : ''}`}
            disabled={isLoading}
            onClick={() => void refresh()}
            type="button"
          >
            <RefreshCw size={16} />
            Reload
          </button>
        </div>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          <ul>
            {showBrowserGroup ? (
              <li>
                <button onClick={() => scrollToGroup(BROWSER_GROUP_ID)} type="button">
                  This browser
                </button>
              </li>
            ) : null}
            {groups.map((group) => {
              const dirty = group.fields.some((field) => field.key in draft);
              return (
                <li key={group.id}>
                  <button onClick={() => scrollToGroup(group.id)} type="button">
                    {group.label}
                    {dirty ? <span className="settings-nav-dot" aria-label="unsaved changes" /> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="settings-main">
          {pendingRestart.length > 0 ? (
            <div className="settings-banner" role="status">
              Saved but not active until a restart: {pendingRestart.map((field) => field.label).join(', ')}.
            </div>
          ) : null}
          {notice ? (
            <div className="settings-banner is-success" role="status">
              {notice}
            </div>
          ) : null}

          {showBrowserGroup ? (
            <section className="card settings-group" id={`settings-group-${BROWSER_GROUP_ID}`}>
              <div className="settings-group-heading">
                <h2>This browser</h2>
                <p>Dashboard preferences, saved in this browser only. Changes apply immediately.</p>
              </div>
              <div className="settings-row">
                <div className="settings-row-text">
                  <div className="settings-row-title">
                    <span>Theme</span>
                  </div>
                </div>
                <div className="settings-row-control">
                  <Segmented ariaLabel="Theme" options={THEME_OPTIONS} value={theme} onChange={onThemeChange} />
                </div>
              </div>
              <div className="settings-row">
                <div className="settings-row-text">
                  <div className="settings-row-title">
                    <label htmlFor="pref-volume-window">Time window</label>
                  </div>
                  <p className="settings-row-description">Range of the volume charts on the Live dashboard.</p>
                </div>
                <div className="settings-row-control">
                  <select
                    className="field"
                    id="pref-volume-window"
                    onChange={(event) => onPreferenceChange('volumeWindowMinutes', Number(event.target.value))}
                    value={preferences.volumeWindowMinutes}
                  >
                    {VOLUME_WINDOW_OPTIONS.map((option) => (
                      <option key={option.minutes} value={option.minutes}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="settings-row">
                <div className="settings-row-text">
                  <div className="settings-row-title">
                    <label htmlFor="pref-update-interval">Update every</label>
                  </div>
                  <p className="settings-row-description">How often queued live messages are drawn.</p>
                </div>
                <div className="settings-row-control">
                  <select
                    className="field"
                    id="pref-update-interval"
                    onChange={(event) => onPreferenceChange('liveUpdateIntervalMs', Number(event.target.value))}
                    value={preferences.liveUpdateIntervalMs}
                  >
                    {LIVE_UPDATE_INTERVAL_OPTIONS.map((option) => (
                      <option key={option.milliseconds} value={option.milliseconds}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <PreferenceSwitch
                checked={preferences.showLiveFeed}
                description="Stream messages over SSE and show the live feed."
                id="pref-live-feed"
                label="Live updates"
                onChange={(checked) => onPreferenceChange('showLiveFeed', checked)}
              />
              <PreferenceSwitch
                checked={preferences.hideBots}
                description="Hide known chat bots from the live feed and top chatters."
                id="pref-hide-bots"
                label="Hide bots"
                onChange={(checked) => onPreferenceChange('hideBots', checked)}
              />
              <div className="settings-row">
                <div className="settings-row-text">
                  <div className="settings-row-title">
                    <label htmlFor="pref-api-key">API key</label>
                  </div>
                  <p className="settings-row-description">
                    The backend's API_AUTH_TOKEN, if one is set. Sent as X-API-Key with every request.
                  </p>
                </div>
                <form className="settings-row-control settings-secret" onSubmit={handleApiKeySave}>
                  <input
                    autoComplete="off"
                    className="field"
                    id="pref-api-key"
                    onChange={(event) => setApiKey(event.target.value)}
                    placeholder="Not set"
                    spellCheck={false}
                    type="password"
                    value={apiKey}
                  />
                  <button className="btn btn-ghost btn-sm" type="submit">
                    Use key
                  </button>
                </form>
              </div>
              <div className="settings-group-footer">
                <button className="btn btn-ghost btn-sm" onClick={onResetPreferences} type="button">
                  Reset preferences
                </button>
              </div>
            </section>
          ) : null}

          {loadError ? (
            <div className="alert" role="alert">
              {needsApiKey
                ? 'The backend requires an API key to read settings. Enter it under This browser → API key.'
                : `Server settings unavailable: ${loadError}`}
            </div>
          ) : null}

          {!data && isLoading ? <div className="card settings-group skeleton settings-skeleton" /> : null}

          {groups.map((group) => (
            <section className="card settings-group" id={`settings-group-${group.id}`} key={group.id}>
              <div className="settings-group-heading">
                <h2>{group.label}</h2>
                {group.description ? <p>{group.description}</p> : null}
              </div>
              {group.fields.map((field) => (
                <SettingRow
                  draft={draft[field.key]}
                  field={field}
                  hasDraft={field.key in draft}
                  key={field.key}
                  onChange={handleChange}
                  onDiscard={handleDiscard}
                />
              ))}
            </section>
          ))}

          {data && groups.length === 0 && !showBrowserGroup ? (
            <div className="empty">No settings match “{filter}”.</div>
          ) : null}

          {data ? (
            <p className="settings-footnote muted">
              Saved values override .env and live in <code>{data.overrides_file}</code> on the backend.
            </p>
          ) : null}
        </div>
      </div>

      {dirtyKeys.length > 0 || saveError ? (
        <div className="settings-savebar" role="region" aria-label="Unsaved changes">
          <span className="settings-savebar-text">
            {saveError ? (
              <span className="settings-error">{saveError}</span>
            ) : invalidKeys.length > 0 ? (
              <span className="settings-error">Fix {invalidKeys.length} invalid value{invalidKeys.length === 1 ? '' : 's'} to save</span>
            ) : (
              `${dirtyKeys.length} unsaved change${dirtyKeys.length === 1 ? '' : 's'}`
            )}
          </span>
          <div className="settings-savebar-actions">
            <button
              className="btn btn-ghost"
              disabled={isSaving}
              onClick={() => {
                setDraft({});
                setSaveError('');
              }}
              type="button"
            >
              Discard
            </button>
            <button
              className={`btn btn-accent${isSaving ? ' is-busy' : ''}`}
              disabled={isSaving || dirtyKeys.length === 0 || invalidKeys.length > 0}
              onClick={() => void handleSave()}
              type="button"
            >
              {isSaving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

type PreferenceSwitchProps = {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
};

function PreferenceSwitch({ id, label, description, checked, onChange }: PreferenceSwitchProps) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <div className="settings-row-title">
          <label htmlFor={id}>{label}</label>
        </div>
        <p className="settings-row-description">{description}</p>
      </div>
      <div className="settings-row-control">
        <div className="switch-wrapper settings-switch">
          <label className="switch">
            <input checked={checked} id={id} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
            <span className="slider"></span>
          </label>
          <span className="switch-state">{checked ? 'On' : 'Off'}</span>
        </div>
      </div>
    </div>
  );
}
