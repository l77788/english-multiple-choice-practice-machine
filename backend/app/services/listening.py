from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..config import QUESTION_BANK_DIR


CONTENT_VERSION = "1.0.0"
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


def _safe_audio_name(name: str, index: int, suffix: str) -> str:
    label = Path(name).stem.strip()
    return label[:80] if label else f"听力音频 {index}"


def _track_url(package_id: str, content_version: str, asset_id: str) -> str:
    return (
        "/api/question-banks/assets/"
        f"{package_id}/{content_version}/{asset_id}"
    )


def attach_listening_assets(
    connection: sqlite3.Connection,
    paper_id: int,
    audio_paths: Iterable[Path],
    audio_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Persist imported audio and associate every listening unit with it.

    CET audio is intentionally kept as a complete track. All listening
    sections of the same paper reference that track instead of cutting it
    into question-sized clips.
    """

    listening_units = connection.execute(
        """
        SELECT id, shared_data
        FROM units
        WHERE paper_id = ? AND unit_type = 'listening'
        ORDER BY sequence
        """,
        (paper_id,),
    ).fetchall()
    if not listening_units:
        return []

    paths = [Path(path) for path in audio_paths]
    names = list(audio_names or [])
    valid: list[tuple[Path, str, str]] = []
    for index, source in enumerate(paths, 1):
        suffix = source.suffix.lower()
        if suffix not in MEDIA_TYPES or not source.is_file() or source.stat().st_size <= 0:
            continue
        original_name = names[index - 1] if index - 1 < len(names) else source.name
        valid.append((source, original_name, suffix))
    if not valid:
        return []

    package_id = f"local.paper-{paper_id}"
    target_dir = (
        QUESTION_BANK_DIR
        / package_id
        / CONTENT_VERSION
        / "assets"
        / "audio"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    tracks: list[dict[str, Any]] = []

    for index, (source, original_name, suffix) in enumerate(valid, 1):
        asset_id = f"listening.track.{index}"
        target = target_dir / f"track-{index}{suffix}"
        shutil.copy2(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        label = _safe_audio_name(original_name, index, suffix)
        metadata = {
            "assetId": asset_id,
            "mediaType": MEDIA_TYPES[suffix],
            "originalName": original_name,
            "label": label,
            "size": target.stat().st_size,
        }
        connection.execute(
            """
            INSERT INTO question_bank_assets
                (package_id, content_version, asset_id, stored_path,
                 media_type, sha256, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(package_id, content_version, asset_id) DO UPDATE SET
                stored_path = excluded.stored_path,
                media_type = excluded.media_type,
                sha256 = excluded.sha256,
                metadata = excluded.metadata
            """,
            (
                package_id,
                CONTENT_VERSION,
                asset_id,
                str(target),
                MEDIA_TYPES[suffix],
                digest,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        tracks.append(
            {
                "asset_id": asset_id,
                "label": label,
                "media_type": MEDIA_TYPES[suffix],
                "url": _track_url(package_id, CONTENT_VERSION, asset_id),
            }
        )

    connection.execute(
        """
        INSERT INTO question_bank_packages
            (package_id, content_version, title, publisher, manifest_data,
             source_file, status)
        SELECT ?, ?, title, '本地导入', '{}', COALESCE(source_file, ''), 'published'
        FROM papers WHERE id = ?
        ON CONFLICT(package_id, content_version) DO UPDATE SET
            title = excluded.title,
            source_file = excluded.source_file,
            status = 'published',
            updated_at = CURRENT_TIMESTAMP
        """,
        (package_id, CONTENT_VERSION, paper_id),
    )

    for unit in listening_units:
        try:
            shared_data = json.loads(unit["shared_data"] or "{}")
        except json.JSONDecodeError:
            shared_data = {}
        shared_data.update(
            {
                "content_package_id": package_id,
                "content_version": CONTENT_VERSION,
                "audio_tracks": tracks,
            }
        )
        connection.execute(
            "UPDATE units SET shared_data = ? WHERE id = ?",
            (json.dumps(shared_data, ensure_ascii=False), unit["id"]),
        )
    return tracks


def repair_published_listening_assets(connection: sqlite3.Connection) -> int:
    """Recover audio uploaded by older versions but never attached on publish."""

    repaired = 0
    jobs = connection.execute(
        """
        SELECT parse_context
        FROM import_jobs
        WHERE status = 'published' AND parse_context IS NOT NULL
        """
    ).fetchall()
    for job in jobs:
        try:
            context = json.loads(job["parse_context"] or "{}")
        except json.JSONDecodeError:
            continue
        paper_ids = [
            int(value)
            for value in context.get("published_paper_ids", [])
            if isinstance(value, int) and value > 0
        ]
        paths = [Path(value) for value in context.get("audio_paths", []) if value]
        names = [str(value) for value in context.get("audio_names", []) if value]
        if not paper_ids or not paths:
            continue
        for paper_id in paper_ids:
            row = connection.execute(
                """
                SELECT shared_data
                FROM units
                WHERE paper_id = ? AND unit_type = 'listening'
                ORDER BY sequence LIMIT 1
                """,
                (paper_id,),
            ).fetchone()
            if row is None:
                continue
            try:
                shared_data = json.loads(row["shared_data"] or "{}")
            except json.JSONDecodeError:
                shared_data = {}
            if shared_data.get("audio_tracks"):
                continue
            if attach_listening_assets(connection, paper_id, paths, names):
                repaired += 1
    if repaired:
        connection.commit()
    return repaired
