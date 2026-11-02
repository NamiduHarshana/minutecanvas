"use client";
// The landing page's one moving piece: a meeting being drawn. Lines arrive, Flow Map boxes follow them, then
// the gap check circles what nobody said. Plays once; reduced motion shows the finished picture.
import { useState } from "react";
import { RotateCcw } from "lucide-react";

const LINES = [
  { t: "00:41", who: "Kasun", text: "Customers land on the home screen and pick a sofa." },
  { t: "00:47", who: "Amali", text: "Then they go to checkout and pay by card." },
  { t: "00:54", who: "Kasun", text: "After the payment succeeds we show the order confirmation." },
  { t: "01:02", who: "Amali", text: "Delivery takes five working days inside Colombo." },
];

export function HeroDemo() {
  const [run, setRun] = useState(0);
  return (
    <figure className="hd" aria-label="Example: MinuteCanvas drawing a meeting while people speak">
      <div className="hd-run" key={run}>
        <div className="hd-bar">
          <span className="hd-rec" aria-hidden />
          <span>Recording</span>
          <b className="hd-clock">01:04</b>
          <span className="hd-src">Mic + Tab</span>
        </div>
        <div className="hd-body">
          <ol className="hd-lines" aria-label="Transcript">
            {LINES.map((l, i) => (
              <li key={i} style={{ "--d": `${0.3 + i * 1.3}s` } as React.CSSProperties}>
                <time>{l.t}</time>
                <span>
                  <b>{l.who}:</b> {l.text}
                </span>
              </li>
            ))}
          </ol>
          <svg className="hd-map" viewBox="4 6 358 276" role="img" aria-label="Flow Map: Home screen, then Checkout and pay, then Order confirmed. A dashed box beside Checkout asks: payment fails? Nobody said.">
            <defs>
              <marker id="hd-ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0 0 8 4 0 8z" fill="#667085" />
              </marker>
              <marker id="hd-ah-gap" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0 0 8 4 0 8z" fill="#d97706" />
              </marker>
            </defs>
            <g className="hd-x" style={{ "--d": "0.8s" } as React.CSSProperties}>
              <rect className="hd-box" x="12" y="14" width="160" height="52" rx="9" />
              <rect className="hd-edge" x="12" y="14" width="4" height="52" rx="2" />
              <text className="hd-k" x="28" y="34">Start</text>
              <text className="hd-t" x="28" y="54">Home screen</text>
            </g>
            <g className="hd-x" style={{ "--d": "2.1s" } as React.CSSProperties}>
              <path className="hd-arrow" d="M92 66 V104" markerEnd="url(#hd-ah)" />
              <rect className="hd-box" x="12" y="108" width="160" height="52" rx="9" />
              <rect className="hd-edge" x="12" y="108" width="4" height="52" rx="2" />
              <text className="hd-k" x="28" y="128">Decision</text>
              <text className="hd-t" x="28" y="148">Checkout &amp; pay</text>
            </g>
            <g className="hd-x" style={{ "--d": "3.4s" } as React.CSSProperties}>
              <path className="hd-arrow" d="M92 160 V218" markerEnd="url(#hd-ah)" />
              <rect className="hd-pill" x="66" y="179" width="52" height="18" rx="9" />
              <text className="hd-pill-t" x="92" y="191.5" textAnchor="middle">success</text>
              <rect className="hd-box" x="12" y="222" width="160" height="52" rx="9" />
              <rect className="hd-edge" x="12" y="222" width="4" height="52" rx="2" />
              <text className="hd-k" x="28" y="242">End</text>
              <text className="hd-t" x="28" y="262">Order confirmed</text>
            </g>
            <g className="hd-x hd-gap" style={{ "--d": "5.6s" } as React.CSSProperties}>
              <path className="hd-arrow-gap" d="M172 134 H206" markerEnd="url(#hd-ah-gap)" />
              <rect className="hd-gapbox" x="210" y="104" width="136" height="60" rx="9" />
              <text className="hd-gt" x="224" y="130">payment fails?</text>
              <text className="hd-gn" x="224" y="149">Nobody said</text>
            </g>
            <path
              className="hd-circle"
              pathLength={1}
              d="M282 92c40 0 74 12 74 41s-34 44-80 44-82-13-81-44c1-26 38-41 92-40 12 0 22 2 30 5"
            />
          </svg>
        </div>
        <figcaption className="hd-cap">
          <b>Gap found.</b> Nobody said what happens when the card payment fails.
        </figcaption>
      </div>
      <button type="button" className="hd-again" onClick={() => setRun((r) => r + 1)}>
        <RotateCcw size={14} aria-hidden /> Play again
      </button>
    </figure>
  );
}
