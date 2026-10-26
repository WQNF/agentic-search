"""命令行入口。

在 backend/ 下运行：
  python -m tests.eval.cli gt --sample core
  python -m tests.eval.cli run --sample pilot --force
  python -m tests.eval.cli invalid --force
  python -m tests.eval.cli baseline --sample pilot --force
  python -m tests.eval.cli faults --force
  python -m tests.eval.cli aggregate
  python -m tests.eval.cli all --sample core
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _check_cwd():
    if not Path("app").exists() or not Path(".env").exists():
        raise SystemExit("请在 agentic_research/backend/ 目录下运行（需要 app/ 和 .env）")


def cmd_gt(args):
    from .config import companies_for
    from .ground_truth import build
    build(companies_for(args.sample), force=args.force)


def cmd_run(args):
    from .runner import run_sample
    asyncio.run(run_sample(args.sample, limit=args.limit, force=args.force))


def cmd_invalid(args):
    from .invalid import run_all
    asyncio.run(run_all(force=args.force))


def cmd_baseline(args):
    from .baseline import run_sample
    asyncio.run(run_sample(args.sample, limit=args.limit, force=args.force))


def cmd_faults(args):
    from .faults import run_all
    asyncio.run(run_all(force=args.force, only=args.only))


def cmd_aggregate(args):
    from .aggregate import main
    main()


def cmd_all(args):
    from .config import companies_for
    from .ground_truth import GT_CSV, build
    if args.gt or not GT_CSV.exists():
        build(companies_for(args.sample), force=args.force)
    if not args.skip_e2e:
        asyncio.run(__import__("tests.eval.runner", fromlist=["run_sample"]).run_sample(
            args.sample, limit=args.limit, force=args.force))
    if not args.skip_invalid:
        asyncio.run(__import__("tests.eval.invalid", fromlist=["run_all"]).run_all(force=args.force))
    if not args.skip_baseline:
        asyncio.run(__import__("tests.eval.baseline", fromlist=["run_sample"]).run_sample(
            args.sample, limit=args.limit, force=args.force))
    if not args.skip_faults:
        asyncio.run(__import__("tests.eval.faults", fromlist=["run_all"]).run_all(force=args.force))
    from .aggregate import main
    main()


def build_parser():
    p = argparse.ArgumentParser("eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--sample", default="core", choices=["core", "pilot", "old"])
        sp.add_argument("--force", action="store_true")
        sp.add_argument("--limit", type=int, default=None)

    g = sub.add_parser("gt", help="构建 ground truth")
    common(g)
    g.set_defaults(func=cmd_gt)

    r = sub.add_parser("run", help="30 家端到端")
    common(r)
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("invalid", help="3 个无效代码")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_invalid)

    b = sub.add_parser("baseline", help="单 LLM 基线")
    common(b)
    b.set_defaults(func=cmd_baseline)

    f = sub.add_parser("faults", help="10 类故障注入")
    f.add_argument("--force", action="store_true")
    f.add_argument("--only", nargs="*", default=None)
    f.set_defaults(func=cmd_faults)

    a = sub.add_parser("aggregate", help="汇总 21 项指标")
    a.set_defaults(func=cmd_aggregate)

    al = sub.add_parser("all", help="整套跑一遍")
    common(al)
    al.add_argument("--gt", action="store_true")
    al.add_argument("--skip-e2e", action="store_true")
    al.add_argument("--skip-invalid", action="store_true")
    al.add_argument("--skip-baseline", action="store_true")
    al.add_argument("--skip-faults", action="store_true")
    al.set_defaults(func=cmd_all)
    return p


def main(argv=None):
    _check_cwd()
    p = build_parser()
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
