import type { Metadata } from "next";
import { HeroDemo } from "@/components/site/HeroDemo";
import { DemoVideo } from "@/components/site/DemoVideo";
import { SITE, demoReplayHref } from "@/core/site";

export const metadata: Metadata = {
  title: "MinuteCanvas — notes tell you what was said, MinuteCanvas shows what was missed",
  description:
    "A live visual notetaker for meetings. It draws the flow people describe, checks every box against the transcript, and points out the steps nobody covered. Built with Meta Muse models.",
};

const STEPS = [
  {
    name: "Listen",
    model: "Muse Voice Transcribe",
    text: "Your mic, or your mic plus a Google Meet or Zoom tab in Chrome, is transcribed live with a label for each speaker. You can give the speakers their real names.",
  },
  {
    name: "Sort",
    model: "Muse Spark",
    text: "About every 40 seconds, Muse Spark reads what was just said and keeps the board up to date: goals, decisions, actions, updates and open questions.",
  },
  {
    name: "Draw",
    model: "Muse Spark + Muse Image",
    text: "A Muse Spark agent draws the flow people describe, box by box, one band per part of the product. Each box must quote the transcript or it is refused. Muse Image illustrates the cards.",
  },
  {
    name: "Check",
    model: "Muse Spark",
    text: "Every few minutes, and once more after you press Stop, the agent looks for what is missing: the step after a dead end, the error path nobody described, two people giving different numbers.",
  },
];

const FAQ: { q: string; a: React.ReactNode }[] = [
  {
    q: "Do the other people in the meeting need to install anything?",
    a: "No. Only the person taking notes opens MinuteCanvas in a browser. Everyone else joins the meeting as usual.",
  },
  {
    q: "Which meetings does it work with?",
    a: "In-room meetings through your microphone, and online meetings that run in a Chrome tab (Google Meet, or Zoom and Teams in the browser) with Mic + Tab. Use headphones for online meetings so your mic doesn't pick up the speakers.",
  },
  {
    q: "Is the audio recorded?",
    a: "No. Audio is streamed to Meta's Muse Voice Transcribe for live transcription and is never saved by MinuteCanvas. What is saved after Stop is a text replay: transcript, board, Flow Map and card images.",
  },
  {
    q: "Who can see my meeting?",
    a: `Only people you give the replay link to. The link contains a long random code that can't be guessed. The replay is deleted after ${SITE.keepDays} days, and you can delete it any time before that.`,
  },
  {
    q: "Do I need an account?",
    a: "No. You can start a meeting without signing in. Sign in with Google only if you want to see all your past meetings in one list under My meetings.",
  },
  {
    q: "What if the AI gets something wrong?",
    a: "Every Flow Map box, gap and conflict shows the transcript words it is based on, so you can check it in one click. You can edit or delete any card and rename the speakers.",
  },
  {
    q: "Which languages and how long a meeting?",
    a: "English for now. A meeting can run for up to three hours.",
  },
  {
    q: "Is it open source?",
    a: (
      <>
        Yes, under the MIT license.{" "}
        <a href={SITE.github} target="_blank" rel="noopener">
          The code is on GitHub
        </a>
        .
      </>
    ),
  },
];

