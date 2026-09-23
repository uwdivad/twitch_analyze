import type React from 'react';

type ButtonVariant = 'default' | 'primary' | 'accent' | 'ghost';

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: 'md' | 'sm';
  iconOnly?: boolean;
  // Disables the button and spins its icon (.btn.is-busy).
  busy?: boolean;
};

export function buttonClassName({
  variant = 'default',
  size = 'md',
  iconOnly = false,
  busy = false,
  className
}: Pick<ButtonProps, 'variant' | 'size' | 'iconOnly' | 'busy' | 'className'>): string {
  return [
    'btn',
    variant !== 'default' ? `btn-${variant}` : '',
    size === 'sm' ? 'btn-sm' : '',
    iconOnly ? 'btn-icon' : '',
    busy ? 'is-busy' : '',
    className ?? ''
  ]
    .filter(Boolean)
    .join(' ');
}

export function Button({
  variant,
  size,
  iconOnly,
  busy = false,
  className,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      aria-busy={busy || undefined}
      className={buttonClassName({ variant, size, iconOnly, busy, className })}
      disabled={disabled || busy}
      type={type}
    />
  );
}
