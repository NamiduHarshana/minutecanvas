import { Logo } from "../Logo";
import { SITE } from "@/core/site";

export function SiteHeader() {
  return (
    <header className="s-head">
      <div className="s-wrap s-head-in">
        <a href="/" className="s-home" aria-label="MinuteCanvas home">
          <Logo small />
        </a>
        <nav className="s-nav" aria-label="Main">
          <a href="/#how" className="s-nav-hide">
            How it works
          </a>
          <a href="/#faq" className="s-nav-hide">
            FAQ
          </a>
          <a href="/meetings">My meetings</a>
          <a href="/meet" className="s-btn s-btn-ink s-btn-sm">
            Start a meeting
          </a>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="s-foot">
      <div className="s-wrap s-foot-in">
        <div className="s-foot-brand">
          <Logo small />
          <p>A live visual notetaker for meetings, built with Meta Muse models for the Meta Global AI Developer Hackathon 2026.</p>
        </div>
        <ul className="s-foot-links">
          <li>
            <a href="/meet">Start a meeting</a>
          </li>
          <li>
            <a href="/meetings">My meetings</a>
          </li>
          <li>
            <a href="/privacy">Privacy policy</a>
          </li>
          <li>
            <a href={SITE.github} target="_blank" rel="noopener">
              Source code on GitHub (MIT)
            </a>
          </li>
          {SITE.contactEmail && (
            <li>
              <a href={`mailto:${SITE.contactEmail}`}>{SITE.contactEmail}</a>
            </li>
          )}
        </ul>
      </div>
    </footer>
  );
}
