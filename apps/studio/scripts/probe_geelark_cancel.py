"""Phase 0a probe: test /open/v1/task/cancel against a real running RPA task.

Background
==========
docs/superpowers/specs/2026-06-13-phone-runtime-architecture-design.md
Phase 0a requires verifying the cancel endpoint before we wire it into
the timeout flow. Documentation is contradictory:

  - docs/geelark/api_reference.md and rpa_tasks.md say `{"ids":[...]}` (list)
  - docs/geelark/openapi-en.yaml says `{"id": "..."}` (single)

api_reference.md marks the endpoint with WARN ("require valid task IDs"),
i.e. no one in this repo has actually called it. We need ground truth.

What this script does
=====================
1. start_phones([env_id])
2. submit a short browse-video warmup RPA task
3. wait ~15s so GeeLark transitions the task to running (status=2)
4. query_tasks to confirm status=2 before we attempt cancel
5. POST /open/v1/task/cancel with {"ids":[task_id]} (the list-form, our
   primary hypothesis based on doc credibility)
6. poll task detail every 5s for up to 30s and record status timeline
7. ALWAYS stop_phones([env_id]) in a finally block — billing-safe

Cost
====
~1.5-2 minutes of one cloud phone running. At ~$0.007/min that is
roughly $0.01-$0.02. Cheap, but real money. Run it deliberately.

Usage
=====
Set GEELARK_TOKEN in .env (already done for the main app), then:

    uv run python scripts/probe_geelark_cancel.py --env-id <cloud_phone_id>

Optional:
    --schema {ids,id,both}   default: ids
        ids  = POST {"ids":[task_id]} only
        id   = POST {"id": task_id}   only (likely wrong per doc evidence)
        both = run two probes back-to-back (≈ 2x cost) to settle the
               schema question definitively
    --task-duration-min N    default: 5  (the warmup task's nominal length;
                                          we cancel long before it ends)
    --wait-before-cancel-s N default: 15 (let task transition to status=2)
    --poll-attempts N        default: 6  (poll task detail after cancel)
    --poll-interval-s N      default: 5

Output is a JSON timeline on stdout so the result can be pasted directly
into the spec's "Phase 0a Findings" section.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.geelark_service import make_client  # noqa: E402


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record(events: list[dict], **kwargs) -> None:
    events.append({"ts": _now_iso(), **kwargs})
    print(json.dumps(events[-1], ensure_ascii=False), flush=True)


def _attempt_cancel(client, task_id: str, schema: str, events: list[dict]) -> dict:
    """Hit /open/v1/task/cancel with one of the two doc-claimed payload shapes.

    Returns a dict {"http_ok": bool, "code": int, "msg": str, "raw": dict|None}.
    Never raises — we want the probe to keep going and stop the phone.
    """
    if schema == "ids":
        payload = {"ids": [task_id]}
    elif schema == "id":
        payload = {"id": task_id}
    else:
        raise ValueError(f"unknown schema {schema!r}")

    _record(events, op="cancel.request", schema=schema, payload=payload)
    try:
        data = client._post("/open/v1/task/cancel", payload)
        _record(events, op="cancel.response", schema=schema,
                http_ok=True, code=0, data=data)
        return {"http_ok": True, "code": 0, "msg": "", "raw": data}
    except RuntimeError as exc:
        _record(events, op="cancel.response", schema=schema,
                http_ok=False, error=str(exc))
        return {"http_ok": False, "code": -1, "msg": str(exc), "raw": None}
    except Exception as exc:  # noqa: BLE001 — surface everything as data
        _record(events, op="cancel.exception", schema=schema, error=repr(exc))
        return {"http_ok": False, "code": -2, "msg": repr(exc), "raw": None}


def _poll_task_detail(client, task_id: str, attempts: int, interval_s: int,
                      events: list[dict]) -> list[dict]:
    """Poll task detail after cancel to observe status transition.

    GeeLark status codes:
      1=waiting, 2=running, 3=completed, 4=failed, 7=cancelled (per
      docs/geelark/RESEARCH-tiktok-login.md and our CLAUDE.md).
    """
    snapshots: list[dict] = []
    for i in range(attempts):
        time.sleep(interval_s)
        try:
            detail = client.get_task_detail(task_id)
        except Exception as exc:  # noqa: BLE001
            _record(events, op="poll.error", attempt=i + 1, error=repr(exc))
            continue
        status = detail.get("status") if isinstance(detail, dict) else None
        fail_code = detail.get("failCode") if isinstance(detail, dict) else None
        fail_desc = detail.get("failDesc") if isinstance(detail, dict) else None
        snapshots.append({"attempt": i + 1, "status": status,
                          "failCode": fail_code, "failDesc": fail_desc})
        _record(events, op="poll.detail", attempt=i + 1, status=status,
                failCode=fail_code, failDesc=fail_desc)
        if status in (3, 4, 7):
            break
    return snapshots


def _run_one_probe(client, env_id: str, args, schema: str) -> dict:
    events: list[dict] = []
    summary = {"schema": schema, "env_id": env_id}

    _record(events, op="phone.start", env_id=env_id)
    client.start_phones([env_id])

    try:
        _record(events, op="task.submit",
                kind="warmup", duration_min=args.task_duration_min)
        task_id = client.create_warmup_task(env_id, args.task_duration_min)
        summary["task_id"] = task_id
        _record(events, op="task.submitted", task_id=task_id)

        # Let GeeLark pick the task up (waiting → running).
        _record(events, op="wait.before_cancel",
                seconds=args.wait_before_cancel_s)
        time.sleep(args.wait_before_cancel_s)

        # Snapshot pre-cancel status so we know we're cancelling a
        # real-running task, not one already in terminal state.
        try:
            pre = client.get_task_detail(task_id)
            pre_status = pre.get("status") if isinstance(pre, dict) else None
        except Exception as exc:  # noqa: BLE001
            pre_status = None
            _record(events, op="pre_cancel.error", error=repr(exc))
        else:
            _record(events, op="pre_cancel.status", status=pre_status)
        summary["pre_cancel_status"] = pre_status

        # The actual cancel attempt — the question of this whole probe.
        summary["cancel"] = _attempt_cancel(client, task_id, schema, events)

        # Observe what happens after cancel.
        summary["post_snapshots"] = _poll_task_detail(
            client, task_id, args.poll_attempts, args.poll_interval_s, events
        )
    finally:
        # GeeLark charges per minute of running. Always stop, even if
        # cancel "succeeded" — we don't yet know if cancel implies stop.
        _record(events, op="phone.stop", env_id=env_id)
        try:
            client.stop_phones([env_id])
        except Exception as exc:  # noqa: BLE001
            _record(events, op="phone.stop.error", error=repr(exc))

    summary["events"] = events
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-id", required=True,
                        help="GeeLark cloud phone id (envId) to probe against")
    parser.add_argument("--schema", choices=["ids", "id", "both"],
                        default="ids",
                        help="cancel payload shape to test (default: ids)")
    parser.add_argument("--task-duration-min", type=int, default=5)
    parser.add_argument("--wait-before-cancel-s", type=int, default=15)
    parser.add_argument("--poll-attempts", type=int, default=6)
    parser.add_argument("--poll-interval-s", type=int, default=5)
    args = parser.parse_args()

    client = make_client()

    results: list[dict] = []
    schemas = ["ids", "id"] if args.schema == "both" else [args.schema]
    for s in schemas:
        print(f"\n=== probing schema={s} on env_id={args.env_id} ===",
              flush=True)
        results.append(_run_one_probe(client, args.env_id, args, s))

    print("\n=== summary ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
