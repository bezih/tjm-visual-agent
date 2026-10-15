# Design: Finding the Text Editor on the Desktop with ScreenSeekeR

*TJM Labs take-home, Part 1. Results from building and testing are in
[evaluation.md](evaluation.md).*

## 1. What it does and how I know it works

The program finds the text editor on the desktop, whether it's been moved somewhere else on the
screen, covered by a pop-up, or shown at a different size, colour or theme. It does that with an
**LLM** (Claude) and an **SLM** (a small local model trained on screens). They talk back and
forth until Claude confirms it found the right thing, or it comes back with "not found". If it's
unsure, it tries 3 times. If nothing comes out of it, it does nothing.

Once it finds the icon, it opens the editor, types each blog post from the JSONPlaceholder API,
saves it, and closes the editor. Then it starts over for the next post with a fresh screenshot.
The finding part doesn't know anything about TextEdit or blog posts. You describe a target in
plain English and it returns the **centre (x, y) to click**, so it works for other icons and
buttons too.

**I know it's done and correct when:**

- the first 10 posts are typed into the editor and structured properly:
  `Title: {title}`, a blank line, then the body;
- they're saved properly as `post_1.txt` … `post_10.txt` in `Desktop/tjm-project`, checked
  byte for byte;
- it still works when the icon moves, the UI changes, or a pop-up shows up;
- it doesn't click anything when it can't confirm the target;
- the whole thing runs from one command: `uv run tjm-agent run`.

I'll test it, then patch the failures and bugs I find.

## 2. My setup: macOS instead of Windows

The brief says Windows at 1920×1080. I asked and got permission to use my Mac, because that's
the machine I have (Apple M2, 8 GB).

| Brief | What I use | Note |
|---|---|---|
| Notepad shortcut on the desktop | TextEdit alias on the desktop | Same job: a text editor launched from a desktop icon |
| 1920×1080 | 3420×2224 screenshot, 1710×1112 click points | The resolution is double, so there's a conversion factor |
| Ctrl+S | ⌘S | Same save dialog: filename plus folder |
| Close Notepad | Quit TextEdit | So the next post has to find the icon again |

The resolution being double actually makes it a bit harder. The screenshot is in pixels but the
mouse clicks in points, so every location has to be divided by 2 before clicking. Mix them up
and every click lands in the wrong place, so I keep that conversion in one spot and test it. The
bigger screenshot also has to be shrunk before the models see it, which is one reason zooming
in helps.

Rules I'm sticking to:

- It finds the editor **only from screenshots**: no Spotlight, no `open -a`, no saved picture of
  the icon.
- Posts are typed and saved through the app. Reading the file afterwards is only to check it.
- TextEdit auto-corrects and saves rich text by default, so I turn that off for the run and put
  my settings back afterwards.

## 3. Why ScreenSeekeR and not template matching

UI changes frequently. Icons come in different sizes, light and dark themes, highlighted when
selected, redesigned in new versions. On top of that, sometimes we get unexpected pop-ups that
need reasoning, not matching. Template matching is only good if things stay constant, and it
can't find something it has never seen, like a button on a pop-up. Reading text on screen (OCR)
doesn't help with icons that have no label.

The brief also says the approach is *intentionally* overkill for Notepad, because the point is
general grounding that works for any icon or button. That's what ScreenSeekeR is for. It comes
from the ScreenSpot-Pro paper (Section 4.2, Algorithm 1, Appendix C). It searches the way a
person would: think about where something probably is, look there, zoom in, and double-check
before clicking.

## 4. Why two models

Each model is good at the opposite thing:

- **The LLM (Claude)** understands the screen and can reason, e.g. "desktop icons have labels
  under them, Dock icons don't". But it's bad at giving exact pixel positions. In the paper,
  GPT-4o on its own found under 1% of targets.
- **The SLM (UI-Venus-1.5-2B, running locally)** was trained on lots of screenshots, so it's
  precise at pointing. But it gets lost on big, busy screens, and it will point at *something*
  even when the target isn't there.

