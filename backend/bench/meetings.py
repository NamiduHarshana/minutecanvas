"""Scripted test meetings for the transcript bench, each with an answer key.

They are written like ASR output of real meetings (fillers, short "mm-hmm" lines, sentences cut over two lines) and
use other domains than the prompt examples, so a good score is not just the prompt's own examples coming back.

Answer key fields (all optional):
  steps:      {key: [[words], [words], ...]}  a map step matches when its label contains ALL words of one list
              (word stems, lowercase; "!word" = must NOT contain it). The key's prefix before "." is the part ("trial.book" -> part "trial");
              "a|b.x" = the step belongs to either part (a staff action inside the customer journey, a step two parts share).
  links:      [[from_key, to_key], ...]       expected arrows (a path of at most 2 arrows also counts)
  cross_ok:   [[from_part, to_part], ...]     arrows between parts that the transcript really says
  gaps:       [{"name", "any": [[words]...]}] real open paths: a gap question with all words of one list
  no_gaps:    [{"name", "any": [[words]...], "answered": "later"?}] answered in the meeting: never a gap. With
              "answered": "later" a gap flagged before the answer came and closed afterwards is fine.
  conflicts:  [{"name", "numbers": [[a, b], ...]}]   a contradiction whose two quotes carry a and b
  no_conflicts: same shape: must never be flagged
  stale:      [{"old": [words], "new": [words]}]   no card may still name the old thing (without the new one) at the end
"""
from __future__ import annotations

import re

WPS = 2.6           # words per second while talking


def build(script: list[tuple[str, str]], gap_s: float = 0.6) -> list[dict]:
    """[(speaker, text)] -> transcript lines with speech start t and arrival t_end (end of the turn + ~2 s)."""
    out, t = [], 3.0
    for i, (sp, text) in enumerate(script):
        words = len(re.findall(r"\w+", text))
        dur = max(0.8, words / WPS)
        out.append({"id": i, "turn": f"1-{i}", "speaker": sp, "text": text, "t": round(t, 1),
                    "t_end": round(t + dur + 2.0, 1)})
        t += dur + gap_s
    return out


