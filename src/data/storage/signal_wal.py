"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 19.01.2026.
    File        : signal_wal
    Project     : Project-103
    Description : Write-Ahead Log (WAL) for signal persistence.
                  Ensures signals are not lost if database is unavailable.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class WALEntryType(Enum):
    """Types of WAL entries."""

    SIGNAL_CREATE = "SIGNAL_CREATE"
    SIGNAL_EXECUTE = "SIGNAL_EXECUTE"
    SIGNAL_CLOSE = "SIGNAL_CLOSE"
    ORDER_CREATE = "ORDER_CREATE"
    ORDER_UPDATE = "ORDER_UPDATE"
    BALANCE_SAVE = "BALANCE_SAVE"


class WALEntryStatus(Enum):
    """Status of WAL entries."""

    PENDING = "PENDING"  # Not yet synced to DB
    SYNCED = "SYNCED"  # Successfully synced to DB
    FAILED = "FAILED"  # Failed to sync (will retry)
    ABANDONED = "ABANDONED"  # Too many retries, given up


@dataclass
class WALEntry:
    """Single WAL entry."""

    id: str  # Unique entry ID
    entry_type: str  # WALEntryType value
    data: Dict[str, Any]  # Payload data
    created_at: float  # Unix timestamp
    status: str = "PENDING"  # WALEntryStatus value
    retry_count: int = 0  # Number of sync attempts
    last_error: Optional[str] = None  # Last error message
    synced_at: Optional[float] = None  # When synced to DB

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "entry_type": self.entry_type,
            "data": self.data,
            "created_at": self.created_at,
            "status": self.status,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WALEntry":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            entry_type=data["entry_type"],
            data=data["data"],
            created_at=data["created_at"],
            status=data.get("status", "PENDING"),
            retry_count=data.get("retry_count", 0),
            last_error=data.get("last_error"),
            synced_at=data.get("synced_at"),
        )


