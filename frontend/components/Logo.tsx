export function Logo({ small = false }: { small?: boolean }) {
  const s = small ? 26 : 34;
  return (
    <span className={`logo ${small ? "logo-sm" : ""}`}>
      <svg width={s} height={s} viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#4f46e5" />
        <rect x="6" y="7" width="9" height="8" rx="2" fill="#fff" />
        <rect x="17" y="7" width="9" height="12" rx="2" fill="#c7d2fe" />
        <rect x="6" y="17" width="9" height="8" rx="2" fill="#c7d2fe" />
        <rect x="17" y="21" width="9" height="4" rx="2" fill="#fff" />
      </svg>
      <span className="logo-text">MinuteCanvas</span>
    </span>
  );
}