So Claude plans where to look and checks the answer, and the SLM does the pointing. The SLM
never reads Claude's whole answer: my code pulls out each thing Claude mentions ("TextEdit
icon", "checkin folder") and asks the SLM about them one at a time. In the paper, combining them
took accuracy from 18.9% to 48.1% with no extra training. The paper used GPT-4o and OS-Atlas-7B;
a 7B model doesn't fit well in my 8 GB, so I use Claude plus a smaller screen model, and the
paper's prompts word for word.

## 5. How the search works

Screenshot → **Claude** says where the target probably is and what's near it → **SLM** locates
each of those things → the program turns each one into a zoom area and scores it (the paper's
formula: more located points near the centre means a higher score) → zoom into the best area →
**SLM** pinpoints the target → the program draws a red box on it → **Claude** checks it and
answers one of:

- **yes** → good to click;
- **no, it's somewhere else**, plus a clearer description → the SLM tries again with that
  description;
- **not found** → move to the next zoom area, or give up and don't click.

Where I differ from the paper, and why:

- **Points instead of boxes.** The paper's grounder gives boxes, mine gives points, so I draw a
  small box around each point. It doesn't matter in practice, because every box gets enlarged
  into a zoom area next.
- **Step order.** The paper's pseudocode scores the zoom areas on one line but only creates
  them on the next, so it can't run as written. Removing overlapping areas also needs the
  scores, because you keep the better one. So I go: enlarge → score → remove overlaps → sort.
- **Depth counter.** The pseudocode resets it to 0 inside the loop; I read that as "start at 0".
- **Limits.** Every search has a cap on depth, AI calls and time, so it can't run forever.

## 6. One post, start to finish

1. Type the command in the terminal.
2. Hide the open windows so the desktop is visible → take a screenshot → run the search above.
3. Found and confirmed → double-click → check TextEdit actually opened a new, blank document.
4. Type the post exactly: `Title: {title}`, a blank line, the body.
5. ⌘S → type `post_{id}.txt` → go to `Desktop/tjm-project` → save.
6. Open the saved file and check it matches word for word.
7. Quit TextEdit → back to step 2 with a fresh screenshot for the next post.

**What it should never do:**

- click when it's unsure;
- overwrite a file that's already there;
- touch apps or documents I already had open (if TextEdit is already running, it stops);
- retype blindly;
- press anything on a pop-up except a safe button like "Later" or "OK";
- reuse old coordinates.

## 7. When things go wrong

| What happens | What it does |
|---|---|
| Icon moved | Normal: every post starts from a new screenshot and search |
| Icon covered by a pop-up | Not found → no click → Claude looks at the screen and describes the pop-up → if there's a safe button ("Later", "OK", "Cancel"), the search finds that button **by its label** and clicks it → search again. Risky, permission or unclear → stop |
| Still not found after 3 tries | Stop, do nothing, save screenshots of every attempt so I can see why |
| Wrong thing opened | Close only what it opened and search again |
| File already exists | Stop, never overwrite |
| AI or network error | A few retries; a garbled answer counts as a failure, never a guess |
| Saved text doesn't match | Mark that post failed and keep the file to look at |

## 8. Testing

- **Live on my Mac:** move the icon to the top-left, centre and bottom-right; take it away
  completely; add lookalike icons; small and large icon sizes; light and dark themes; a fake
  pop-up over the icon; the full 10-post run. Annotated screenshots of each go in `evidence/`.
- **Automated tests:** the maths, the scoring, the search logic and the safety rules, using
  fake models and a fake desktop, so they run anywhere.
- Then test it, patch the failures and bugs, and test again.

## 9. Speed and trade-offs

- **Speed.** A search makes a few Claude calls and several SLM calls, so I expect tens of
  seconds per search instead of a couple, slower if there's a pop-up because there are more
  steps. For ten launches that's fine. It's the price of something that works on any screen.
- **Making it faster:**
  - try a quick direct guess first, checked by Claude, and fall back to the full search only if
    the check fails;
  - run the SLM on a GPU server;
  - batch the SLM calls;
  - use a faster model for planning but keep the strong one for the final check.

  (Bigger models would be slower, not faster.)
- **Later:**
  - handle two identical icons by saying "ambiguous" instead of picking one;
  - remember the layout between launches;
  - run the planner locally;
  - support multiple monitors;
  - port to Windows/Notepad, which only needs new screen and mouse code.