# ---------------------------------------------------------------- 1. pet grooming app (one journey, fail paths)
GROOMING = [
    ("A", "Okay so thanks for joining. I want to walk you through how the grooming app should work for our customers."),
    ("B", "Sure, go ahead."),
    ("A", "So first the owner downloads the app and signs in with their phone number."),
    ("A", "They get a code by SMS and they type it in."),
    ("B", "Mm-hmm. And if the code is wrong?"),
    ("A", "If it's wrong they can ask for a new code after thirty seconds."),
    ("B", "Okay."),
    ("A", "Then the first time they have to add their pet, like the name, the breed and the size."),
    ("B", "Small, medium, large?"),
    ("A", "Yeah exactly small medium large because the price depends on the size."),
    ("A", "A bath for a small dog is thirty dollars and for a large dog it's fifty dollars."),
    ("B", "Got it. And a full groom?"),
    ("A", "Full groom is sixty for small and ninety for large."),
    ("B", "Mm-hmm."),
    ("A", "So after adding the pet they choose the service, bath or full groom or nails."),
    ("A", "And then they pick a groomer and a time slot from the calendar."),
    ("B", "What if the slot they want is already taken?"),
    ("A", "Then we just show the next free slots for that groomer, or other groomers on the same day."),
    ("B", "Okay that makes sense."),
    ("A", "And when they pick the slot they pay a deposit, twenty percent of the price, by card."),
    ("B", "Twenty percent, okay. And after the deposit?"),
    ("A", "After the deposit the booking is confirmed and they see a confirmation screen with the date."),
    ("A", "We also email them the receipt."),
    ("B", "Right. Do they get a reminder?"),
    ("A", "Yes a reminder SMS the day before at around six in the evening."),
    ("B", "Cool."),
    ("A", "Oh and they can cancel for free up to twenty four hours before."),
    ("B", "Up to twenty four hours, okay."),
    ("A", "Now on the day, the owner comes to the salon and checks in at the front desk with the app."),
    ("B", "With a QR code or something?"),
    ("A", "Yeah a QR code on the booking."),
    ("B", "Mm-hmm."),
    ("A", "And then the groomer starts and when the groomer marks it done and then"),
    ("A", "then the owner gets a photo of the dog and a message that it's ready for pickup."),
    ("B", "Oh nice, the photo is a good touch."),
    ("A", "Yeah people love that. Then they pay the rest at the counter or in the app."),
    ("B", "And after pickup?"),
    ("A", "After pickup they can rate the groomer from one to five stars."),
    ("B", "Okay. What about if the owner is late?"),
    ("A", "Good question, let me think. I'll come back to that."),
    ("B", "Sure."),
    ("A", "So for the groomers themselves, they have a simple screen with today's bookings."),
    ("B", "Mm-hmm."),
    ("A", "They log in with a staff account on the salon tablet."),
    ("B", "Okay."),
    ("A", "And the manager adds new groomers to the system."),
    ("A", "They see the pet, the service and any notes like the dog bites or it's nervous."),
    ("B", "Okay so a separate groomer view."),
    ("A", "Yes, same app but a different login for staff."),
    ("B", "Got it."),
    ("A", "Oh about being late. If they are more than fifteen minutes late the groomer can move them to the next free slot."),
    ("B", "Okay, and they keep the deposit?"),
    ("A", "Yes the deposit moves with the booking."),
    ("B", "Alright. And if a groomer is sick that day?"),
    ("A", "Then the manager moves their bookings to another groomer and the owners get a message about it."),
    ("B", "Okay good."),
    ("A", "And one more thing about receipts, instead of emailing the receipt let's send it on WhatsApp, people don't read email."),
    ("B", "WhatsApp, okay, I'll check the API."),
    ("A", "And on the booking screen write that cancellation is free up to twelve hours before."),
    ("B", "Okay, twelve hours."),
    ("A", "So can you send me a first design by next Wednesday?"),
    ("B", "Yes, I'll send the screens by Wednesday."),
    ("A", "Great, thanks."),
]
GROOMING_KEY = {
    "steps": {
        "app.sign": [["sign"], ["log", "!staff"], ["phone"]],
        "app.code": [["code"], ["otp"], ["verif"]],
        "app.pet": [["pet", "!note"]],   # not the groomer's "View pet (service) notes"
        "app.service": [["servic", "!note"]],
        "app.slot": [["slot"], ["groomer", "pick"], ["time"]],
        "app.deposit": [["deposit"], ["pay"]],
        "app.confirm": [["confirm"]],
        "app.checkin": [["check"], ["qr"]],
        "app|staff.done": [["done"], ["finish"], ["mark"]],
        "app.photo": [["photo"], ["pickup"], ["ready"]],
        "app.rate": [["rate"], ["rating"], ["star"]],
        "staff.today": [["today"], ["booking", "view"], ["groomer", "view"]],
        "staff.login": [["log", "staff"], ["staff", "account"], ["staff", "login"]],
    },
    "links": [["app.sign", "app.code"], ["app.code", "app.pet"], ["app.pet", "app.service"], ["app.service", "app.slot"],
              ["app.slot", "app.deposit"], ["app.deposit", "app.confirm"],
              # "done" may sit in the groomer's row (app|staff): then the owner's row goes check-in -> photo and the
              # groomer's "done" leads into the photo (the next pair is required anyway)
              [["app.checkin", "app|staff.done"], ["app.checkin", "app.photo"]],
              ["app|staff.done", "app.photo"], ["app.photo", "app.rate"], ["staff.login", "staff.today"]],
    "cross_ok": [],
    "gaps": [{"name": "deposit payment fails", "any": [["fail"], ["declin"]]},
             {"name": "owner doesn't show up", "any": [["show"], ["miss"], ["come"]]}],
    "no_gaps": [{"name": "after the groomer marks it done (next line says)", "any": [["after", "done"], ["after", "mark"]]},
                {"name": "groomer sick / unavailable (answered right after)", "any": [["sick"], ["unavailab"], ["absent"]], "answered": "later"},
                # "!slot": the answer is "move them to the next free slot"; "what if there is NO free slot for a late
                # arrival?" (v6.10 bench) is a new follow-up question, not this answered one
                {"name": "owner late (answered ~1 min later)", "answered": "later",
                 "any": [["owner", "late", "!slot"], ["customer", "late", "!slot"], ["late", "arriv", "!slot"],
                         ["they", "late", "!slot"], ["run", "late", "!slot"]]},
                {"name": "slot taken (answered)", "any": [["slot", "taken"], ["slot", "avail"]]},
                {"name": "wrong code (answered)", "any": [["code", "wrong"], ["otp", "wrong"]]},
                {"name": "after pickup (answered)", "any": [["after", "pickup"]]}],
    "conflicts": [{"name": "free cancellation 24 h vs 12 h", "numbers": [["24", "12"], ["twenty four", "twelve"]]}],
    "no_conflicts": [{"name": "small vs large price", "numbers": [["30", "50"], ["thirty", "fifty"], ["60", "90"], ["sixty", "ninety"]]},
                     {"name": "bath vs full groom", "numbers": [["30", "60"], ["thirty", "sixty"], ["50", "90"], ["fifty", "ninety"]]}],
    "stale": [{"old": ["email"], "new": ["whatsapp"]}],
}

