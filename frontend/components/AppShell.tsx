"use client";
// The signed-in app's frame: left sidebar (264px) + content. At phone width the sidebar becomes a top bar with a menu.
import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Code,
  Ellipsis,
  Gauge,
  House,
  LayoutGrid,
  List,
  LogOut,
  Menu,
  Mic,
  Moon,
  Search,
  Shield,
  Sun,
  SunMoon,
  UserRound,
  X,
} from "lucide-react";
import { signOut } from "@/core/account";
import { useUser } from "@/core/library";
import { useTheme, type ThemePref } from "@/core/theme";
import { SITE } from "@/core/site";
import { Initials } from "./kit";
import { Mark } from "./Logo";

export type NavKey = "dashboard" | "newm" | "meetings" | "findings" | "usage" | "account";

const I = { size: 18, strokeWidth: 1.75, "aria-hidden": true } as const;

export function AppShell({ active, children }: { active: NavKey; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  return (
    <div className="app">
      <a className="skip" href="#content">
        Skip to content
      </a>
      <header className="app-top">
        <a href="/dashboard" className="mc-brand" aria-label="MinuteCanvas dashboard">
          <Mark />
          MinuteCanvas
        </a>
        <button className="mc-ib" onClick={() => setOpen(true)} aria-label="Open menu" aria-expanded={open}>
          <Menu size={18} strokeWidth={1.75} aria-hidden />
        </button>
      </header>
      {open && <div className="app-scrim" onClick={() => setOpen(false)} aria-hidden />}
      <Sidebar active={active} open={open} onClose={() => setOpen(false)} />
      <main className="main" id="content">
        {children}
      </main>
    </div>
  );
}

function Sidebar({ active, open, onClose }: { active: NavKey; open: boolean; onClose: () => void }) {
  const on = (k: NavKey) => (k === active ? "on" : undefined);
  const cur = (k: NavKey) => (k === active ? ("page" as const) : undefined);
  return (
    <aside className={`mc-side ${open ? "open" : ""}`} aria-label="Sidebar">
      <div className="side-head">
        <a href="/" className="mc-brand">
          <Mark />
          MinuteCanvas
        </a>
        <button className="mc-ib side-close" onClick={onClose} aria-label="Close menu">
          <X size={18} strokeWidth={1.75} aria-hidden />
        </button>
      </div>
      <nav className="mc-nav" aria-label="Site">
        <a href="/">
          <House {...I} />
          Home
          <ArrowUpRight className="ext" strokeWidth={1.75} aria-hidden />
        </a>
      </nav>
      <div className="mc-sep" />
      <nav className="mc-nav" aria-label="App">
        <a href="/dashboard" className={on("dashboard")} aria-current={cur("dashboard")}>
          <LayoutGrid {...I} />
          Dashboard
        </a>
        <a href="/meet" className={on("newm")} aria-current={cur("newm")}>
          <Mic {...I} />
          New meeting
        </a>
        <a href="/meetings" className={on("meetings")} aria-current={cur("meetings")}>
          <List {...I} />
          Meetings
        </a>
        <a href="/findings" className={on("findings")} aria-current={cur("findings")}>
          <Search {...I} />
          Findings
        </a>
      </nav>
      <div className="mc-sep" />
      <nav className="mc-nav" aria-label="More">
        <a href="/usage" className={on("usage")} aria-current={cur("usage")}>
          <Gauge {...I} />
          Usage
        </a>
        <a href="/privacy">
          <Shield {...I} />
          Privacy
        </a>
        <a href={SITE.github} target="_blank" rel="noopener">
          <Code {...I} />
          GitHub
          <ArrowUpRight className="ext" strokeWidth={1.75} aria-hidden />
        </a>
      </nav>
      <Me active={active === "account"} />
    </aside>
  );
}

const THEMES: { id: ThemePref; label: string; Icon: typeof Sun }[] = [
  { id: "system", label: "Match my device", Icon: SunMoon },
  { id: "dark", label: "Dark", Icon: Moon },
  { id: "light", label: "Light", Icon: Sun },
];

function Me({ active }: { active: boolean }) {
  const user = useUser();
  const { pref, set } = useTheme();
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setMenu(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [menu]);

  if (user === undefined) return <div className="mc-me" aria-hidden />;
  if (!user) {
    return (
      <div className={`mc-me ${active ? "on" : ""}`}>
        <span className="mc-avatar ghost" aria-hidden>
          <UserRound size={18} strokeWidth={1.75} />
        </span>
        <span className="who">
          <b>Not signed in</b>
          <span className="sub">Meetings stay in this browser</span>
        </span>
        <a className="mc-btn mc-btn-outline mc-btn-xs" href="/account">
          Sign in
        </a>
      </div>
    );
  }
  return (
    <div className={`mc-me ${active ? "on" : ""}`} ref={ref}>
      <a href="/account" className="me-link">
        <Initials name={user.name} />
        <span className="who">
          <b>{user.name}</b>
          <span className="sub">Google account</span>
        </span>
      </a>
      <button className="mc-ib ghost" onClick={() => setMenu((m) => !m)} aria-label="Account menu" aria-haspopup="menu" aria-expanded={menu}>
        <Ellipsis size={18} strokeWidth={1.75} aria-hidden />
      </button>
      {menu && (
        <div className="mc-menu up" role="menu">
          <a role="menuitem" href="/account">
            <UserRound size={16} strokeWidth={1.75} aria-hidden /> Account
          </a>
          <div className="mc-menu-sep" />
          <span className="mc-menu-lab">Theme</span>
          {THEMES.map(({ id, label, Icon }) => (
            <button key={id} role="menuitemradio" aria-checked={pref === id} className={pref === id ? "on" : ""} onClick={() => set(id)}>
              <Icon size={16} strokeWidth={1.75} aria-hidden /> {label}
            </button>
          ))}
          <div className="mc-menu-sep" />
          <button
            role="menuitem"
            onClick={() => {
              setMenu(false);
              signOut();
              window.location.href = "/account";
            }}
          >
            <LogOut size={16} strokeWidth={1.75} aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
