# Design: Finding Desktop Icons with ScreenSeekeR

*TJM Labs take-home, Part 1. Results from building and testing are in
[evaluation.md](evaluation.md).*

## 1. What I'm building and how I'll know it works

The program has to find the Notepad icon on the desktop wherever it's been moved, open it, and
save ten blog posts from JSONPlaceholder as `post_1.txt` to `post_10.txt` in
`Desktop/tjm-project`. Before every launch it takes a new screenshot and finds the icon again
from scratch.

The part I care most about is finding things on screen. I'm building that as its own engine,
based on **ScreenSeekeR** from the ScreenSpot-Pro paper (Section 4.2, Algorithm 1, Appendix C).
You give it a screenshot and a plain-English description, and it either returns the
**centre (x, y) to click** or says it couldn't find it. It doesn't know anything about Notepad or blog posts, so the same
engine should work for any icon or button.

**It works if:**

1. All ten files exist and match `"Title: {title}\n\n{body}"` exactly.
2. Every launch starts from a fresh screenshot and a new search, with no reused coordinates.
3. It finds the icon wherever someone drags it (at least top-left, centre and bottom-right),
   and **it doesn't click** when the icon isn't there or it can't confirm what it found.
4. Every search leaves a trace I can look at afterwards.
5. The whole task runs from one command, so in the interview you can move the icon and just
   run `uv run tjm-agent run`.

## 2. My setup: macOS instead of Windows

The brief says Windows and Notepad at 1920×1080. I got permission to use my Mac (Apple M2,
8 GB, Retina screen), so this is what changes:

| Brief (Windows) | My version (macOS) | Why it's equivalent |
|---|---|---|
| Notepad shortcut on the desktop | **TextEdit alias** on the desktop | Same idea: a plain-text editor opened from a desktop icon |
| 1920×1080 | Screenshot is 3420×2224 pixels, but clicks use 1710×1112 points | Actually harder: I have to convert between the two (2×) correctly |
| Ctrl+S, save dialog | ⌘S, save sheet | Same: type the filename and folder into the dialog |
| Close Notepad | Quit TextEdit | Forces the next post to find the icon again |

Some rules I'm holding myself to:

- I find the app **only from screenshots**: no Spotlight, no `open -a`, no saved picture of the
  icon, no accessibility shortcuts.
- Files are created by typing and saving in the app. Reading them back from disk is fine, but
  only to *check* the result.
- TextEdit likes to auto-correct and auto-capitalise, and to save as rich text. I have to make
  sure it saves exactly what I typed, as plain text, and check it byte for byte.
- macOS needs Screen Recording and Accessibility permissions. A `doctor` command checks these
  before anything runs.

## 3. Why ScreenSeekeR and not something simpler

The brief asks for something that works "for any icon or button even if we don't have the
exact image or text beforehand" and that can deal with surprise pop-ups. That rules out the
usual shortcuts:

- **Matching a saved picture of the icon** breaks as soon as the size, theme or highlight
  changes, and it can't find anything it hasn't seen before.
- **Reading text on screen (OCR)** doesn't help with icons and buttons that have no label.
- **Asking one AI model "where is it?"** works on simple screens but falls apart on big, busy
  ones. That's the main finding of the paper: the best model found only 18.9% of targets.

ScreenSeekeR searches the way a person would. A strong AI model (the **planner**) looks at the
screen and reasons about where the target probably is, and what should be next to it: "it's in
the column of desktop icons, near those folders." A smaller model that's trained on screens
(the **grounder**) pinpoints those things. The search then zooms into the most promising area,
and the planner double-checks the result before anything gets clicked. In the paper this took
a specialist model from 18.9% to 48.1%, with no extra training.

Finding a desktop icon is the easy case, and the brief says so. The point is that the same
engine can also find a "Cancel" button in a pop-up it has never seen.

## 4. Which models

- **Planner:** Claude, through the Anthropic API. The paper used GPT-4o; any strong model that
  can read screenshots fits this role. I use the paper's prompts word for word.
- **Grounder:** UI-Venus-1.5-2B, running locally on my Mac. The paper used OS-Atlas-7B, but a
  7B model doesn't fit well in 8 GB of memory. UI-Venus is small, trained specifically for
  finding things on screens (57.7% on ScreenSpot-Pro by itself), and it can say "not here".

Only one file talks to the models, so either one can be swapped out later. Screenshots go to
Anthropic for the planning step, so I'll run the demo on a cleaned-up desktop.

## 5. How the search works, mapped to the paper

```
Search(target, area, depth):
  if the area is small enough or we're 3 levels deep:  pinpoint the target, then verify it
  plan    = Planner: where is it likely to be, and what's nearby?
  points  = Grounder: locate each thing the planner mentioned
  areas   = turn each point into a zoom area, score it (paper's formula), drop overlaps, sort
  for each area, best first: Search(target, that area, depth + 1); stop when one is verified
```