# ---------------------------------------------------------------- 2. music school website (several parts)
MUSIC = [
    ("A", "Hi, so this is about the new website for the music school."),
    ("B", "Yeah, I looked at your old site. What are the main things people should do on it?"),
    ("A", "There are basically four things. Free trial lessons, the group classes, renting instruments, and the teacher side."),
    ("B", "Okay let's go one by one."),
    ("A", "So the trial lesson. A parent books a free trial for their kid."),
    ("A", "They pick the instrument, like piano, guitar or violin, and then they pick a day that suits them."),
    ("B", "Mm-hmm."),
    ("A", "Then they get a confirmation email with the address and what to bring."),
    ("A", "And the teacher calls the parent the day before to say hi."),
    ("B", "Nice. And after the trial?"),
    ("A", "After the trial, if they like it they can buy a class pass, that's the group classes part."),
    ("B", "Okay so let's talk about the group classes."),
    ("A", "Right, so on the classes page they see the timetable for the term."),
    ("A", "A term is twelve weeks."),
    ("B", "Mm-hmm."),
    ("A", "And they can buy a ten class pass for one eighty, or a single class for twenty dollars."),
    ("B", "So the pass is cheaper per class."),
    ("A", "Exactly. Then they just come to class and the teacher marks attendance, and the pass counts down."),
    ("B", "Okay. How do they get updates, like if a class is cancelled?"),
    ("A", "Right now we have a WhatsApp group for the parents for all the announcements."),
    ("B", "Okay, the WhatsApp group."),
    ("A", "Yeah. Okay, renting instruments. A lot of kids don't have an instrument at the start."),
    ("A", "So they fill the rental form on the website and then"),
    ("A", "then Maria checks if we have that instrument in the right size in stock."),
    ("B", "Mm-hmm, and then?"),
    ("A", "If we have it she emails them and they pick it up at the front desk."),
    ("A", "The rental is fifteen dollars a month and they pay it at the front desk when they pick it up."),
    ("B", "Okay. And when they're done they bring it back to the front desk?"),
    ("A", "Yes they return it at the front desk and Maria checks it for damage."),
    ("B", "Got it."),
    ("A", "And the last part is the teacher side. Teachers log in to a teacher portal."),
    ("A", "They see their class list for the day, they mark attendance, and they can post homework for each class."),
    ("B", "So attendance from the group classes is marked there."),
    ("A", "Exactly, same thing."),
    ("B", "And parents see the homework?"),
    ("A", "Yes parents see the homework when they log in, on their kid's page."),
    ("B", "Okay."),
    ("A", "Oh and actually, instead of the WhatsApp group, let's put the announcements in the parent portal, the group gets too noisy."),
    ("B", "Makes sense, announcements in the portal."),
    ("A", "And on the rental page put the price, the rental is twelve dollars a month."),
    ("B", "Twelve a month, okay."),
    ("A", "So, can you send me a sitemap by Friday?"),
    ("B", "Yes, sitemap by Friday."),
    ("A", "Thanks!"),
]
MUSIC_KEY = {
    "steps": {
        "trial.book": [["trial"]],
        "trial.instrument": [["instrument", "pick", "!up"], ["instrument", "choos"], ["instrument", "select"]],
        "trial.day": [["day"], ["date"]],
        "trial.email": [["confirm"], ["email", "!availab", "!stock", "!rental"]],
        "trial.call": [["call"]],
        "class.timetable": [["timetabl"], ["schedul"], ["class", "view", "!list"]],
        "class.pass": [["pass"], ["buy"]],
        "class|teach.attend": [["attend"]],
        "rent.form": [["rental", "form"], ["form"]],
        "rent.stock": [["stock"], ["check", "!damag"]],
        "rent.pickup": [["pick", "up"], ["pickup"], ["collect"]],
        "rent.return": [["return"]],
        "teach.login": [["log", "!parent"], ["portal", "!homework", "!announc", "!parent"]],
        "teach.list": [["class", "list"], ["roster"], ["list"]],
        "teach.homework": [["homework", "!see", "!child", "!parent", "!view"]],
        # (no parent.login: "parents see the homework when they log in" is one step, "See child homework"; the
        # v6.10 key asked for a separate login box the line doesn't describe)
        # the meeting names its own 4 parts ("trial lessons, group classes, renting instruments, and the teacher side"),
        # no parent part: announcements come up inside group classes ("how do they get updates, like if a class is
        # cancelled?") and parents seeing homework inside the teacher side. Either row is right (v6.11 bench drew them
        # there; v6.10 drew a "Parent portal" row: also right).
        "teach|parent.homework": [["homework", "see"], ["homework", "child"], ["homework", "view"]],
        "class|parent.announce": [["announc"]],
    },
    "links": [["trial.book", "trial.instrument"], ["trial.instrument", "trial.day"], ["trial.day", "trial.email"],
              ["trial.email", "trial.call"], ["class.timetable", "class.pass"], ["class.pass", "class|teach.attend"],
              ["rent.form", "rent.stock"], ["rent.stock", "rent.pickup"], ["rent.pickup", "rent.return"],
              ["teach.login", "teach.list"]],
    "cross_ok": [["trial", "class"], ["class", "teach"], ["teach", "class"], ["teach", "parent"]],
    "gaps": [{"name": "instrument out of stock", "any": [["stock"], ["availab"]]},
             {"name": "how parents pay for the class pass", "any": [["pay"]]}],
    "no_gaps": [{"name": "after the rental form (next line says)", "any": [["after", "form"], ["after", "submit"]]},
                {"name": "after the trial (answered)", "any": [["after", "trial"]]},
                {"name": "who marks attendance (answered)", "any": [["who", "attend"]]},
                {"name": "how rental is paid (answered)", "any": [["pay", "rent"]]}],
    "conflicts": [{"name": "rental fee 15 vs 12", "numbers": [["15", "12"], ["fifteen", "twelve"]]}],
    "no_conflicts": [{"name": "pass vs single class", "numbers": [["180", "20"], ["one eighty", "twenty"]]},
                     {"name": "10 classes vs 12 weeks", "numbers": [["10", "12"], ["ten", "twelve"]]}],
    "stale": [{"old": ["whatsapp"], "new": ["portal"]}],
}

