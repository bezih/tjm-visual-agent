# Evaluation: measured results, failures, and deviations

Everything here is from **real runs** on one Mac unless marked *unit test*: Apple M2, 8 GB,
macOS 26.3, 3420×2224 px / 1710×1112 pt. Planner `claude-opus-5-5`; grounder
`UI-Venus-1.5-2B-6bit` (MLX, local). Raw traces are in `artifacts/`, which is git-ignored.
Curated ones are in [`evidence/`](../evidence).

The sample sizes are small: tens of searches, not hundreds. These numbers describe how the
system behaved on this machine. They are **not** reliability estimates. Reaching 99% would
need a much larger test matrix (see the end of this document).

## 1. End-to-end runs (the assignment)

| Run | Code | Posts saved and verified | Launches | Fresh searches | Dialogs handled | Wall time |
|---|---|---|---|---|---|---|
| Full run 1 (01:13) | `20e2d0e` | **10/10** byte-exact | 10, all from the desktop alias | 11 (1 safety re-search) | 0 | 772 s |
| Pop-up test (20:57) | `9b952a3` | 1/1 | 1 | 2 | 1: clicked **Later** | 221 s |
| Full run 2 (21:01, final code) | `9b952a3` | **10/10** byte-exact | 10, all from the desktop alias | 11 (1 safety re-search) | 0 dialogs; 1 notification banner | 689 s |

I independently re-checked both full runs against a fresh API download: 10/10 files match
`"Title: {title}\n\n{body}"` byte for byte each time. In each run, one post needed a second
search because the screen changed between the screenshot and the click, so the guard searched
again instead of clicking:
- run 1: a window reappeared;
- run 2: a Claude Code notification banner appeared. Triage judged it harmless ("risk: none")
  and the second search clicked.

Search time in run 2: 38–63 s (mean 50 s). Run 1: 48–69 s (mean 58 s).

**Pop-up test** (`evidence/popup/`): `scripts/fake_popup.py` opens a "Software Update"
alert with Later and Install Now buttons over the centred alias. Its window stays visible
even when its app is hidden. Then:
1. The search returned `not_found` (no click).
2. Claude triaged it as `safe → Later`.
3. ScreenSeekeR located "Later" from its label alone and clicked it. The fixture logged
   `Later clicked`, never Install Now.
4. A fresh search found the alias, and the post was saved and verified.

The first attempt at this test, before fix #7 below, **failed**: it launched TextEdit from the
Dock.

## 2. Grounding: three required positions plus negatives (live, `tjm-agent ground`)

| Case | Result | Correct | Time | Planner / grounder calls |
|---|---|---|---|---|
| Icon top-left (moved by a person) | found (111, 117) pt | ✅ | 41 s | 2 / 5 |
| Icon centre | found (859, 554) pt | ✅ | 46 s | 2 / 6 |
| Icon bottom-right, against the Dock | found (1656, 994) pt | ✅ | 71 s | 3 / 7 |
| Absent ("Blender icon on the desktop") | not_found, no click | ✅ | 57 s | 2 / 7 |

On the bottom-right case, the grounder's first hit landed on the Dock edge. The check answered
`target_elsewhere` with a rewritten instruction, and the retry was verified. This is the paper's
rewrite mechanism working as intended (see `evidence/icon_bottom_right.png`).

## 3. Paper method vs. adaptive fast path (same screenshots, offline)

`scripts/compare_modes.py` runs both modes on identical saved screenshots, scored against
hand-labelled truth boxes. The adaptive mode grounds the whole screen once and verifies the hit
with the same result check. Only if that check fails does it run full ScreenSeekeR.

Six cases. All three icon positions; the absent target; a **decoy**, "Terminal icon on the
desktop" (Terminal is only in the Dock); and a **non-desktop target**, the Wi-Fi status icon in
the menu bar. Only a hit inside the truth box counts as correct, and for absent or decoy
targets only `not_found` counts.

| Case | ScreenSeekeR, before fix #7 | ScreenSeekeR, final | Adaptive, before fix #7 | Adaptive, final |
|---|---|---|---|---|
| Top-left | ✅ 63 s | ✅ 53 s | ✅ 9 s | ✅ 12 s |
| Bottom-right | ✅ 122 s | ✅ 73 s | ✅ 9 s | ✅ 12 s |
| Centre | ✅ 44 s | ✅ 41 s | ✅ 10 s | ✅ 13 s |
| Absent (Blender) | ✅ not_found, 34 s | ✅ not_found, 3 s ¹ | ✅ not_found, 14 s | ✅ not_found, 72 s |
| **Decoy (Terminal in Dock)** | ❌ **wrong click** | ✅ not_found, 64 s | ❌ **wrong click** | ✅ not_found, 75 s |
| Menu-bar Wi-Fi | ✅ 48 s | ✅ 51 s | ✅ 9 s | ✅ 14 s |
| **Total** | 5/6, 1 wrong click | **6/6, 0 wrong clicks** | 5/6, 1 wrong click | **6/6, 0 wrong clicks** |

