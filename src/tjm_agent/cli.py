"""Command-line entry point: `tjm-agent doctor | ground | run`."""

from __future__ import annotations

import argparse
import os
import sys

from . import config


def _engine(settings):
    from .grounding.backends import ClaudePlanner, VenusGrounder
    from .grounding.screenseeker import ScreenSeekeR

    planner = ClaudePlanner(model=settings.planner_model, effort=settings.planner_effort)
    grounder = VenusGrounder(repo=settings.grounder_repo)
    return ScreenSeekeR(planner, grounder, settings.search)


def cmd_doctor(args, settings) -> int:
    from . import desktop

    ok = True
    for name, granted in desktop.permissions().items():
        print(f"[{'ok' if granted else 'MISSING'}] permission: {name}")
        ok &= granted
    shot = desktop.capture()
    print(f"[ok] screenshot {shot.size[0]}x{shot.size[1]} px, scale {shot.scale} "
          f"(click space {shot.size[0] / shot.scale:.0f}x{shot.size[1] / shot.scale:.0f} pt)")
    key = bool(os.getenv("ANTHROPIC_API_KEY"))
    print(f"[{'ok' if key else 'MISSING'}] ANTHROPIC_API_KEY set (value not shown)")
    ok &= key
    print(f"[ok] planner {settings.planner_model} (effort {settings.planner_effort}); "
          f"grounder {settings.grounder_repo}")
    print(f"[ok] output dir {settings.output_dir}")
    return 0 if ok else 1


def cmd_ground(args, settings) -> int:
    from . import desktop, trace

    if not args.no_show_desktop:
        desktop.show_desktop()
    shot = desktop.capture()
    outcome = _engine(settings).search(shot.image, args.target)
    click_pt = desktop.point_for(*outcome.point_px, shot) if outcome.point_px else None
    run_dir = trace.new_run_dir()
    path = trace.save(run_dir, args.name, shot.image, outcome, click_pt, title=args.name)
    print(f"{outcome.status}: px={outcome.point_px} click_pt={click_pt} "
          f"({outcome.seconds:.1f}s, planner {outcome.planner_calls}, grounder {outcome.grounder_calls})")
    print(f"trace: {path}")
    if outcome.status == "found" and args.hover:
        desktop.move(*click_pt)
    return 0 if outcome.status == "found" else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tjm-agent")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check permissions, display mapping and configuration")
    g = sub.add_parser("ground", help="locate a target and save an annotated trace (no click)")
    g.add_argument("--target", default=None, help="natural-language description of the target")
    g.add_argument("--name", default="ground")
    g.add_argument("--hover", action="store_true", help="move the mouse to the result")
    g.add_argument("--no-show-desktop", action="store_true")
    args = p.parse_args(argv)
    settings = config.load()
    if getattr(args, "target", "") is None:
        args.target = settings.icon_target
    return {"doctor": cmd_doctor, "ground": cmd_ground}[args.command](args, settings)


if __name__ == "__main__":
    sys.exit(main())
