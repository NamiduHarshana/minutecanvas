import type { Metadata } from "next";
import { CircleCheck, CircleHelp, Play, TriangleAlert } from "lucide-react";
import { DemoVideo } from "@/components/site/DemoVideo";
import { SITE, demoReplayHref } from "@/core/site";

export const metadata: Metadata = {
  title: "MinuteCanvas — notes tell you what was said, MinuteCanvas shows what was missed",
  description:
    "A live visual notetaker for meetings. It draws the flow people describe, checks every box against the transcript, and points out the steps nobody covered. Built with Meta Muse models.",
};

const FAQ: { q: string; a: React.ReactNode }[] = [
  {
    q: "Do I need an account?",
    a: "No. Start a meeting right away. Sign in with Google only to keep your meetings in one list across devices.",
  },
  {
    q: "Does it work with Google Meet and Zoom?",
    a: "Yes, in Chrome: choose “Mic + meeting tab” and share the meeting tab with its audio. Use headphones so your mic doesn't pick up the speakers. For a meeting in the room, choose Mic.",
  },
  {
    q: "Which models does it use?",
    a: "Muse Voice Transcribe for the transcript, Muse Spark for the board, the Flow Map, the findings and the report, and Muse Image for the card pictures.",
  },
  {
    q: "What happens to my audio?",
    a: `It is streamed to Meta for transcription and never stored. Only a text replay is saved — transcript, board, Flow Map and card pictures — for ${SITE.keepDays} days.`,
  },
  {
    q: "Who can see my meeting?",
    a: "Only people you give the replay link to. The link contains a long random code that can't be guessed, and you can delete the replay any time.",
  },
  {
    q: "Do the other people need to install anything?",
    a: "No. Only the person taking notes opens MinuteCanvas in a browser. Everyone else joins the meeting as usual.",
  },
  {
    q: "What if the AI gets something wrong?",
    a: "Every Flow Map box, gap and conflict shows the transcript words it is based on, so you can check it in one click. You can edit or delete any card and rename the speakers.",
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
      <section className="sec hero-sec">
        <span className="mc-tag mc-tag-plain pill-tag">Built with Meta Muse models</span>
        <h1 className="big">
          Notes tell you what was said.
          <br />
          <span>MinuteCanvas shows what was missed.</span>
        </h1>
        <p className="lead">
          It listens to your meeting and draws the flow people describe, live. Then it circles the branches nobody covered and the points people disagree
          on — each one backed by the exact words that were said.
        </p>
        <div className="mc-row ctas">
          <a className="mc-btn mc-btn-primary mc-btn-lg" href="/meet">
            Start a meeting — no sign-up
          </a>
          {replay ? (
            <a className="mc-btn mc-btn-secondary mc-btn-lg" href={replay}>
              <Play strokeWidth={1.75} aria-hidden />
              Watch a demo replay
            </a>
          ) : (
            <a className="mc-btn mc-btn-secondary mc-btn-lg" href="#how">
              See how it works
            </a>
          )}
        </div>

        <figure className="flow hero-flow" aria-label="Example: two steps of a flow, then a dashed box asking how customers pay, which nobody said">
          <div className="band">
            <span className="lab">Behavioral coaching</span>
            <div className="chain">
              <div className="mc-step demo d1">
                <div className="k">
                  <b>8</b>Step
                </div>
                <div className="t">Open coaching page</div>
                <div className="q">“if they click on this then…”</div>
              </div>
              <span className="arrow d2" />
              <div className="mc-step demo d2">
                <div className="k">
                  <b>9</b>Step
                </div>
                <div className="t">See session price</div>
                <div className="q">“200 per session”</div>
              </div>
              <span className="arrow dash d3" />
              <div className="mc-gapnode demo d3">
                <div className="t">How do customers pay?</div>
                <div className="s">Nobody said</div>
              </div>
            </div>
          </div>
        </figure>
      </section>

      {(SITE.demoVideoId || replay) && (
        <section className="sec tight" aria-labelledby="video-h">
          <span className="lab">Demo</span>
          <h2 id="video-h" className="mid sec-h">
            See it on a real meeting.
          </h2>
          <DemoVideo />
        </section>
      )}

      <section className="sec tight" aria-labelledby="why-h">
        <div className="grid2 center">
          <div>
            <span className="lab">The problem</span>
            <h2 id="why-h" className="mid" style={{ marginTop: 12 }}>
              Meetings decide things. Then the details fall through.
            </h2>
          </div>
          <p className="prose">
            A professional graphic recorder draws a meeting live for $700–$5,000 a day, so most teams never get one. Plain notes list what was said — they
            don’t show the step nobody explained, or that two people said opposite things. Those are the gaps that turn into rework later.
          </p>
        </div>
      </section>

      <section className="sec tight" id="how" aria-labelledby="how-h">
        <span className="lab">How it works</span>
        <h2 id="how-h" className="mid sec-h">
          Three models, one live canvas.
        </h2>
        <div className="steps">
          <div className="feat">
            <span className="num">1</span>
            <h3>Muse Voice listens</h3>
            <p>Your mic, or your mic plus a Google Meet or Zoom tab. A live transcript with speakers, in seconds.</p>
          </div>
          <div className="feat">
            <span className="num">2</span>
            <h3>Muse Spark maps</h3>
            <p>An agent draws the flow as people describe it, checks every box against the transcript, and flags gaps, conflicts and bugs.</p>
          </div>
          <div className="feat">
            <span className="num">3</span>
            <h3>You leave with answers</h3>
            <p>A report, a replay link and the open questions to ask next time. Muse Image illustrates each board card.</p>
          </div>
        </div>
      </section>

      <section className="sec tight" id="features" aria-labelledby="feat-h">
        <span className="lab">Features</span>
        <h2 id="feat-h" className="mid sec-h">
          Built to catch what notes miss.
        </h2>
        <div className="grid2">
          <div className="feat">
            <span className="mc-tag mc-tag-gap">
              <CircleHelp aria-hidden />
              Gaps
            </span>
            <h3>Live Flow Map</h3>
            <p>Steps appear as they are described, grouped into product parts. Dashed boxes mark what nobody said happens next.</p>
          </div>
          <div className="feat">
            <span className="mc-tag mc-tag-conflict">
              <TriangleAlert aria-hidden />
              Conflicts
            </span>
            <h3>Two quotes, side by side</h3>
            <p>When people say opposite things, both quotes are pinned to the step so the team can settle it.</p>
          </div>
          <div className="feat">
            <span className="mc-tag mc-tag-ok">
              <CircleCheck aria-hidden />
              Evidence
            </span>
            <h3>Every box is quote-checked</h3>
            <p>Click any step to see who said it and when. Nothing on the map is invented: a box without the words to prove it is refused.</p>
          </div>
          <div className="feat">
            <span className="mc-tag mc-tag-plain">Replay</span>
            <h3>Replay the meeting</h3>
            <p>A share link that plays the meeting back — transcript, board and Flow Map growing in order — with every finding on the timeline.</p>
          </div>
        </div>
      </section>

      <section className="sec tight" aria-labelledby="use-h">
        <div className="grid2">
          <div>
            <span className="lab">How to use it</span>
            <h2 id="use-h" className="mid" style={{ marginTop: 12 }}>
              Five steps, no setup.
            </h2>
          </div>
          <ol className="howto">
            <li>
              <b>Open New meeting.</b> Add a title if you like, and the names and product words people will say, so the transcript spells them right.
            </li>
            <li>
              <b>Pick the audio.</b> Mic for a meeting in the room. Mic + meeting tab for Google Meet or Zoom in Chrome: choose the meeting tab and turn on
              “Also share tab audio”.
            </li>
            <li>
              <b>Tell everyone.</b> Copy the ready-made notice into the meeting chat, then tick the box to confirm people know.
            </li>
            <li>
              <b>Press Start recording</b> and keep talking. The board and the Flow Map build on their own.
            </li>
            <li>
              <b>Press Stop.</b> A final check runs, then the report and the replay link are ready.
            </li>
          </ol>
        </div>
      </section>

      <section className="sec tight" id="privacy" aria-labelledby="priv-h">
        <div className="flow priv">
          <div>
            <span className="lab">Privacy</span>
            <h2 id="priv-h" className="mid" style={{ marginTop: 12, fontSize: 34, lineHeight: "40px" }}>
              Clear by default.
            </h2>
            <a className="link" href="/privacy" style={{ marginTop: 12 }}>
              Read the privacy policy →
            </a>
          </div>
          <div>
            <h3>Audio is never stored</h3>
            <p>It goes to Meta for live transcription only.</p>
          </div>
          <div>
            <h3>Everyone is told</h3>
            <p>You confirm participants know, and can paste a notice into the meeting chat.</p>
          </div>
          <div>
            <h3>You can delete it</h3>
            <p>Text replays last {SITE.keepDays} days. Delete one, or everything, any time.</p>
          </div>
        </div>
      </section>

      <section className="sec tight faq" id="faq" aria-labelledby="faq-h">
        <span className="lab">FAQ</span>
        <h2 id="faq-h" className="mid" style={{ margin: "12px 0 28px" }}>
          Questions
        </h2>
        {FAQ.map((f) => (
          <details key={f.q}>
            <summary>{f.q}</summary>
            <p>{f.a}</p>
          </details>
        ))}
      </section>
    </>
  );
}
