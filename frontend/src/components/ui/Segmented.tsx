type SegmentedOption<T extends string> = {
  value: T;
  label: string;
};

type SegmentedProps<T extends string> = {
  options: ReadonlyArray<SegmentedOption<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
};

export function Segmented<T extends string>({ options, value, onChange, ariaLabel, className }: SegmentedProps<T>) {
  return (
    <div aria-label={ariaLabel} className={className ? `segmented ${className}` : 'segmented'} role="group">
      {options.map((option) => {
        const isActive = option.value === value;
        return (
          <button
            key={option.value}
            aria-pressed={isActive}
            className={isActive ? 'is-active' : undefined}
            onClick={() => onChange(option.value)}
            type="button"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