# ---------------------------------------------------------------- 3. banking app release sync (updates, bugs)
RELEASE = [
    ("A", "Morning everyone, quick release sync for version four point two."),
    ("A", "Nimal, how is the transfer limits API?"),
    ("B", "That's done, it's merged and QA tested it yesterday."),
    ("A", "Great. Sachini, biometric login?"),
    ("C", "Biometric login is in progress, the iOS part is done but Android still needs the fallback to PIN."),
    ("A", "Okay. Let's walk through the send money flow once more because QA found issues there."),
    ("C", "Sure. So the user taps send money, then picks a payee from the list or adds a new one."),
    ("C", "Then they enter the amount and see a review screen with the fee."),
    ("C", "On the review screen they confirm and then"),
    ("C", "then we send an SMS OTP and they type it in."),
    ("B", "Mm-hmm."),
    ("C", "If the OTP is right they get the success screen and the receipt is saved in history."),
    ("A", "And wrong OTP?"),
    ("C", "Three wrong attempts and the account is locked for thirty minutes."),
    ("A", "Okay."),
    ("C", "But there's a bug, on Android twelve the OTP screen freezes when the SMS arrives."),
    ("B", "Yeah I saw that, it's the auto read permission, I'm on it."),
    ("A", "Okay, Nimal please fix that before Thursday."),
    ("B", "Will do, Thursday."),
    ("A", "And adding a new payee, how does that work?"),
    ("C", "They type the account number and the bank, and we show the account name to check it's the right person."),
    ("A", "Okay good."),
    ("C", "Then they save the payee and continue to the amount screen."),
    ("A", "Right. Release date is still the fourteenth, Friday."),
    ("B", "Should be fine if the OTP bug is fixed."),
    ("A", "Also, security team asked something, instead of the SMS OTP they want in-app approval with a push notification."),
    ("C", "In-app approval, okay, that changes the confirm step."),
    ("A", "Yeah, but only from the next version, not this one."),
    ("B", "Okay, by the way I just pushed the fix for the Android twelve freeze, it's in build four two one."),
    ("C", "Nice, I'll test it after the meeting."),
    ("A", "And the lock time, security says one hour after three wrong attempts, not thirty minutes."),
    ("C", "Hmm, okay we need to check that with them."),
    ("A", "Okay that's it, thanks everyone."),
]
RELEASE_KEY = {
    "steps": {
        "send.start": [["send", "money"], ["send"]],
        "send.payee": [["payee"]],
        "send.amount": [["amount"]],
        "send.review": [["review"]],
        "send.otp": [["otp"], ["approv"], ["confirm"]],
        "send.success": [["success"], ["receipt"]],
    },
    "links": [["send.start", "send.payee"], ["send.payee", "send.amount"], ["send.amount", "send.review"],
              ["send.review", "send.otp"], ["send.otp", "send.success"]],
    "cross_ok": [],
    "gaps": [{"name": "transfer fails at the bank / wrong account", "any": [["fail"], ["wrong", "account"], ["reject"]]}],
    "no_gaps": [{"name": "after the review screen (next line says)", "any": [["after", "review"]]},
                {"name": "wrong OTP (answered)", "any": [["otp", "wrong"], ["wrong", "attempt"]]},
                {"name": "after success (answered)", "any": [["after", "success"]]}],
    "conflicts": [{"name": "lock time 30 min vs 1 hour", "numbers": [["30", "hour"], ["thirty", "hour"], ["thirty", "one"]]}],
    "no_conflicts": [{"name": "3 attempts vs 30 minutes", "numbers": [["3", "30"], ["three", "thirty"]]}],
    "stale": [],
    "bugs": [{"name": "Android 12 OTP freeze", "any": [["android"], ["freez"]], "fixed": True}],
}

