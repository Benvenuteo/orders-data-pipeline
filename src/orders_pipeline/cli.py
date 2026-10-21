"""Command-line operations, demonstration and safe structured diagnostics."""

import argparse
import json
import logging
import sys
from pathlib import Path
from uuid import UUID

from orders_pipeline.client import ApiClient
from orders_pipeline.db import SqlTarget, provision
from orders_pipeline.errors import PipelineError, SimulatedFailure
from orders_pipeline.reconcile import reconcile
from orders_pipeline.runner import Runner
from orders_pipeline.settings import Settings


class JsonFormatter(logging.Formatter):
    """Log operational metadata without HTTP headers, credentials or business payloads."""

    def format(self, record: logging.LogRecord) -> str:
        data = {"level": record.levelname, "event": record.getMessage()}
        for key in ("run_id", "error_code", "status", "checkpoint", "inserted", "updated"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data)


def _print(data: dict, output: str | None = None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    print(text)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


def mutate_demo(api: ApiClient, base_count: int) -> dict:
    """Apply ten inserts, five updates and two soft deletions to a seeded lab source."""
    for order_id in range(base_count + 1, base_count + 11):
        api.mutate(
            "POST",
            "/v1/orders",
            {
                "order_id": order_id,
                "customer_id": 1,
                "total_gross": "100.00",
                "currency": "PLN",
                "status": "new",
            },
        )
    for order_id in range(1, 6):
        api.mutate("PATCH", f"/v1/orders/{order_id}", {"status": "paid", "total_gross": "999.99"})
    for order_id in (6, 7):
        api.mutate("PATCH", f"/v1/orders/{order_id}", {"is_deleted": True})
    return {"inserted": 10, "updated": 5, "deleted": 2}


def demo(settings: Settings, target: SqlTarget, api: ApiClient, count: int) -> dict:
    """Exercise full load, rollback, offline replay and migration acceptance."""
    if api.boundary().high or target.checkpoint()[1] or target.orders():
        raise PipelineError("Demo requires an empty source and target; existing data is preserved")
    api.mutate("POST", "/admin/seed", {"count": count, "seed": 42})
    initial = Runner(settings, target, api).run()
    mutate_demo(api, count)
    try:
        Runner(settings, target, api).run(fail_before_commit=True)
    except SimulatedFailure:
        failed_id = target.status()["last_run"]["run_id"]
    else:
        raise PipelineError("The rollback scenario did not fail as requested")
    if len(target.orders()) != count or target.checkpoint()[1] != count:
        raise PipelineError("Failed publication changed the previously committed state")
    replay = Runner(settings, target, None).run(parent=failed_id)
    api.mutate("POST", "/admin/freeze", {"frozen": True})
    try:
        report = reconcile(target, api, cutover=True)
        if (
            not report["passed"]
            or report["target"]["total"] != count + 10
            or report["target"]["active"] != count + 8
        ):
            raise PipelineError("Demonstration reconciliation did not match expected results")
    finally:
        api.mutate("POST", "/admin/freeze", {"frozen": False})
    return {
        "initial": initial,
        "simulated_failed_run": failed_id,
        "offline_replay": replay,
        "cutover_check": report,
        "source_unfrozen_after_demo": True,
    }


def parser() -> argparse.ArgumentParser:
    """Expose explicit commands suitable for an operator and Windows Task Scheduler."""
    root = argparse.ArgumentParser(description="Reliable orders pipeline")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Provision schemas using administrator credentials")
    run = sub.add_parser("run", help="Import changes since the last committed checkpoint")
    run.add_argument(
        "--fail-before-commit", action="store_true", help="Exercise transaction rollback"
    )
    replay = sub.add_parser("replay", help="Replay a completely extracted failed run without HTTP")
    replay.add_argument("run_id", type=lambda value: str(UUID(value)))
    sub.add_parser("status", help="Show checkpoints, counts and the last run")
    sub.add_parser(
        "health", help="Exit nonzero if the last run failed or success is older than 30 min"
    )
    check = sub.add_parser("reconcile", help="Compare all fields at the target checkpoint")
    check.add_argument("--cutover", action="store_true")
    check.add_argument("--output")
    demonstration = sub.add_parser("demo", help="Run an empty-environment acceptance scenario")
    demonstration.add_argument("--count", type=int, default=1000, choices=range(10, 10001))
    demonstration.add_argument("--output")
    source = sub.add_parser("source", help="Operate the synthetic shop")
    actions = source.add_subparsers(dest="action", required=True)
    seed = actions.add_parser("seed")
    seed.add_argument("--count", type=int, default=10000)
    actions.add_parser("boundary")
    actions.add_parser("freeze")
    actions.add_parser("unfreeze")
    mutate = actions.add_parser("mutate-demo")
    mutate.add_argument("--base-count", type=int, default=1000)
    fault = actions.add_parser("fault")
    fault.add_argument("mode", choices=["none", "http_429", "http_503", "invalid_record", "delay"])
    fault.add_argument("--remaining", type=int, default=1)
    fault.add_argument("--delay", type=float, default=0.1)
    return root


def main() -> int:
    """Return stable exit codes and keep unsafe driver exception messages off the console."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.getLogger("orders_pipeline").addHandler(handler)
    logging.getLogger("orders_pipeline").setLevel(logging.INFO)
    args = parser().parse_args()
    target = None
    api = None
    try:
        settings = Settings.from_env()
        if args.command == "init-db":
            provision(settings)
            _print({"initialized": settings.sql_database})
            return 0
        needs_api = args.command in {"run", "source", "demo", "reconcile"}
        api = ApiClient(settings) if needs_api else None
        if args.command == "source":
            if args.action == "boundary":
                result = api.boundary().model_dump()
            elif args.action == "seed":
                result = api.mutate("POST", "/admin/seed", {"count": args.count, "seed": 42})
            elif args.action in {"freeze", "unfreeze"}:
                result = api.mutate("POST", "/admin/freeze", {"frozen": args.action == "freeze"})
            elif args.action == "mutate-demo":
                result = mutate_demo(api, args.base_count)
            else:
                result = api.mutate(
                    "POST",
                    "/admin/fault",
                    {
                        "mode": args.mode,
                        "remaining": args.remaining,
                        "delay": args.delay,
                    },
                )
            _print(result)
            return 0
        target = SqlTarget(settings)
        if args.command == "run":
            _print(Runner(settings, target, api).run(fail_before_commit=args.fail_before_commit))
        elif args.command == "replay":
            _print(Runner(settings, target, None).run(parent=args.run_id))
        elif args.command in {"status", "health"}:
            result = target.status()
            _print(result)
            return 0 if args.command == "status" or result["healthy"] else 2
        elif args.command == "reconcile":
            report = reconcile(target, api, cutover=args.cutover)
            _print(report, args.output)
            return 0 if report["passed"] else 2
        elif args.command == "demo":
            _print(demo(settings, target, api, args.count), args.output)
        return 0
    except Exception as exc:
        data = {"status": "failed", "error_code": type(exc).__name__}
        if isinstance(exc, PipelineError):
            data["message"] = str(exc)
        _print(data)
        return 3 if isinstance(exc, SimulatedFailure) else 1
    finally:
        if api:
            api.close()
        if target:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
