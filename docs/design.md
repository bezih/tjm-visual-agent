# Design: ScreenSeekeR Visual Grounding for Desktop Automation

*TJM Labs take-home, Part 1. Results from building and testing are in
[evaluation.md](evaluation.md).*

## 1. Goal and success criteria

The goal is a Python application that finds a desktop icon from a screenshot alone, given only
a natural-language description, and double-clicks it to launch the editor. Through the editor's
UI it then writes the first ten JSONPlaceholder posts to `Desktop/tjm-project/post_{id}.txt`,
grounding the icon again from a fresh screenshot before every launch. The grounding engine is
the focus. It implements **ScreenSeekeR** (Li et al., *ScreenSpot-Pro*, 2025: §4.2,
Algorithm 1, Appendix C) and is target-agnostic:
`(screenshot, "description of a UI element") → found(point) | not_found`.

**Success means:**

1. Ten files whose bytes equal `"Title: {title}\n\n{body}"` for the first ten API posts.
2. Every launch is preceded by a fresh screenshot and a full search; no coordinates are reused.
3. The icon is found wherever a person drags it, at minimum top-left, centre and bottom-right.
   The agent **declines to click** when the icon is absent or a hit cannot be verified.
4. Every search leaves an inspectable trace: planner output, candidates, crops and timings.

## 2. Assumptions: the macOS adaptation

The brief specifies Windows 10/11 at 1920×1080 with Notepad. With permission, I am building on
**macOS (Apple M2, 8 GB, Retina)**:

| Windows (brief) | macOS (this build) | Note |
|---|---|---|
| Notepad desktop shortcut | **TextEdit alias** on the desktop | Same role: a plain-text editor launched from a desktop icon |
| 1920×1080 | 3420×2224 px screenshot, 1710×1112 pt click space | Harder: pixels and click points differ by 2×, so coordinate mapping must be explicit |
| Ctrl+S / Save dialog | ⌘S / save sheet | Filename and folder are entered through the dialog |
| Close Notepad | Quit TextEdit | Forces the next launch to go through the icon again |

Other ground rules:

- The app is found only from screenshots: no Accessibility lookups, Spotlight, `open -a` or
  stored icon templates.
- Reading the saved file back from disk is allowed, but only to *verify* the result.
- TextEdit defaults to rich text and rewrites typed text (smart quotes, auto-capitalisation),
  so plain-text output must be enforced and checked byte for byte.
- The agent needs Screen Recording and Accessibility permissions. A `doctor` command checks
  them before anything runs.

## 3. Why ScreenSeekeR

The brief asks for a method that works "for any icon or button even if we don't have the
exact image or text beforehand" and that copes with unexpected pop-ups. That rules out the
usual primary methods:

- **Template matching** needs the exact image. It breaks with icon size, theme, Retina
  scaling and selection highlights.
- **OCR** fails on unlabelled icons and buttons.
- **Single-shot VLM grounding** generalises, but collapses on large, dense screens. That is
  ScreenSpot-Pro's central finding: the best model scored 18.9% when targets covered about
  0.07% of the screen.

