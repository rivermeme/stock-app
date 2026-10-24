import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class Portfolio:
    def __init__(self, path="data/portfolio.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)

        with self.connection() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL
                )
            """)

    def connection(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def all(self):
        with self.connection() as con:
            rows = con.execute(
                "SELECT * FROM assets ORDER BY rowid"
            )
            return [dict(row) for row in rows]

    def save(
        self,
        name,
        kind,
        quantity,
        price,
        currency,
        asset_id=None,
    ):
        if (
            not name.strip()
            or quantity <= 0
            or price < 0
            or currency not in ("KRW", "USD")
        ):
            raise ValueError(
                "종목명, 양수 수량, 0 이상 매수가를 확인하세요."
            )

        with self.connection() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO assets
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id or str(uuid.uuid4()),
                    name.strip(),
                    kind,
                    quantity,
                    price,
                    currency,
                ),
            )

    def delete(self, asset_id):
        with self.connection() as con:
            con.execute(
                "DELETE FROM assets WHERE id=?",
                (asset_id,),
            )


def discussion_top(posts, cutoff, limit=10):
    """직전 24시간 내 동일 사건의 최고 조회수 글만 남깁니다."""
    if cutoff.tzinfo is None:
        raise ValueError("집계 시각에 시간대가 필요합니다.")

    grouped = {}

    for post in posts:
        date = datetime.fromisoformat(post["published_at"])

        if date.tzinfo is None:
            raise ValueError("게시 시각에 시간대가 필요합니다.")

        if not cutoff - timedelta(hours=24) < date <= cutoff:
            continue

        key = post["topic_id"]

        if (
            key not in grouped
            or post["views"] > grouped[key]["views"]
        ):
            grouped[key] = post

    return sorted(
        grouped.values(),
        key=lambda post: post["views"],
        reverse=True,
    )[:limit]


def morning_cutoff(now=None):
    now = (now or datetime.now(KST)).astimezone(KST)

    cutoff = now.replace(
        hour=6,
        minute=30,
        second=0,
        microsecond=0,
    )

    if now < cutoff:
        cutoff -= timedelta(days=1)

    return cutoff
