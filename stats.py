"""
stats.py - Lưu / đọc bảng xếp hạng (JSON trên đĩa).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
STATS_PATH = DATA_DIR / "stats.json"


@dataclass
class PlayerStats:
    user_id: int
    wins: int = 0
    losses: int = 0
    draws: int = 0
    display_name: str = ""

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def points(self) -> float:
        return self.wins * 1.0 + self.draws * 0.5

    @property
    def win_rate(self) -> float:
        return (self.wins / self.games * 100) if self.games else 0.0


class StatsStore:
    def __init__(self, path: Path = STATS_PATH):
        self.path = path
        self._lock = asyncio.Lock()
        self._players: dict[int, PlayerStats] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw.get("players", []):
            ps = PlayerStats(
                user_id=int(item["user_id"]),
                wins=int(item.get("wins", 0)),
                losses=int(item.get("losses", 0)),
                draws=int(item.get("draws", 0)),
                display_name=str(item.get("display_name", "")),
            )
            self._players[ps.user_id] = ps

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "players": [asdict(p) for p in self._players.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, user_id: int) -> Optional[PlayerStats]:
        return self._players.get(user_id)

    def ensure(self, user_id: int, display_name: str = "") -> PlayerStats:
        ps = self._players.get(user_id)
        if ps is None:
            ps = PlayerStats(user_id=user_id, display_name=display_name)
            self._players[user_id] = ps
        elif display_name:
            ps.display_name = display_name
        return ps

    def ranked(self) -> list[PlayerStats]:
        return sorted(
            self._players.values(),
            key=lambda p: (p.points, p.wins, p.win_rate, -p.losses),
            reverse=True,
        )

    async def record_result(
        self,
        white_id: int,
        black_id: int,
        result: str,
        white_name: str = "",
        black_name: str = "",
    ) -> None:
        """result: '1-0' | '0-1' | '1/2-1/2' — bỏ qua nếu '*' hoặc khác."""
        if result not in ("1-0", "0-1", "1/2-1/2"):
            return
        async with self._lock:
            white = self.ensure(white_id, white_name)
            black = self.ensure(black_id, black_name)
            if result == "1-0":
                white.wins += 1
                black.losses += 1
            elif result == "0-1":
                black.wins += 1
                white.losses += 1
            else:
                white.draws += 1
                black.draws += 1
            self._save()

    async def record_resign(
        self,
        winner_id: int,
        loser_id: int,
        winner_name: str = "",
        loser_name: str = "",
    ) -> None:
        async with self._lock:
            winner = self.ensure(winner_id, winner_name)
            loser = self.ensure(loser_id, loser_name)
            winner.wins += 1
            loser.losses += 1
            self._save()
