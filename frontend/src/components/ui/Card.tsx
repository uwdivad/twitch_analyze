import type React from 'react';

type CardProps = React.HTMLAttributes<HTMLElement> & {
  as?: 'section' | 'div' | 'article' | 'aside';
};

export function Card({ as: Tag = 'section', className, ...rest }: CardProps) {
  return <Tag {...rest} className={className ? `card ${className}` : 'card'} />;
}

type CardHeadingProps = {
  title: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  level?: 'h2' | 'h3';
};

// Title (15px) + muted meta line on the left, optional actions on the right.
export function CardHeading({ title, meta, actions, className, level: Heading = 'h2' }: CardHeadingProps) {
  return (
    <div className={className ? `card-heading ${className}` : 'card-heading'}>
      <div className="card-title">
        <Heading>{title}</Heading>
        {meta ? <span className="card-meta">{meta}</span> : null}
      </div>
      {actions ? <div className="card-actions">{actions}</div> : null}
    </div>
  );
}