| Step | What I do | Same as the paper? |
|---|---|---|
| Plan (Table 7 prompt) | Read the `<element>`, `<area>` and `<neighbor>` tags from the planner's answer | Prompt used word for word |
| Locate references | The grounder returns a point, and I draw a small box around it | **My change:** the paper's grounder returns boxes, mine returns points |
| Expand into zoom areas | Grow each box to about 1280 px so there's context, at most half the current view, kept on screen | The paper doesn't give the max size, so I picked half to make sure it always zooms in |
| Score (formula 1–2) | Areas with more located points near their centre score higher (σ = 0.3) | Same formula, with unit tests |
| Remove overlaps | Standard non-max suppression (overlap 0.5) | The paper's pseudocode lists the steps in an order that doesn't quite work; I do expand → score → remove overlaps → sort |
| Recurse | Depth-first, at most 3 levels | The pseudocode resets the depth counter inside the loop; I read that as a typo |
| Verify (Table 8 prompt) | Draw a red box on the guess and ask the planner: target, elsewhere, or not found? | Same. "Elsewhere" retries with the planner's rewritten description; only "target" ends the search |

**Coordinates.** I keep everything in screenshot pixels and convert to click points in one
place only (divide by 2 on this screen). This is easy to get wrong, so it has its own tests.

**Limits.** Each search has a cap on depth, AI calls and total time. I don't trust a model's
own confidence score. Only the planner's explicit "yes, that's it" allows a click.

## 6. The workflow

The steps for saving a post are always the same, so a plain step-by-step program runs them. AI
is only used where I need to *find something on screen*. I fetch the posts once and check they
look right, then for each post:

1. **Pre-check:** TextEdit is closed and the output file doesn't exist yet. If not, stop. I
   won't touch anyone's open documents or overwrite files.
2. **Find the icon:** clear the desktop, take a fresh screenshot, search for "TextEdit
   application icon on the desktop".
3. **Launch:** double-click it, then confirm TextEdit actually opened with a new, empty
   document.
4. **Type** the post once. No blind retyping, since that could duplicate text.
5. **Save:** ⌘S, then the filename, then go to `~/Desktop/tjm-project`, then confirm.
6. **Check** the file on disk: right name, right folder, exact content.
7. **Quit** TextEdit and make sure it closed before the next post.

## 7. When things go wrong

| Problem | What it does |
|---|---|
| Icon moved | Normal case: every launch searches again |
| Icon missing or covered | "Not found" means no click. It checks whether something is covering the icon, then tries again. After the allowed attempts it stops and saves what it saw |
| Wrong thing opened | If TextEdit isn't the app that opened, it closes only what it opened and searches again |
| Surprise pop-up | The planner describes the pop-up and how risky it is. If there's a safe button from an approved list, the engine finds it **by its label** and clicks it. Anything risky, permission-related or unclear: stop |
| Save dialog issues, or the file already exists | Never overwrite. Buttons are found by description, never by fixed coordinates |
| AI or network errors | A few retries. A garbled answer counts as a failure, never as a guess |
| Saved text doesn't match | Mark the post failed and keep the file so I can look at it |

## 8. Testing and proof

- **Automated tests** (run on every push): coordinate maths, the scoring formula, the search
  logic using fake models, and the workflow rules using a fake desktop.
- **Live tests on my Mac:** icon top-left, centre and bottom-right; icon missing; look-alike
  icons; the full ten-post run.
- **Evidence:** annotated screenshots in `evidence/`, with a note of which code, models and
  settings produced them. I'll keep live results and fake-model tests clearly separate.

## 9. Trade-offs and what I'd do later

- **Speed vs. robustness.** A full search makes a few planner calls and several grounder calls,
  so it takes tens of seconds instead of a couple. For ten launches that's fine, and it's the
  price of a method that holds up on busy professional screens.
- **Possible speed-up, only once the basics work:** try one direct guess first, verify it with
  the paper's own check, and fall back to the full search if the check fails. I'd measure both
  on the same screenshots and only keep it if it actually helps.
- **Bonus challenges.** Because nothing is matched against a saved image, different icon
  sizes and light or dark themes *should* just work, since the models see the icon the way a
  person does. I'll test them if there's time rather than assume. For several similar icons,
  the planner's neighbours and the verify step are what pick the right one.
- **Later:**
  - handle two matching icons (say "ambiguous" instead of picking one);
  - remember the screen layout between launches;
  - run the planner locally so there's no network dependency;
  - support multiple monitors;
  - port to Windows/Notepad, which would only need new screen and mouse code.
