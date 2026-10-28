// What a Flow Map box shows besides its title: an icon picked from the words in the label,
// one note line (open issue → open conflict → the real quote), and text tags.
// Nothing here is generated: the note is either the quote-checked transcript words or the
// bug/conflict text that is already backed by quotes.
import {
  Bell,
  CalendarDays,
  Camera,
  CircleCheckBig,
  CircleX,
  CreditCard,
  Diamond,
  Flag,
  House,
  KeyRound,
  LayoutDashboard,
  Mail,
  MapPin,
  MessageSquare,
  Package,
  Play,
  Search,
  Server,
  Settings,
  ShoppingCart,
  Square,
  Star,
  Truck,
  User,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { FlowData, FlowStep, StepKind } from "./types";

const RULES: [RegExp, LucideIcon][] = [
  [/\b(error|fail|failed|fails|declin|reject|cancel)/, CircleX],
  [/\b(otp|log ?in|sign ?in|sign ?up|register|auth|password|verif|2fa|pin)\b/, KeyRound],
  [/\b(confirm|success|succeed|done|complete|thank|receipt)/, CircleCheckBig],
  [/\b(checkout|pay|payment|billing|invoice|cards?)\b/, CreditCard],
  [/\b(cart|basket|bag)\b/, ShoppingCart],
  [/\b(home|landing|welcome|splash)\b/, House],
  [/\b(search|filter|browse|categor)/, Search],
  [/\b(product|item|detail|catalog|menu|sofa|listing)/, Package],
  [/\b(deliver|ship|track|courier|dispatch)/, Truck],
  [/\b(admin|dashboard|stock|inventory|report|analytic)/, LayoutDashboard],
  [/\b(profile|account|user|customer)/, User],
  [/\b(notif|alert|sms|push)/, Bell],
  [/\b(e-?mail|mail)\b/, Mail],
  [/\b(upload|scan|photo|camera|image)/, Camera],
  [/\b(book|schedul|calendar|appointment|slot)/, CalendarDays],
  [/\b(chat|message|support|contact)/, MessageSquare],
  [/\b(session|meeting|call|interview|one-on-one|1:1)\b/, Users],
  [/\b(location|address|gps|directions|map (view|screen|page))\b/, MapPin],   // not "map stories" (the verb)
  [/\b(setting|config|preference)/, Settings],
  [/\b(review|rating|feedback)/, Star],
  [/\b(api|server|backend|database|sync|webhook)\b/, Server],
];

const KIND_ICON: Record<StepKind, LucideIcon> = { start: Play, step: Square, decision: Diamond, end: Flag };

export function stepIcon(step: Pick<FlowStep, "label" | "kind">): LucideIcon {
  const t = step.label.toLowerCase();
  for (const [re, Icon] of RULES) if (re.test(t)) return Icon;
  return KIND_ICON[step.kind];
}

export type NoteTone = "bug" | "conflict" | "quote";
export interface StepNote {
  tone: NoteTone;
  text: string;
}

export function stepNote(flow: FlowData, step: FlowStep): StepNote | null {
  const bug = flow.bugs.find((b) => b.step === step.id && !b.fixed);
  if (bug) return { tone: "bug", text: bug.text };
  const c = flow.contradictions.find((x) => x.step === step.id && !x.resolved);
  if (c) return { tone: "conflict", text: c.note };
  const q = step.evidence[0]?.quote?.trim();
  return q ? { tone: "quote", text: q } : null;
}

export function issueTag(n: number): string {
  return n === 1 ? "Issue" : `${n} issues`;
}
export function conflictTag(n: number): string {
  return n === 1 ? "Conflict" : `${n} conflicts`;
}