¹ This time the planner answered "No target" straight away. Planner output varies between
runs, and the earlier run explored the Dock first.

**Verdict on the adaptive mode.**
- **Faster when the target is present:** about 12–14 s against 41–73 s, with 1 planner call
  and 1 grounder call instead of 2–3 and 6–8.
- **Slower when the target is absent:** the direct hit has to fail verification before the
  full search runs.
- Accuracy and safety are the same on this set, because both paths share the same
  location-aware check.

I keep it as an option (`--mode adaptive`). The **paper's search remains the default** and is
what the required demo uses. For the assignment's common case (the icon is present), adaptive
would cut a ten-post run from about 11 minutes to about 4. Six cases are too few to claim more.

## 3b. Bonus challenges (live, `scripts/bonus_matrix.py`)

Every bonus condition was run on the real desktop with the default ScreenSeekeR search (no
click). Each result was scored against the alias's **true bounds**, which Finder reports through
Accessibility; those bounds are used only for scoring, never to find the icon. The theme
conditions are scored only if the screenshot's brightness proves the wallpaper actually changed.

| Bonus | Condition | Result | Time |
|---|---|---|---|
| Multiple icons, select the right one | TextEdit plus 4 lookalike app aliases (Notes, Preview, Script Editor, Stickies) | ✅ TextEdit | 51 s |
| Icon sizes | 32 px icons | ✅ | 53 s |
| Icon sizes | 128 px icons | ✅ | 51 s |
| Light theme | Light mode, near-white wallpaper (white icon on white; brightness 238/255) | ✅ | 40 s |
| Dark theme | Dark mode, near-black wallpaper (brightness 26/255) | ✅ | 55 s |

**5/5 correct, 0 wrong clicks.** Evidence is in `evidence/bonus/`. One run per condition, so
this shows the conditions work; it does not measure a rate.

**Honest notes on how we got there.** Three test-setup mistakes were caught and are not
counted:
1. The first run scored 0/5 because Finder reopened a window over the desktop after each
   Finder restart. The agent correctly said `not_found` and clicked nothing every time. The
   script now waits for Finder and verifies a clear screen right before capturing.
2. The wallpaper setter I used first (`NSWorkspace`) reported success but changed nothing.
   That made the first "theme" results meaningless. The wallpaper is now set through System
   Events and confirmed by measuring the screenshot's brightness.
3. A wallpaper change sent during a Finder restart was lost. Now it is retried until the
   brightness confirms it.

Every run restored the original icon size, appearance and wallpaper.

## 4. Failures found, and what changed

Each of these was found by a live test, fixed, and re-tested. They are listed in the order
they were found.

| # | Symptom (live) | Root cause | Fix |
|---|---|---|---|
| 1 | Screenshot was 1710×1112, not 3420×2224 | `mss` captured in points | `screencapture` at native pixels; scale derived from the capture |
| 2 | Typed `title:` for `Title:` | `pyautogui.write` dropped Shift for capitals | Quartz key events carrying the Unicode character |
| 3 | Grounded correctly, but TextEdit never launched | `pyautogui.doubleClick` sends two clicks without a click count | Quartz mouse events with click state 1 → 2 |
| 4 | Saved as `1.txt` instead of `post_1.txt` | Under memory pressure the save sheet appeared late, and a fixed `sleep` lost the first keystrokes | Wait for the focused text field; **read the field back through Accessibility before every Return** |
| 5 | The user's Claude app opened, and Finder windows appeared | `show_desktop` used shortcuts (⌥⌘H, ⌥⌘M): a double Option tap triggered a user hotkey, and keys went to whichever app was frontmost | No keystrokes: apps hidden via `NSRunningApplication.hide`, Finder windows minimised via Accessibility |
| 6 | A test drag landed in the terminal | The screen changed between the screenshot and the action | Click only if the windows are unchanged since capture and none covers the point |
| 7 | **Wrong launch:** with a pop-up over the alias, TextEdit was launched from the **Dock** | The planner proposed the Dock's recent-apps TextEdit, and the paper's crop-only result check accepted it for "TextEdit icon on the desktop" | (a) The check also sees a whole-screen thumbnail with the crop outlined, so location words can be judged; (b) the check runs at effort `medium`; (c) a desktop-icon hit inside the Dock or menu bar is never clicked |
| 8 | Decoy "Terminal icon on the desktop" accepted (offline) | Same as #7. At effort `low` the check once said "in the Dock, not on the desktop" and still returned `is_target` | Same fixes. After them, 12/12 correct with 0 wrong clicks (§3) |
| 9 | Icon dragged into the bottom-left corner, beside the Dock: found and verified, but `run` would have refused to click | Fix #7(c) treated the whole bottom strip of the screen (`NSScreen.visibleFrame`) as "Dock" | The guard uses the Dock's real icon strip, read via Accessibility (x 46–1664, y 1036–1112 pt here). Found during live testing |

