from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from tools.repo_health.contract import stub_report, validate_report


def load_adapter_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"repo_health_adapter_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load adapter module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command_validate(args: argparse.Namespace) -> int:
    path = Path(args.adapter).resolve()
    module = load_adapter_module(path)
    if not hasattr(module, "emit_report"):
        raise SystemExit(f"adapter {path} must define emit_report()")
    report = module.emit_report()
    errors = validate_report(report)
    if errors:
        if args.json:
            print(json.dumps({"status": "invalid", "adapter": str(path), "errors": errors}, indent=2))
        else:
            print(f"INVALID {path}")
            for error in errors:
                print(f"- {error}")
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "adapter": str(path),
                    "repo_id": report["repo"]["repo_id"],
                    "working_status": report["summary"]["working_status"],
                    "evidence_quality": report["summary"]["evidence_quality"],
                    "checks": len(report["checks"]),
                },
                indent=2,
            )
        )
    else:
        print(
            f"VALID repo={report['repo']['repo_id']} working={report['summary']['working_status']} "
            f"evidence={report['summary']['evidence_quality']} "
            f"checks={len(report['checks'])}"
        )
    return 0


def command_stub(args: argparse.Namespace) -> int:
    report = stub_report(
        repo_id=args.repo_id,
        display_name=args.display_name,
        repo_root=args.repo_root,
        profile=args.profile,
        adapter_name=args.adapter_name,
        adapter_version=args.adapter_version,
    )
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical repo-health contract helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate an adapter module that exposes emit_report().")
    validate_parser.add_argument("adapter")
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=command_validate)

    stub_parser = subparsers.add_parser("stub", help="Emit a stub report for onboarding a new repo.")
    stub_parser.add_argument("--repo-id", required=True)
    stub_parser.add_argument("--display-name", required=True)
    stub_parser.add_argument("--repo-root", required=True)
    stub_parser.add_argument("--profile", choices=("application", "automation", "service_only", "library"), required=True)
    stub_parser.add_argument("--adapter-name", default="repo_health.stub")
    stub_parser.add_argument("--adapter-version", default="0.1.0")
    stub_parser.set_defaults(func=command_stub)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