MEETINGS = {
    "grooming_app": {"title": "Pet grooming app", "lines": build(GROOMING), "key": GROOMING_KEY},
    "music_school": {"title": "Music school website", "lines": build(MUSIC), "key": MUSIC_KEY},
    "release_sync": {"title": "Banking release sync", "lines": build(RELEASE), "key": RELEASE_KEY},
}

# ---------------------------------------------------------------- 4. the 32-min Vimeo meeting (real ASR, v6.5 run)
# The transcript is someone else's recording: it is NOT in git. bench.py reads it from bench/private/leetcode_site.json
# (made from a debug json with:  python -m bench.bench --extract debug/<file>.json leetcode_site).
LEETCODE_KEY = {
    "steps": {
        "practice.meet": [["meet", "!time"], ["practic", "daily"], ["practic", "question"]],
        "practice.calibrate": [["calibrat"]],
        "practice.evaluate": [["evaluat", "!self"]],
        "practice.metrics": [["metric"], ["track", "!coach"], ["note"]],   # v6.9: not "Select coaching track"
        "practice.weak": [["weak"]],
        "daily.slack": [["slack"], ["subscrib"], ["daily", "question"]],   # "Receive daily question" = same step
        "daily.solve": [["solv"]],
        "daily.critique": [["critiqu"], ["peer"]],
        "behav.view": [["behavior"]],
        "behav.price": [["pric"]],
        "behav.how": [["how"]],
        "behav|sysd.schedule": [["calendly"], ["schedul", "session"], ["schedul", "coach"]],
        "behav|sysd.timeframes": [["time", "frame"], ["timefram"], ["availab"]],
        "behav.questions": [["question", "page"], ["see", "question", "!daily"], ["question", "list"], ["trello"], ["question", "form"],
                            ["get", "question", "!daily"],
                            ["view", "question"], ["access", "question"]],
        "behav.email": [["email"]],
        "behav.stories": [["stor", "writ"], ["writ"], ["creat", "stor"]],
        "behav.map": [["map"], ["tag"]],
        "behav.submit": [["submit"], ["share"], ["send"]],
        "behav.review": [["review"], ["feedback"], ["sync"]],
        "behav|sysd.mock": [["mock"]],
        "sysd.view": [["system"], ["design"], ["four", "session"], ["four", "question"]],   # v6.14: "Learn four questions"
        "sysd.practice": [["practic", "session"]],
        "net.register": [["regist"]],
        "net.network": [["network"], ["placement"]],
    },
    "links": [["practice.meet", "practice.calibrate"], ["practice.calibrate", "practice.evaluate"],
              ["practice.evaluate", "practice.metrics"], ["practice.metrics", "practice.weak"],
              ["daily.slack", "daily.solve"], ["daily.solve", "daily.critique"],
              ["behav.view", "behav.price"], ["behav|sysd.schedule", "behav|sysd.timeframes"],
              ["behav.questions", "behav.stories"], ["behav.stories", "behav.map"], ["behav.map", "behav.submit"],
              ["behav.review", "behav|sysd.mock"], ["sysd.view", "sysd.practice"], ["net.register", "net.network"]],
    "cross_ok": [["sysd", "behav"], ["behav", "sysd"]],      # "Do mock interviews" / Calendly can be shared by both
    "gaps": [{"name": "how people pay ($100 / sessions)", "any": [["pay"], ["charg"]]}],
    "no_gaps": [{"name": "after solving the daily question (next line: peers critique)", "any": [["after", "solv"]]},
                {"name": "who reviews the answers / board / form (12:02, 24:15)", "any": [["who", "review"]]},
                {"name": "after registering (tap into the network)", "any": [["after", "regist"]]},
                {"name": "after the review sessions (mock interviews)", "any": [["after", "review", "session"]]},
                {"name": "after scheduling (we get back with the exact time)", "any": [["after", "schedul"], ["calendly"]]},
                {"name": "how they get the questions (page / form / email)", "any": [["how", "get", "question"], ["access", "question"]]}],
    "conflicts": [{"name": "package 5 sessions $1000 vs 7 sessions $1400", "numbers": [["five", "seven"], ["5", "7"], ["1,000", "1400"], ["1000", "1400"]]},
                  {"name": "system design single session 250 vs 200", "numbers": [["250", "200"]]}],
    "no_conflicts": [{"name": "100 questions vs 500 done", "numbers": [["100", "500"]]},
                     {"name": "$100 daily question vs $200 session", "numbers": [["100", "200"]]},
                     {"name": "15 stories vs 40/50 questions", "numbers": [["15", "40"], ["15", "50"], ["fifteen", "fifty"]]}],
    "stale": [{"old": ["trello"], "new": ["form", "page"]}],
}