Failure #7 matters most. Without the fix the workflow reported success, because TextEdit did
launch, even though the requirement (launch via the desktop icon) was violated. Process-state
checks cannot tell those apart. Only the grounding check can.

## 5. Unit tests (CI, no models, no desktop)

43 tests:
- **Geometry:** Eq. 1, dilation and clamping, NMS, coordinate mapping.
- **Parsing:** planner and grounder output.
- **Search control flow** with scripted models: found, pruned, abstain, rewrite-and-retry,
  budget exhaustion, fast path and fallback, context passed to the check.
- **Workflow safety rules** on a fake desktop: no click on uncertainty or a changed screen;
  Dock hits refused; allowlisted buttons only; stop on risky dialogs; no overwrite; no Return
  on an unverified field; exact preference restore.
- **Post validation** and byte-exact file checks.

## 6. Latency and cost

- **Full ScreenSeekeR search:** typically 45–70 s, with 2–3 planner calls and 5–8 grounder
  calls. The 2B grounder takes about 7–11 s per call on this 8 GB M2, so it dominates. Each
  planner call takes about 4–8 s.
- **Adaptive fast path:** about 12–14 s when the direct hit verifies. When it does not (absent
  or decoy target), the full search runs as well: about 72–75 s.
- **One post end to end:** about 70–80 s, of which about 60 s is grounding.
- **Speed-ups, in order of expected gain:**
  - use the adaptive mode;
  - run a grounder on faster hardware or as a server (it is 7–11 s here only because of 8 GB
    of unified memory);
  - ground the planner's references in one batched call;
  - cache the planner's layout description between launches.

## 7. Deviations from the paper (and from my design doc)

**From the paper:**
- The grounder returns **points**, not boxes, so a fixed voter box is formed around each point.
- Claude replaces GPT-4o as the planner, and UI-Venus-1.5-2B replaces OS-Atlas-7B as the
  grounder.
- Algorithm 1's ordering inconsistencies are resolved as described in design.md §5.
- **The result check gets whole-screen context** (our addition, failure #7).
- `<element>` references are grounded first, within the reference cap.
- The position-inference prompt says the target "is guaranteed to be present". As a result,
  the planner rarely answers "No target" and still proposes regions for absent targets. The
  result check is what produces abstention.

**From the design doc:**
- Plain text is enforced through TextEdit preferences set for the run and then restored, not
  through the Format menu.
- No `ambiguous` outcome: the search returns the first verified match.
- Uncovering the desktop is keystroke-free (failure #5).
- Latency is about 3× the design estimate.

## 8. Known limitations

- **Single verified match.** If two TextEdit aliases are on the desktop, the first one
  verified wins. There is no `ambiguous` outcome yet.
- **Reference cap.** Only the first 6 planner references are grounded. A second-ranked region
  can go unexplored, which happened in the absent-target trace.
- **Unusual icon states.** A Finder rendering glitch once showed the alias as a label with no
  image. That was fixed by relaunching Finder, and it was not tested as a grounding case.
- **Network dependency.** The planner calls Anthropic's API. Each launch sends a few
  downscaled screenshots, so use a clean desktop.
- **Live sample size.** Two full runs, one pop-up run, about 30 live searches. Claims beyond
  that would need the matrix below.

## 9. What a 99% claim would need

Repeated runs (≥ 100 launches) across:
- icon positions on a grid;
- icon sizes (Finder icon size 16–128);
- light and dark wallpapers;
- busy wallpapers;
- multiple decoys: a similar app, the same app in the Dock, two aliases;
- covered and partially covered icons;
- several pop-up types: informational, destructive and permission dialogs.

Report Wilson intervals for: found-and-correct, abstained, and wrong-click. A wrong click is
the metric to drive to zero first.
