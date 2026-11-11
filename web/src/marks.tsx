export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 3 21.5 20h-19Z" />
      <path d="m8.8 15.4 2.4 2.2 4-4.4" />
    </svg>
  );
}

const shapes = {
  supported: <circle cx="12" cy="12" r="10.5" />,
  changed: <path d="M12 2 23 21.5H1Z" />,
  unverifiable: <path d="M12 1 23 12 12 23 1 12Z" />,
};

export function VerdictMark({ kind }: { kind: keyof typeof shapes }) {
  return (
    <svg className={`verdict-mark verdict-${kind}`} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {shapes[kind]}
    </svg>
  );
}