ScreenSeekeR narrows the search the way a person does. A strong multimodal **planner** reasons
about where the target probably is ("in the desktop icon column, near other aliases"; "in the
dialog's button row, next to *Save*"). A specialist **grounder** localises those references,
and the search recurses into the most promising crop until it finds a hit it can verify.
Without any training, this lifts OS-Atlas-7B from 18.9% to 48.1%. A desktop icon is an easy
case, and the brief says so. The point is that the same engine also finds a "Cancel" button
in a pop-up it has never seen.

## 4. Models

- **Planner (paper: GPT-4o):** Claude Opus 5.5 via API. It runs position inference and result
  checking using the paper's prompts verbatim; any strong multimodal model can fill this role.
- **Grounder (paper: OS-Atlas-7B):** UI-Venus-1.5-2B, 6-bit MLX, running locally. A 7B model
  does not fit comfortably in 8 GB. UI-Venus is a GUI-grounding specialist, scoring 57.7% on
  ScreenSpot-Pro on its own per its model card, and it can refuse by answering `[-1,-1]`.

Both models sit behind two small interfaces, and only `grounding/backends.py` imports a model
SDK. Screenshots and crops are sent to Anthropic for planning and checking, so the demo uses a
cleared desktop.

## 5. Grounding engine: paper → code

```
VisualSearch(T, viewport, depth):
  if depth ≥ Dmax or viewport ≤ Smin:   return leaf(T, viewport)    # ground + verify
  refs   = Planner.position_inference(T, crop)    # <element> <area> <neighbor>
  voters = [Grounder.ground(r, crop) for r in refs]
  cands  = dilate(voters, Smin) → score (Eq. 1, σ=0.3) → NMS → sort
  for c in cands: if (hit := VisualSearch(T, c, depth+1)): return hit
  return None
```

| Mechanism | Implementation | Paper vs. ours |
|---|---|---|
| Position inference (Table 7) | Parse `<element>`, `<area>`, `<neighbor>` in order; "No target" short-circuits | Prompt verbatim |
| Grounding references | The grounder returns a point; we form a fixed-size voter box around it | **Ours:** the paper's grounder returns boxes; dilation dominates the size anyway |
| Dilation | Expand to `Smin` = 1280 px (the paper's leaf size), at most 50% of the viewport, kept inside the image | `Rmax` is undefined in the paper; the 50% cap guarantees narrowing |
| Scoring (Eq. 1–2) | `exp(−((x′−.5)²+(y′−.5)²)/2σ²)` if the voter centre is inside the candidate, summed over voters | Verbatim, unit-tested |
| NMS | Greedy, IoU 0.5 | Algorithm 1 scores `nms_patches` before defining them; we run dilate → score → NMS → sort |
| Recursion | Depth-first, `Dmax` = 3, leaf at ≤ 1280 px | Algorithm 1 resets `d ← 0` inside the recursion; we treat that as the initial call only |
| Leaf check (Table 8) | Red box on the crop; JSON verdict `is_target` / `target_elsewhere` / `target_not_found` | `target_elsewhere` retries with the rewritten instruction; only `is_target` ends the search |

**Coordinates.** Every viewport carries its rectangle in screenshot pixels, and model outputs
are converted immediately (`0–1000 grid → viewport px → screenshot px`). Only `geometry.py`
maps pixels to click points (÷ backing scale = 2.0). This is unit-tested and verified live.

**Budgets.** A search is capped on depth, planner calls (10), grounder calls (30) and wall-clock
time (150 s). Model confidence is never treated as a probability: only the planner's explicit
verdict permits a click.

## 6. Workflow

A deterministic state machine drives the known task. Grounding is called wherever a *screen
location* is needed; the model is never asked to re-plan the whole task. Posts are fetched once
and validated (integer id, non-empty ASCII title and body). Then, for each post:

1. **Pre-check:** TextEdit must not be running and the output file must not exist. If either
   fails, stop rather than touch the user's data.
2. **Ground:** uncover the desktop, take a fresh screenshot, and search for "TextEdit
   application icon on the desktop".
3. **Launch:** double-click the hit. Within a bounded wait, TextEdit must become frontmost with
   an empty `Untitled` document.
4. **Type** the text once. There are no blind retries, because retyping duplicates text.
5. **Save:** ⌘S, then the filename, then Go to Folder `~/Desktop/tjm-project`, then confirm.
6. **Verify** on disk: the right name and folder, with bytes equal to the expected text.
7. **Quit** TextEdit and confirm the process has exited before the next post.

## 7. Failure handling

| Failure | Response |
|---|---|
| Icon moved | Normal case: every launch searches again |
| Icon absent or covered | `not_found` → no click; triage the screen, then search again; stop with a trace after the attempts run out |
| Wrong thing opened | Frontmost app ≠ TextEdit → close only what we opened, then search again |
| Unexpected pop-up | The planner describes it and classifies the risk. A safe dismissal from an allowlist is located **by description** with ScreenSeekeR and clicked. Destructive, permission or unclear dialogs → stop |
| Save-dialog variants, file exists | Never overwrite; buttons are located by description, never by coordinates |
| API or model errors | Bounded retries; malformed output counts as a failure, never a guess |
| Content mismatch | Mark the post failed and keep the file for inspection |

## 8. Testing and evidence

- **Unit tests (CI):** geometry, Eq. 1, NMS, coordinate mapping, output parsing, search
  termination and budgets with scripted fake models, workflow transitions on a fake desktop.
- **Live tests (opt-in, real Mac):** icon top-left, centre and bottom-right; icon absent;
  decoy icons; the full ten-post run.
- **Evidence:** the curated annotated screenshots go into `evidence/` with a manifest (commit,
  models, config, timings). Live results and mocked tests are always reported separately.

## 9. Trade-offs and deferred work

- **Latency vs. generality.** A full search costs a few planner calls and several local
  grounder calls, which is tens of seconds compared with seconds for one-shot grounding. That
  is acceptable for ten launches, and it is the price of a method that holds up on dense
  professional UIs.
- **Planned optimisation, only once the baseline works:** ground the full screen once, verify
  it with the result-check prompt, and fall back to the full search when verification fails.
  This is measured on the same screenshots and kept only if it pays off.
- **Deferred:**
  - an `ambiguous` outcome for several verified matches;
  - caching the planner's layout description between launches;
  - a local planner, to remove the network dependency;
  - multi-monitor support;
  - a Windows/Notepad port, which needs only a new desktop executor and app constants.
