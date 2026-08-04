"""Backfill legacy Studio credentials and files into one Radar workspace.

Run without --apply for counts, after the Alembic workspace migration and a
database/file backup. The operation is repeatable: already moved files and
already encrypted secrets are skipped or updated in place.
"""

import argparse
import shutil
import sys
from pathlib import Path

# Allow the documented ``python scripts/migrate_workspace_data.py`` invocation
# to import the sibling ``app`` package without requiring a manual PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app import config, workspace_secrets
from app.db import session_scope


def select_workspace(session, workspace_id: int) -> None:
    """Restore the transaction-local RLS context after an internal commit."""
    session.execute(
        text("SELECT set_config('app.workspace_id', :value, true)"),
        {"value": str(workspace_id)},
    )


def direct_files(directory: Path, workspace_id: int) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.iterdir()
            if p.is_file() and p.parent.name != str(workspace_id)]


def move_files(paths: list[Path], destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for source in paths:
        target = destination / source.name
        if target.exists():
            if source.stat().st_size != target.stat().st_size:
                raise RuntimeError(f"file collision with different size: {target}")
            source.unlink()
        else:
            shutil.move(str(source), str(target))
        moved += 1
    return moved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.workspace_id <= 0:
        raise SystemExit("--workspace-id must be positive")

    uploads = direct_files(Path(config.UPLOAD_DIR), args.workspace_id)
    thumbnails = direct_files(Path(config.DATA_DIR) / "thumbnails", args.workspace_id)
    screenshots = direct_files(Path(config.SCREENSHOT_DIR), args.workspace_id)
    secret_values = {
        "GEELARK_TOKEN": config.GEELARK_TOKEN,
        "GEMINI_API_KEY": config.GEMINI_API_KEY,
    }
    secret_values = {k: v for k, v in secret_values.items() if v}
    print({
        "mode": "apply" if args.apply else "dry-run",
        "workspace_id": args.workspace_id,
        "uploads": len(uploads),
        "thumbnails": len(thumbnails),
        "screenshots": len(screenshots),
        "secrets": sorted(secret_values),
    })
    if not args.apply:
        return

    with session_scope(args.workspace_id) as session:
        workspace_secrets.update(session, args.workspace_id, secret_values)
        # workspace_secrets.update commits, which clears the transaction-local
        # app.workspace_id used by FORCE RLS. Re-select it before path updates.
        select_workspace(session, args.workspace_id)
        moved_uploads = move_files(
            uploads, Path(config.UPLOAD_DIR) / str(args.workspace_id),
        )
        for old_path in uploads:
            new_path = Path(config.UPLOAD_DIR) / str(args.workspace_id) / old_path.name
            session.execute(
                text("UPDATE videos SET stored_path=:new WHERE stored_path=:old"),
                {"new": str(new_path), "old": str(old_path)},
            )
        moved_thumbnails = move_files(
            thumbnails, Path(config.DATA_DIR) / "thumbnails" / str(args.workspace_id),
        )
        for old_path in thumbnails:
            session.execute(
                text("UPDATE videos SET thumbnail_path=:new WHERE thumbnail_path=:old"),
                {
                    "new": f"thumbnails/{args.workspace_id}/{old_path.name}",
                    "old": f"thumbnails/{old_path.name}",
                },
            )
        moved_screenshots = move_files(
            screenshots, Path(config.SCREENSHOT_DIR) / str(args.workspace_id),
        )
        session.commit()
        select_workspace(session, args.workspace_id)
        encrypted = workspace_secrets.get_status(session, args.workspace_id)
    print({
        "committed": True,
        "uploads_moved": moved_uploads,
        "thumbnails_moved": moved_thumbnails,
        "screenshots_moved": moved_screenshots,
        "encrypted": {k: v["set"] for k, v in encrypted.items()},
    })


if __name__ == "__main__":
    main()