class SignalWAL:
    """
    Write-Ahead Log for signal persistence.

    Provides durability for signals when the database is unavailable.
    Signals are first written to a local file, then synced to the database.
    Failed syncs are retried in the background.
    """

    def __init__(
        self,
        wal_dir: str = "data/wal",
        max_retries: int = 10,
        retry_interval: float = 30.0,
        compact_threshold: int = 100,
        max_entries_in_memory: int = 5000,
    ):
        """
        Initialize WAL.

        Args:
            wal_dir: Directory to store WAL files
            max_retries: Maximum number of sync retries before abandoning
            retry_interval: Seconds between retry attempts
            compact_threshold: Compact WAL when synced entries exceed this
            max_entries_in_memory: Maximum entries to keep in memory
        """
        self.wal_dir = Path(wal_dir)
        self.wal_file = self.wal_dir / "signals.wal"
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.compact_threshold = compact_threshold
        self.max_entries_in_memory = max_entries_in_memory

        # In-memory cache of pending entries
        self._entries: Dict[str, WALEntry] = {}
        self._lock = asyncio.Lock()
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False

        # Database sync callbacks (set by SignalLifecycleManager)
        self._sync_callbacks: Dict[str, Callable] = {}

        # Ensure WAL directory exists
        self.wal_dir.mkdir(parents=True, exist_ok=True)

        # Load existing entries from WAL file
        self._load_wal()

        logger.info(f"SignalWAL initialized: {len(self._entries)} pending entries")

    # An entry that has not synced for this many days will never sync: the update
    # target was deleted from the DB or never existed.
    HOPELESS_AFTER_DAYS = 7

    def _is_hopeless(self, entry) -> bool:
        """A failed entry older than a week is garbage, not a task.

        The retry counter does not help here: the WAL is re-read from disk on every
        start, and retry_count stays at 1 — however many sessions the entry survives.
        So the limit is set by age.

        That is how two ORDER_UPDATEs for TAKE_PROFIT_1 lived for three weeks: the
        signals were already deleted from the DB, and a TP1 order on MT5 cannot exist
        at all — the take-profit there is set by modifying the position. Every start
        dutifully reported "2 failed", a false alarm that teaches people not to look.
        """
        if entry.status != "FAILED":
            return False
        age_days = (time.time() - entry.created_at) / 86400
        return age_days > self.HOPELESS_AFTER_DAYS

    def _load_wal(self) -> None:
        """Load existing WAL entries from file."""
        if not self.wal_file.exists():
            return

        dropped = []
        try:
            with open(self.wal_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = WALEntry.from_dict(data)
                        # Only load pending/failed entries
                        if entry.status in ("PENDING", "FAILED"):
                            if self._is_hopeless(entry):
                                dropped.append(entry)
                                continue
                            self._entries[entry.id] = entry
                    except json.JSONDecodeError as e:
                        logger.warning(f"WAL line {line_num} invalid JSON: {e}")
                    except Exception as e:
                        logger.warning(f"WAL line {line_num} parse error: {e}")

            logger.info(f"Loaded {len(self._entries)} pending WAL entries from disk")

            # Dropping silently is not allowed: the content must stay in the log,
            # otherwise a noise-reduction tool itself becomes a way to lose data.
            for e in dropped:
                age = int((time.time() - e.created_at) / 86400)
                logger.warning(
                    f"WAL: entry {e.entry_type} dropped — {age} days without "
                    f"sync ({e.last_error}); data: {e.data}"
                )

        except Exception as e:
            logger.error(f"Failed to load WAL file: {e}")

    def _append_to_file(self, entry: WALEntry) -> bool:
        """Append entry to WAL file."""
        try:
            with open(self.wal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk
            return True
        except Exception as e:
            logger.error(f"Failed to write WAL entry {entry.id}: {e}")
            return False

    def _generate_entry_id(self, entry_type: str) -> str:
        """Generate unique entry ID."""
        timestamp = int(time.time() * 1000)
        return f"WAL_{entry_type}_{timestamp}_{os.urandom(4).hex()}"

    async def write(
        self, entry_type: WALEntryType, data: Dict[str, Any], entry_id: Optional[str] = None
    ) -> str:
        """
        Write entry to WAL.

        Args:
            entry_type: Type of entry
            data: Entry payload
            entry_id: Optional custom entry ID

        Returns:
            Entry ID
        """
        async with self._lock:
            wal_id = entry_id or self._generate_entry_id(entry_type.value)

            entry = WALEntry(
                id=wal_id,
                entry_type=entry_type.value,
                data=data,
                created_at=time.time(),
            )

            # Write to disk first (WAL guarantee)
            if not self._append_to_file(entry):
                logger.error(f"WAL write failed for {wal_id} - entry may be lost!")
                # Still add to memory for retry

            # Add to memory
            self._entries[wal_id] = entry

            logger.debug(f"WAL entry written: {wal_id} ({entry_type.value})")
            return wal_id

    async def mark_synced(self, entry_id: str) -> None:
        """Mark entry as synced to database."""
        async with self._lock:
            if entry_id in self._entries:
                self._entries[entry_id].status = WALEntryStatus.SYNCED.value
                self._entries[entry_id].synced_at = time.time()

        # Check if compaction needed
        synced_count = sum(
            1 for e in self._entries.values() if e.status == WALEntryStatus.SYNCED.value
        )
        if synced_count >= self.compact_threshold:
            await self._compact()

    async def mark_failed(self, entry_id: str, error: str) -> None:
        """Mark entry as failed (will retry)."""
        async with self._lock:
            if entry_id in self._entries:
                entry = self._entries[entry_id]
                entry.status = WALEntryStatus.FAILED.value
                entry.retry_count += 1
                entry.last_error = error

                if entry.retry_count >= self.max_retries:
                    entry.status = WALEntryStatus.ABANDONED.value
                    logger.error(
                        f"WAL entry {entry_id} abandoned after {entry.retry_count} retries: {error}"
                    )

    async def get_pending_entries(self) -> List[WALEntry]:
        """Get all pending entries for sync."""
        async with self._lock:
            return [
                e
                for e in self._entries.values()
                if e.status in (WALEntryStatus.PENDING.value, WALEntryStatus.FAILED.value)
            ]

    async def _compact(self) -> None:
        """
        Compact WAL file by removing synced entries.
        Rewrites the file with only pending/failed entries.
        """
        async with self._lock:
            pending_entries = [
                e
                for e in self._entries.values()
                if e.status not in (WALEntryStatus.SYNCED.value, WALEntryStatus.ABANDONED.value)
            ]

            # Remove synced entries from memory
            self._entries = {e.id: e for e in pending_entries}

            # Rewrite WAL file
            try:
                temp_file = self.wal_file.with_suffix(".wal.tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    for entry in pending_entries:
                        f.write(json.dumps(entry.to_dict()) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic rename
                temp_file.replace(self.wal_file)
                logger.info(f"WAL compacted: {len(pending_entries)} entries remaining")

            except Exception as e:
                logger.error(f"WAL compaction failed: {e}")

    def register_sync_callback(
        self, entry_type: WALEntryType, callback: Callable[[Dict[str, Any]], bool]
    ) -> None:
        """
        Register a callback for syncing entries to database.

        Args:
            entry_type: Type of entry this callback handles
            callback: Async function that takes entry data and returns True if synced
        """
        self._sync_callbacks[entry_type.value] = callback
        logger.debug(f"Registered WAL sync callback for {entry_type.value}")

    async def start_background_sync(self) -> None:
        """Start background sync task."""
        if self._running:
            return

        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("WAL background sync started")

    async def stop_background_sync(self) -> None:
        """Stop background sync task and compact WAL file."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        # Compact on shutdown to remove synced entries from disk
        synced_count = sum(
            1 for e in self._entries.values() if e.status == WALEntryStatus.SYNCED.value
        )
        if synced_count > 0:
            await self._compact()

        logger.info("WAL background sync stopped")

    async def _sync_loop(self) -> None:
        """Background loop that syncs pending entries to database."""
        while self._running:
            try:
                await self._sync_pending_entries()
            except Exception as e:
                logger.error(f"WAL sync loop error: {e}")

            await asyncio.sleep(self.retry_interval)

    async def _sync_pending_entries(self) -> None:
        """Attempt to sync all pending entries."""
        entries = await self.get_pending_entries()
        if not entries:
            return

        logger.debug(f"WAL syncing {len(entries)} pending entries")

        synced_count = 0
        failed_count = 0
        by_type: Dict[str, int] = {}

        for entry in entries:
            callback = self._sync_callbacks.get(entry.entry_type)
            if not callback:
                logger.warning(f"No sync callback for entry type: {entry.entry_type}")
                continue

            try:
                success = await callback(entry.data)
                if success:
                    await self.mark_synced(entry.id)
                    synced_count += 1
                    type_name = str(entry.entry_type)
                    by_type[type_name] = by_type.get(type_name, 0) + 1
                    logger.debug(f"WAL entry synced: {entry.id}")
                else:
                    await self.mark_failed(entry.id, "Sync callback returned False")
                    failed_count += 1
            except Exception as e:
                await self.mark_failed(entry.id, str(e))
                failed_count += 1
                logger.warning(f"WAL sync failed for {entry.id}: {e}")

        if synced_count > 0 or failed_count > 0:
            type_details = ", ".join(f"{t}: {c}" for t, c in by_type.items())
            logger.info(
                f"WAL sync completed: {synced_count} synced, {failed_count} failed "
                f"({type_details})"
            )

            # Compact after sync to remove synced entries from disk
            if synced_count > 0:
                await self._compact()

    def get_stats(self) -> Dict[str, Any]:
        """Get WAL statistics."""
        pending = sum(1 for e in self._entries.values() if e.status == WALEntryStatus.PENDING.value)
        failed = sum(1 for e in self._entries.values() if e.status == WALEntryStatus.FAILED.value)
        synced = sum(1 for e in self._entries.values() if e.status == WALEntryStatus.SYNCED.value)
        abandoned = sum(
            1 for e in self._entries.values() if e.status == WALEntryStatus.ABANDONED.value
        )

        return {
            "total_entries": len(self._entries),
            "pending": pending,
            "failed": failed,
            "synced": synced,
            "abandoned": abandoned,
            "wal_file": str(self.wal_file),
            "running": self._running,
        }

    def get_stats_str(self) -> str:
        """Get WAL statistics as a formatted string."""
        stats = self.get_stats()
        total = stats["total_entries"]
        parts = [f"{total} entries"]
        if stats["pending"]:
            parts.append(f"{stats['pending']} pending")
        if stats["synced"]:
            parts.append(f"{stats['synced']} synced")
        if stats["failed"]:
            parts.append(f"{stats['failed']} failed")
        if stats["abandoned"]:
            parts.append(f"{stats['abandoned']} abandoned")
        return ", ".join(parts)

    async def force_sync(self) -> int:
        """
        Force immediate sync of all pending entries.

        Returns:
            Number of successfully synced entries
        """
        entries = await self.get_pending_entries()
        synced_count = 0

        for entry in entries:
            callback = self._sync_callbacks.get(entry.entry_type)
            if not callback:
                continue

            try:
                success = await callback(entry.data)
                if success:
                    await self.mark_synced(entry.id)
                    synced_count += 1
            except Exception as e:
                await self.mark_failed(entry.id, str(e))

        return synced_count
