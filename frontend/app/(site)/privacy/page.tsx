import type { Metadata } from "next";
import { SITE } from "@/core/site";

export const metadata: Metadata = {
  title: "Privacy policy — MinuteCanvas",
  description: "What MinuteCanvas keeps, for how long, who processes it, and how to delete it.",
};

const UPDATED = "2 November 2026";

export default function Privacy() {
  const contact = SITE.contactEmail ? (
    <a href={`mailto:${SITE.contactEmail}`}>{SITE.contactEmail}</a>
  ) : (
    <a href={`${SITE.github}/issues`} target="_blank" rel="noopener">
      the project&rsquo;s GitHub page
    </a>
  );
  return (
    <article className="s-doc">
      <div className="s-wrap s-doc-in">
        <h1>Privacy policy</h1>
        <p className="s-updated">Last updated {UPDATED}</p>

        <section className="s-short" aria-labelledby="short-h">
          <h2 id="short-h">The short version</h2>
          <ul>
            <li>MinuteCanvas never saves meeting audio.</li>
            <li>After you press Stop, a text replay of the meeting is saved so you can share it. It is deleted after {SITE.keepDays} days, or sooner if you delete it.</li>
            <li>You don&rsquo;t need an account. If you sign in with Google to keep your meetings in one list, we keep your email address and name.</li>
            <li>No ads, no analytics, no tracking cookies, and nothing is sold.</li>
          </ul>
        </section>

        <h2>Who runs MinuteCanvas</h2>
        <p>
          MinuteCanvas is an open-source project built and run by Namidu Harshana in Sri Lanka for the Meta Global AI Developer Hackathon 2026. For any
          privacy question or request, contact {contact}.
        </p>

        <h2>What happens during a meeting</h2>
        <p>
          The person running the meeting chooses the audio: their microphone, or their microphone plus one browser tab. That audio is streamed to our
          server and from there to Meta&rsquo;s Muse Voice Transcribe model, which turns it into text in real time. To recover from network drops, your browser
          and our server keep at most about the last minute of audio in memory; it is never written to disk or storage.
        </p>
        <p>
          The transcript is sent to Meta&rsquo;s Muse Spark model to build the board and the Flow Map, and short card descriptions are sent to Meta&rsquo;s
          Muse Image model to draw the card pictures. Meta processes this data under the terms of the Meta Model API.
        </p>
        <p>
          Before recording starts, the person running the meeting must confirm that everyone in the meeting knows it is being transcribed. The app gives them
          a notice to paste in the meeting chat.
        </p>

        <h2>What we keep</h2>
        <table>
          <thead>
            <tr>
              <th scope="col">Data</th>
              <th scope="col">Why</th>
              <th scope="col">How long</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Meeting audio</td>
              <td>Live transcription only</td>
              <td>Never stored</td>
            </tr>
            <tr>
              <td>Text replay: transcript with speaker names, board cards, Flow Map, card pictures, report</td>
              <td>The replay link and the report</td>
              <td>{SITE.keepDays} days after the meeting, or until you delete it</td>
            </tr>
            <tr>
              <td>Meeting summary: title, date, length, the gaps, conflicts and bugs found (with their quotes), and how much Meta model work it used</td>
              <td>Your Dashboard, Findings and Usage pages</td>
              <td>Deleted together with the replay</td>
            </tr>
            <tr>
              <td>Google account email and name (only if you sign in)</td>
              <td>To show your meetings on your Dashboard, Meetings, Findings and Usage pages</td>
              <td>Until you choose &ldquo;Delete everything&rdquo; in Account, or ask us</td>
            </tr>
            <tr>
              <td>IP address</td>
              <td>Limits on how often a meeting can be started, to prevent abuse</td>
              <td>In server memory for a few hours at most; also in server logs (below)</td>
            </tr>
            <tr>
              <td>Server logs: request times, IP addresses, errors</td>
              <td>Keeping the service running and secure</td>
              <td>Up to 30 days (Google Cloud Logging)</td>
            </tr>
          </tbody>
        </table>
        <p>
          The meeting&rsquo;s live working data (transcript, board, Flow Map, card pictures) is held in server memory while the meeting runs and is removed
          from memory at the latest a few hours after the meeting ends.
        </p>

        <h2>Who can see a replay</h2>
        <p>
          Anyone who has the replay link. The link contains a long random code that can&rsquo;t be guessed, and replays are not listed or searchable anywhere.
          Only share it with people who should see the meeting.
        </p>

        <h2>Deleting your data</h2>
        <ul>
          <li>
            <b>One meeting:</b> use Delete replay in the meeting (Replay menu), on the replay page in the browser you made it on, or from Meetings (⋯ → Delete). The
            transcript, board, Flow Map and pictures are removed and the link stops working for everyone.
          </li>
          <li>
            <b>Everything:</b> in Account, choose Delete everything. Every replay in your account and your account&rsquo;s list are removed.
          </li>
          <li>
            <b>Automatically:</b> every replay is deleted {SITE.keepDays} days after the meeting. The public demo meeting on this site is the only exception.
          </li>
        </ul>

        <h2>Your browser</h2>
        <p>
          MinuteCanvas uses your browser&rsquo;s local storage, not cookies, to remember your setup choices (audio source and project words), the meetings you
          made on this browser (with their summaries, and the key that lets you delete them), your theme and your sign-in. You can clear it at any time in your browser settings.
          Signing out also removes the meeting list from the browser.
        </p>

        <h2>Other services</h2>
        <ul>
          <li>
            <b>Meta (Meta Model API):</b> transcription, the board, the Flow Map and the card pictures.
          </li>
          <li>
            <b>Google Cloud (United States):</b> hosting, and storage of the replays and the signed-in meeting lists.
          </li>
          <li>
            <b>Google sign-in:</b> only when you choose to sign in, on the Account page.
          </li>
          <li>
            <b>YouTube (youtube-nocookie.com):</b> only if you press play on the demo video. Nothing is loaded from YouTube before that.
          </li>
        </ul>
        <p>We don&rsquo;t use advertising or analytics services, and we don&rsquo;t sell or share data for marketing.</p>

        <h2>Children</h2>
        <p>MinuteCanvas is made for work meetings and is not meant for children under 16.</p>

        <h2>Changes</h2>
        <p>If this policy changes, the new version will be posted on this page with a new date.</p>
      </div>
    </article>
  );
}
