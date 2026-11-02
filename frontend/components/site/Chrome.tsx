import { Mark } from "../Logo";
import { SITE, demoReplayHref } from "@/core/site";

export function SiteHeader() {
  return (
    <header className="snav-bar">
      <nav className="snav" aria-label="Main">
        <a href="/" className="mc-brand" aria-label="MinuteCanvas home">
          <Mark />
          MinuteCanvas
        </a>
        <a href="/#how" className="hide-s">
          How it works
        </a>
        <a href="/#features" className="hide-s">
          Features
        </a>
        <a href="/#privacy" className="hide-s">
          Privacy
        </a>
        <a href="/#faq" className="hide-s">
          FAQ
        </a>
        <span className="grow" />
        <a href="/dashboard">Dashboard</a>
        <a className="mc-btn mc-btn-primary" href="/meet">
          Start a meeting
        </a>
      </nav>
    </header>
  );
}

export function SiteFooter() {
  const demo = demoReplayHref();
  return (
    <footer className="foot">
      <div className="foot-brand">
        <div className="mc-brand">
          <Mark />
          MinuteCanvas
        </div>
        <p>A live visual notetaker built with Meta Muse models for the Meta Global AI Developer Hackathon 2026.</p>
      </div>
      <div>
        <h4>Product</h4>
        <a href="/meet">Start a meeting</a>
        <a href="/dashboard">Dashboard</a>
        {demo && <a href={demo}>Demo replay</a>}
      </div>
      <div>
        <h4>About</h4>
        <a href="/privacy">Privacy policy</a>
        <a href={SITE.github} target="_blank" rel="noopener">
          Source on GitHub (MIT)
        </a>
        {SITE.contactEmail && <a href={`mailto:${SITE.contactEmail}`}>{SITE.contactEmail}</a>}
      </div>
    </footer>
  );
}