export default function Landing() {
  const replay = demoReplayHref();
  return (
    <>
      <section className="s-hero">
        <div className="s-wrap s-hero-in">
          <div className="s-hero-copy">
            <h1>
              <span className="s-h1-1">Notes tell you what was said.</span> <span className="s-h1-2">MinuteCanvas shows what was missed.</span>
            </h1>
            <p className="s-lede">
              A live visual notetaker for meetings. It draws the flow your team describes while they speak, checks every box against the transcript, and
              points out the steps nobody covered before anyone starts building.
            </p>
            <div className="s-ctas">
              <a href="/meet" className="s-btn s-btn-brand">
                Start a meeting
              </a>
              {replay ? (
                <a href={replay} className="s-btn s-btn-line">
                  Watch a demo replay
                </a>
              ) : (
                <a href="#how" className="s-btn s-btn-line">
                  See how it works
                </a>
              )}
            </div>
            <p className="s-fine">No account needed. Audio is never stored.</p>
          </div>
          <HeroDemo />
        </div>
      </section>

      {(SITE.demoVideoId || replay) && (
        <section className="s-sec s-video" aria-labelledby="video-h">
          <div className="s-wrap">
            <h2 id="video-h" className="s-h2">
              See it on a real meeting
            </h2>
            <DemoVideo />
          </div>
        </section>
      )}

      <section className="s-sec" aria-labelledby="why-h">
        <div className="s-wrap s-split">
          <h2 id="why-h" className="s-h2">
            The costly part of a meeting is the step nobody mentioned.
          </h2>
          <div className="s-prose">
            <p>
              Notes and summaries record what people said. They can't show you what nobody said: what happens when the payment fails, who handles the
              late arrival, or that one person said five minutes and another said ten.
            </p>
            <p>
              Teams that want to see a meeting hire a graphic recorder, who draws it live on a wall. That costs $700 to $5,000 a day, so most meetings
              never get one.
            </p>
            <p>
              MinuteCanvas draws every meeting, and then it goes one step further than a drawing: it checks the flow for holes while everyone is still in
              the room.
            </p>
          </div>
        </div>
      </section>

      <section className="s-sec s-tint" id="how" aria-labelledby="how-h">
        <div className="s-wrap">
          <h2 id="how-h" className="s-h2">
            How it works
          </h2>
          <ol className="s-steps">
            {STEPS.map((s) => (
              <li key={s.name}>
                <h3>{s.name}</h3>
                <p className="s-model">{s.model}</p>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="s-sec" aria-labelledby="feat-h">
        <div className="s-wrap">
          <h2 id="feat-h" className="s-h2">
            What you get
          </h2>
          <div className="s-flow">
            <div className="s-flow-copy">
              <h3>The Live Flow Map</h3>
              <p>
                The flow your team talks through, drawn as they talk. Click any box to see who said it and when. Three marks tell you where to look before
                the meeting ends:
              </p>
            </div>
            <ul className="s-marks">
              <li>
                <svg viewBox="0 0 64 40" aria-hidden>
                  <rect x="2" y="4" width="60" height="32" rx="7" className="mk-gap" />
                  <text x="32" y="25" textAnchor="middle" className="mk-q">
                    ?
                  </text>
                </svg>
                <div>
                  <b>Gap</b>
                  <p>A dashed box where the flow stops or a branch was never described. It also goes on the board as an open question.</p>
                </div>
              </li>
              <li>
                <svg viewBox="0 0 64 40" aria-hidden>
                  <rect x="2" y="4" width="60" height="32" rx="7" className="mk-box" />
                  <rect x="2" y="4" width="5" height="32" rx="2" className="mk-conf" />
                  <path d="M38 13 L46 27 H30 Z" className="mk-conf-i" />
                </svg>
                <div>
                  <b>Conflict</b>
                  <p>Two statements that can't both be true, shown side by side with both quotes, so the right person can settle it.</p>
                </div>
              </li>
              <li>
                <svg viewBox="0 0 64 40" aria-hidden>
                  <rect x="2" y="4" width="60" height="32" rx="7" className="mk-bug" />
                  <circle cx="54" cy="8" r="6" className="mk-bug-dot" />
                </svg>
                <div>
                  <b>Bug</b>
                  <p>A problem someone reported, pinned in red on the exact step it happens in.</p>
                </div>
              </li>
            </ul>
          </div>
          <dl className="s-more">
            <div>
              <dt>A board that fills itself</dt>
              <dd>Goals, decisions, actions, updates and open questions, each card illustrated by Muse Image.</dd>
            </div>
            <div>
              <dt>Proof for everything</dt>
              <dd>Every box, gap and conflict carries the transcript words it came from. Boxes without proof are refused.</dd>
            </div>
            <div>
              <dt>A replay link</dt>
              <dd>Anyone you send it to can watch the meeting build up from 00:00, jump to any moment, and play it up to 8 times faster.</dd>
            </div>
            <div>
              <dt>A report at the end</dt>
              <dd>Summary, decisions, actions and what was missed, as slides you can save as a PDF.</dd>
            </div>
            <div>
              <dt>Export</dt>
              <dd>Save the board as a PNG or a PDF.</dd>
            </div>
            <div>
              <dt>Your edits win</dt>
              <dd>Rename speakers, fix or delete cards. The AI won't add a deleted card back.</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="s-sec s-tint" aria-labelledby="use-h">
        <div className="s-wrap s-split">
          <h2 id="use-h" className="s-h2">
            How to use it
          </h2>
          <ol className="s-howto">
            <li>
              <b>Open Start a meeting.</b> Add a title if you like, and the names and product words people will say, so the transcript spells them right.
            </li>
            <li>
              <b>Pick the audio.</b> Mic for a meeting in the room. Mic + Tab for Google Meet or Zoom in Chrome: choose the meeting tab and turn on
              &ldquo;Also share tab audio&rdquo;.
            </li>
            <li>
              <b>Tell everyone.</b> Copy the ready-made notice into the meeting chat, then tick the box to confirm people know.
            </li>
            <li>
              <b>Press Start recording</b> and keep talking. The board and the Flow Map build on their own.
            </li>
            <li>
              <b>Press Stop.</b> A final check runs, then the report and the replay link are ready. Delete the replay whenever you want.
            </li>
          </ol>
        </div>
      </section>

      <section className="s-sec" aria-labelledby="priv-h">
        <div className="s-wrap s-split">
          <h2 id="priv-h" className="s-h2">
            Your meeting stays yours
          </h2>
          <div>
            <ul className="s-privacy">
              <li>Audio is sent to Meta for live transcription and is never saved.</li>
              <li>The text replay is kept for {SITE.keepDays} days, then deleted. You can delete it sooner.</li>
              <li>No account is needed to run a meeting. Google sign-in, only for My meetings, keeps your email and name.</li>
              <li>No ads, no analytics, no tracking cookies.</li>
            </ul>
            <a className="s-textlink" href="/privacy">
              Read the privacy policy
            </a>
          </div>
        </div>
      </section>

      <section className="s-sec s-tint" id="faq" aria-labelledby="faq-h">
        <div className="s-wrap s-split">
          <h2 id="faq-h" className="s-h2">
            Questions
          </h2>
          <div className="s-faq">
            {FAQ.map((f) => (
              <details key={f.q}>
                <summary>{f.q}</summary>
                <p>{f.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="s-end">
        <div className="s-wrap s-end-in">
          <h2 className="s-h2">Run your next meeting with it.</h2>
          <a href="/meet" className="s-btn s-btn-brand">
            Start a meeting
          </a>
        </div>
      </section>
    </>
  );
}
