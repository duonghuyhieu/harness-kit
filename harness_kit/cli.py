"""CLI `harness`: audit / retrofit / install / update / uninstall / doctor / upgrade / install-skill / run."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import audit, installer, runtime, scaffold

KIT_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    runtime._configure_stdio()
    p = argparse.ArgumentParser(prog="harness", description="Retrofit legacy projects with a harness-engineering structure.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("audit", help="score the 5 subsystems, Fresh Session Test, clean state (read-only)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", help="write the report to a file")
    s.add_argument("--min-score", type=int, default=0, help="exit with code 1 if the score is lower (for CI)")

    s = sub.add_parser("retrofit", help="generate the harness file set (never overwrites existing files)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--level", choices=scaffold.LEVEL_ORDER, default="standard")
    s.add_argument("--dry-run", action="store_true", help="only print the plan")
    s.add_argument("--git-init", action="store_true", help="run git init if the project does not use git yet")
    s.add_argument("--commit", action="store_true", help="commit a checkpoint of the newly created harness files")
    s.add_argument("--claude", action="store_true",
                   help="also add the Claude Code layer (same as `install`): agents, /hk-* skills, hooks, status line, "
                        "least-privilege settings (off by default for retrofit)")

    s = sub.add_parser("install", help="install the harness + the Claude Code layer into a project (or --global)")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--level", choices=scaffold.LEVEL_ORDER, default="standard")
    s.add_argument("--global", dest="global_", action="store_true",
                   help="install skills, agents and the output style into ~/.claude for every project (no hooks)")
    s.add_argument("--python", help="command the hooks use to start Python (default: detected, e.g. python3 / py -3)")
    s.add_argument("--no-statusline", action="store_true", help="do not install the harness status line")
    s.add_argument("--force", action="store_true", help="replace harness files under .claude/ even if you changed them")
    s.add_argument("--dry-run", action="store_true", help="only print the plan")
    s.add_argument("--git-init", action="store_true", help="run git init if the project does not use git yet")
    s.add_argument("--commit", action="store_true", help="commit a checkpoint of the newly created harness files")

    s = sub.add_parser("update", help="refresh the Claude Code layer and the runtime of an installed project")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--python", help="change the command the hooks use to start Python")
    s.add_argument("--no-statusline", action="store_true")
    s.add_argument("--force", action="store_true", help="also replace harness files you changed")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("uninstall", help="remove the unchanged .claude/ harness files and the harness settings entries")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("doctor", help="check an installation: manifest, runtime, settings, hooks, python (read-only)")
    s.add_argument("path", nargs="?", default=".")

    s = sub.add_parser("upgrade", help="update scripts/harness.py to the latest runtime")
    s.add_argument("path", nargs="?", default=".")

    s = sub.add_parser("install-skill", help="install the /harness-retrofit skill for Claude Code")
    s.add_argument("--dest", default=str(Path.home() / ".claude" / "skills"),
                   help="skills directory (default ~/.claude/skills)")

    s = sub.add_parser("run", help="run the runtime on a project: harness run <path> <runtime command...>")
    s.add_argument("path")
    s.add_argument("rest", nargs=argparse.REMAINDER)

    args = p.parse_args(argv)

    if args.cmd == "audit":
        r = audit.run_audit(Path(args.path))
        text = audit.to_json(r) if args.json else audit.to_markdown(r)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"Wrote {args.out} — score {r['score']}/100, bottleneck {r['bottleneck']}.")
        else:
            print(text)
        return 0 if r["score"] >= args.min_score else 1

    if args.cmd == "retrofit":
        root = Path(args.path)
        if not root.is_dir():
            print(f"ERROR: {root} is not a directory.")
            return 2
        print(scaffold.retrofit(root, level=args.level, dry_run=args.dry_run, git_init=args.git_init,
                                commit=args.commit, claude=args.claude,
                                python=scaffold.detect_python() if args.claude else "python"))
        return 0

    if args.cmd == "install":
        if args.global_:
            print(installer.install_global(dry_run=args.dry_run))
            return 0
        print(installer.install(Path(args.path), level=args.level, python=args.python,
                                statusline=not args.no_statusline, force=args.force, dry_run=args.dry_run,
                                git_init=args.git_init, commit=args.commit))
        return 0

    if args.cmd == "update":
        print(installer.update(Path(args.path), python=args.python, statusline=not args.no_statusline,
                               force=args.force, dry_run=args.dry_run))
        return 0

    if args.cmd == "uninstall":
        print(installer.uninstall(Path(args.path), dry_run=args.dry_run))
        return 0

    if args.cmd == "doctor":
        code, report = installer.doctor(Path(args.path))
        print(report)
        return code

    if args.cmd == "upgrade":
        print(scaffold.upgrade(Path(args.path).resolve()))
        return 0

    if args.cmd == "install-skill":
        src = KIT_ROOT / "skills" / "harness-retrofit"
        dest = Path(args.dest) / "harness-retrofit"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        skill = dest / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8").replace("{{KIT_ROOT}}", KIT_ROOT.as_posix()),
                         encoding="utf-8")
        print(f"Installed the skill to {dest}. In Claude Code, open the legacy project and type /harness-retrofit.")
        return 0

    if args.cmd == "run":
        rest = [a for a in args.rest if a != "--"]
        return runtime.main(rest, root=Path(args.path).resolve())
    return 2


if __name__ == "__main__":
    sys.exit(main())
