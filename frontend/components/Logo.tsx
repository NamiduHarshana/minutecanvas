/** The MinuteCanvas mark (four tiles, one dimmed) + word mark, from the design system. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <span className="mc-mark" style={{ width: size, height: size, padding: Math.round(size * 0.21) }} aria-hidden>
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

export function Logo({ small = false }: { small?: boolean }) {
  return (
    <span className={`mc-logo ${small ? "sm" : ""}`}>
      <Mark size={small ? 24 : 28} />
      <span className="mc-logo-text">MinuteCanvas</span>
    </span>
  );
}
