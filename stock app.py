import hashlib
import html
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from threading import local
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont


# ==================================================
# 설정
# ==================================================

KST = ZoneInfo("Asia/Seoul")

CATEGORIES = {
    "company": "기업분석",
    "industry": "산업분석",
    "invest": "투자전략",
    "economy": "경제분석",
}

ALLOWED_HOSTS = {
    "finance.naver.com",
    "stock.naver.com",
    "ssl.pstatic.net",
    "stock.pstatic.net",
    "kr.investing.com",
    "www.shinhansec.com",
}

HEADERS = {
    "User-Agent": "Gangppi/1.0 (personal research reader)"
}

HTTP_LOCAL = local()

THEMES = {
    "반도체 · 메모리": [
        "반도체", "HBM", "DRAM", "D램", "낸드",
        "NAND", "파운드리", "메모리", "SK하이닉스",
    ],
    "AI · 전력 인프라": [
        "데이터센터", "데이터 센터", "AI 투자",
        "전력망", "변압기", "배전", "전력기기",
        "인공지능", "발전설비",
    ],
    "배터리 · 전기차": [
        "이차전지", "2차전지", "배터리", "양극재",
        "전기차", "리튬", "음극재", "ESS",
    ],
    "금리 · 거시경제": [
        "기준금리", "연준", "FOMC", "인플레이션",
        "소비자물가", "통화정책", "미국 금리", "고용지표",
    ],
    "무역 · 중국 정책": [
        "관세", "수출규제", "트럼프", "중국 경제",
        "중국 증시", "무역분쟁", "무역 정책",
    ],
    "에너지 · 원자재": [
        "국제유가", "유가", "원유", "천연가스",
        "정유", "에너지 가격", "석유", "원자재",
    ],
    "주주환원 · 배당": [
        "주주환원", "자사주", "배당",
        "소각", "기업가치 제고",
    ],
    "바이오 · 헬스케어": [
        "바이오", "임상", "신약", "의약품",
        "바이오시밀러", "의료기기",
    ],
    "건설 · 부동산": [
        "주택", "부동산", "분양",
        "건설", "플랜트", "주택담보대출",
    ],
    "은행 · 금융": [
        "은행", "순이자", "예대마진",
        "대출", "보험", "금융지주",
    ],
    "소비 · 유통": [
        "음식료", "식품", "유통",
        "소매", "소비재", "화장품",
    ],
    "디지털 자산": [
        "비트코인", "이더리움", "암호화폐",
        "가상자산", "스테이블코인",
    ],
}


def normalized(text):
    return " ".join(text.split())


def digest(text):
    return hashlib.sha256(
        normalized(text).encode()
    ).hexdigest()


# ==================================================
# 데이터베이스
# ==================================================

class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(
            parents=True, exist_ok=True
        )

    @contextmanager
    def connection(self):
        con = sqlite3.connect(
            self.path, timeout=30
        )
        con.row_factory = sqlite3.Row

        try:
            with con:
                yield con
        finally:
            con.close()


class Portfolio(Database):
    def __init__(self, path):
        super().__init__(path)

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

    def all(self):
        with self.connection() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM assets ORDER BY rowid"
                )
            ]

    def save(
        self, name, kind, quantity,
        price, currency, asset_id=None,
    ):
        if (
            not name.strip()
            or quantity <= 0
            or price < 0
            or currency not in ("KRW", "USD")
        ):
            raise ValueError(
                "종목명, 양수 수량, "
                "0 이상 매수가를 확인하세요."
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


class ResearchStore(Database):
    def __init__(self, path=None):
        super().__init__(
            path or os.getenv(
                "RESEARCH_DB",
                "data/research.db",
            )
        )

        with self.connection() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS report_catalog (
                    url TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    market TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    body TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY,
                    market TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    document_ids TEXT NOT NULL
                );
            """)

    def save_document(self, doc, connection=None):
        date = datetime.fromisoformat(
            doc["published_at"]
        )

        if date.tzinfo is None:
            raise ValueError(
                "발행 시각에 시간대가 필요합니다."
            )

        if date > datetime.now(KST) + timedelta(minutes=5):
            raise ValueError(
                "미래 발행 자료는 저장하지 않습니다."
            )

        if len(normalized(doc["body"])) < 200:
            raise ValueError(
                "본문이 부족합니다. "
                "제목만으로 정리하지 않습니다."
            )

        if doc["market"] not in (
            "국내", "해외", "비트코인"
        ):
            raise ValueError(
                "지원하지 않는 시장입니다."
            )

        doc_id = hashlib.sha256(
            doc["url"].encode()
        ).hexdigest()[:20]

        def write(con):
            con.execute(
                """
                INSERT INTO documents
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    market=excluded.market,
                    publisher=excluded.publisher,
                    kind=excluded.kind,
                    published_at=excluded.published_at,
                    fetched_at=excluded.fetched_at,
                    body=excluded.body,
                    body_hash=excluded.body_hash,
                    author=excluded.author
                """,
                (
                    doc_id,
                    doc["url"],
                    doc["title"],
                    doc["market"],
                    doc["publisher"],
                    doc["kind"],
                    date.astimezone(KST).isoformat(),
                    datetime.now(KST).isoformat(),
                    doc["body"],
                    digest(doc["body"]),
                    doc.get("author", ""),
                ),
            )

        if connection is not None:
            write(connection)
        else:
            with self.connection() as con:
                write(con)

        return doc_id

    def save_documents(self, documents):
        ids = []

        with self.connection() as con:
            for doc in documents:
                ids.append(
                    self.save_document(
                        doc, connection=con
                    )
                )

        return ids

    def documents(
        self, market, include_body=True
    ):
        now = datetime.now(KST)
        lower = (
            now - timedelta(days=6)
        ).replace(
            hour=0, minute=0,
            second=0, microsecond=0,
        )

        columns = (
            "*"
            if include_body
            else
            "id,url,title,market,publisher,kind,"
            "published_at,fetched_at,body_hash,author"
        )

        with self.connection() as con:
            return [
                dict(row)
                for row in con.execute(
                    f"""
                    SELECT {columns}
                    FROM documents
                    WHERE market=?
                      AND published_at>=?
                      AND published_at<=?
                    ORDER BY published_at DESC
                    """,
                    (
                        market,
                        lower.isoformat(),
                        now.isoformat(),
                    ),
                )
            ]

    def document(self, doc_id):
        with self.connection() as con:
            row = con.execute(
                "SELECT * FROM documents WHERE id=?",
                (doc_id,),
            ).fetchone()

        return dict(row) if row else None

    def existing_urls(self):
        with self.connection() as con:
            return {
                row["url"]
                for row in con.execute(
                    "SELECT url FROM documents"
                )
            }

    def document_kinds(self):
        with self.connection() as con:
            return {
                row["url"]: row["kind"]
                for row in con.execute(
                    "SELECT url,kind FROM documents"
                )
            }

    def save_catalog(self, rows):
        keys = (
            "url", "title", "publisher",
            "published_at", "category",
            "api_url", "author",
        )

        with self.connection() as con:
            for row in rows:
                value = {
                    key: row.get(key, "")
                    for key in keys
                }

                con.execute(
                    """
                    INSERT OR REPLACE INTO report_catalog
                    VALUES (?, ?, ?)
                    """,
                    (
                        row["url"],
                        json.dumps(
                            value,
                            ensure_ascii=False,
                        ),
                        row["published_at"],
                    ),
                )

    def catalog(self, url=None):
        with self.connection() as con:
            if url:
                row = con.execute(
                    """
                    SELECT payload
                    FROM report_catalog
                    WHERE url=?
                    """,
                    (url,),
                ).fetchone()

                return (
                    json.loads(row["payload"])
                    if row else None
                )

            now = datetime.now(KST)
            lower = (
                now - timedelta(days=6)
            ).replace(
                hour=0, minute=0,
                second=0, microsecond=0,
            )

            return [
                json.loads(row["payload"])
                for row in con.execute(
                    """
                    SELECT payload
                    FROM report_catalog
                    WHERE published_at>=?
                      AND published_at<=?
                    ORDER BY published_at DESC
                    """,
                    (
                        lower.isoformat(),
                        now.isoformat(),
                    ),
                )
            ]

    def log_run(
        self, source, started_at,
        status, detail,
    ):
        with self.connection() as con:
            con.execute(
                """
                INSERT INTO collection_runs
                VALUES (NULL, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    started_at,
                    datetime.now(KST).isoformat(),
                    status,
                    json.dumps(
                        detail,
                        ensure_ascii=False,
                    ),
                ),
            )

    def runs(self):
        with self.connection() as con:
            return [
                dict(row)
                for row in con.execute(
                    """
                    SELECT *
                    FROM collection_runs
                    ORDER BY id DESC
                    LIMIT 10
                    """
                )
            ]

    def save_analysis(
        self, market, fingerprint,
        payload, document_ids,
    ):
        with self.connection() as con:
            con.execute(
                """
                INSERT INTO analyses
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market,
                    fingerprint,
                    datetime.now(KST).isoformat(),
                    "로컬 규칙 기반",
                    "무료 로컬 발췌",
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                    json.dumps(document_ids),
                ),
            )

    def analysis(self, market, fingerprint):
        with self.connection() as con:
            row = con.execute(
                """
                SELECT *
                FROM analyses
                WHERE market=? AND fingerprint=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (market, fingerprint),
            ).fetchone()

        if not row:
            return None

        result = dict(row)
        result["payload"] = json.loads(
            result["payload"]
        )
        result["document_ids"] = json.loads(
            result["document_ids"]
        )

        return result


# ==================================================
# HTTPS 요청
# ==================================================

class SourceAccessError(ValueError):
    pass


class CollectionIncomplete(ValueError):
    pass


def http_session():
    if not hasattr(HTTP_LOCAL, "session"):
        HTTP_LOCAL.session = requests.Session()
    return HTTP_LOCAL.session


def fetch(url, max_bytes=20_000_000):
    for _ in range(6):
        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError(
                "지원하는 공개 원문 HTTPS 주소가 아닙니다."
            )

        with http_session().get(
            url,
            headers=HEADERS,
            timeout=(10, 35),
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.is_redirect:
                url = urljoin(
                    url,
                    response.headers["Location"],
                )
                continue

            if response.status_code == 403:
                raise SourceAccessError(
                    f"{parsed.hostname} HTTP 403: "
                    "이 서버의 자동 요청이 거부됐습니다."
                )

            response.raise_for_status()

            chunks = []
            total = 0

            for chunk in response.iter_content(65536):
                total += len(chunk)

                if total > max_bytes:
                    raise ValueError(
                        "다운로드 용량 한도를 초과했습니다."
                    )

                chunks.append(chunk)

            response._content = b"".join(chunks)
            response._content_consumed = True
            return response

    raise ValueError(
        "리다이렉트 횟수 초과"
    )


def parse_date(value):
    value = value.strip()

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            for fmt in (
                "%Y.%m.%d",
                "%y.%m.%d",
                "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    result = datetime.strptime(
                        value, fmt
                    )
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(
                    "발행일을 확인할 수 없습니다."
                )

    if result.tzinfo is None:
        result = result.replace(tzinfo=KST)

    return result.astimezone(KST)


# ==================================================
# PDF: 선택한 한 건만 별도 프로세스에서 처리
# ==================================================

PDF_PARSER = r'''
import json
import sys

try:
    import resource
    resource.setrlimit(
        resource.RLIMIT_AS,
        (384*1024*1024, 384*1024*1024)
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (30, 30)
    )
except ImportError:
    pass

from pypdf import PdfReader

try:
    reader = PdfReader(sys.argv[1])

    if len(reader.pages) > 200:
        raise ValueError("200쪽을 초과한 PDF입니다.")

    parts = []
    total = 0

    for page in reader.pages:
        text = page.extract_text() or ""
        total += len(text)

        if total > 1500000:
            raise ValueError(
                "본문 크기가 처리 한도를 초과했습니다."
            )

        parts.append(text)

    result = "\n".join(parts)

    if len(result.strip()) < 200:
        raise ValueError(
            "본문 미확보: 이미지 PDF는 OCR이 필요합니다."
        )

    print(json.dumps(
        {"text": result},
        ensure_ascii=False
    ))

except Exception as error:
    print(json.dumps(
        {"error": type(error).__name__ + ": " + str(error)[:250]},
        ensure_ascii=False
    ))
    sys.exit(2)
'''


def extract_pdf_isolated(content):
    if (
        len(content) > 20_000_000
        or not content.lstrip().startswith(b"%PDF")
    ):
        raise ValueError(
            "유효한 20MB 이하 PDF가 아닙니다."
        )

    fd, path = tempfile.mkstemp(
        prefix="gangppi-",
        suffix=".pdf",
    )

    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    PDF_PARSER,
                    path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=40,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "PDF 처리 시간이 초과됐습니다. "
                "앱 전체를 멈추지 않고 이 파일 처리를 중단합니다."
            ) from None

        try:
            payload = json.loads(
                result.stdout
            )
        except (ValueError, TypeError):
            raise ValueError(
                "PDF 처리 프로세스가 종료됐습니다. "
                "메모리·시간 한도를 초과했을 수 있습니다."
            ) from None

        if (
            result.returncode
            or payload.get("error")
        ):
            raise ValueError(
                payload.get(
                    "error",
                    "PDF 처리 실패",
                )
            )

        return payload["text"]

    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def pdf_text(url_or_bytes):
    content = (
        url_or_bytes
        if isinstance(url_or_bytes, bytes)
        else fetch(url_or_bytes).content
    )
    return extract_pdf_isolated(content)


# ==================================================
# 네이버 최근 7일 공개 본문 수집
# ==================================================

def naver_reports(category, page_size=50):
    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=6)

    result = []
    seen = set()
    index = 0

    while True:
        url = (
            "https://stock.naver.com/api/"
            "stockSecurity/researches/v2/"
            f"{category}?index={index}&size={page_size}"
        )

        data = fetch(url).json()

        if (
            not isinstance(data, dict)
            or not isinstance(data.get("items"), list)
        ):
            raise CollectionIncomplete(
                "네이버 목록 형식이 변경됐습니다."
            )

        items = data["items"]

        if not items:
            if data.get("hasNext"):
                raise CollectionIncomplete(
                    "빈 목록에 다음 페이지가 표시됐습니다."
                )
            break

        dates = []
        new_ids = 0

        for item in items:
            nid = str(item.get("nid", ""))

            if (
                not nid.isdigit()
                or not item.get("writeDate")
                or not item.get("title")
            ):
                raise CollectionIncomplete(
                    "필수 보고서 정보가 누락됐습니다."
                )

            date = parse_date(item["writeDate"])
            dates.append(date.date())

            if nid in seen:
                continue

            seen.add(nid)
            new_ids += 1

            if not cutoff <= date.date() <= today:
                continue

            body = BeautifulSoup(
                item.get("content") or "",
                "html.parser",
            ).get_text(" ", strip=True)

            result.append({
                "title": item["title"],
                "url": (
                    "https://stock.naver.com/"
                    f"research/{category}/{nid}"
                ),
                "published_at": date.isoformat(),
                "publisher": (
                    item.get("brokerName")
                    or "발행사 확인 필요"
                ),
                "api_url": (
                    "https://stock.naver.com/api/"
                    "stockSecurity/researches/v2/"
                    f"{category}/{nid}"
                ),
                "category": category,
                "author": item.get(
                    "analystName", ""
                ),
                "public_body": body,
            })

        if (
            any(date < cutoff for date in dates)
            or not data.get("hasNext")
        ):
            break

        if not new_ids:
            raise CollectionIncomplete(
                "같은 목록이 반복되어 수집을 중단했습니다."
            )

        index += 1
        time.sleep(0.15)

    return result


def collect_naver_fast(store, progress=None):
    started = datetime.now(KST).isoformat()
    kinds = store.document_kinds()

    saved = 0
    reused = 0
    candidates = 0
    errors = []
    warnings = []
    completed = []

    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:
        jobs = {
            pool.submit(
                naver_reports,
                category,
                page_size=50,
            ): category
            for category in CATEGORIES
        }

        for future in as_completed(jobs):
            category = jobs[future]

            try:
                rows = future.result()
                candidates += len(rows)

                store.save_catalog(rows)
                docs = []

                for row in rows:
                    previous = kinds.get(
                        row["url"]
                    )

                    # 이미 저장한 PDF 전문을
                    # 짧은 공개 발췌로 덮어쓰지 않습니다.
                    if (
                        previous
                        and "공개페이지" not in previous
                    ):
                        reused += 1
                        continue

                    body = row.get(
                        "public_body", ""
                    )

                    if len(normalized(body)) < 200:
                        warnings.append(
                            row["title"][:70]
                            + ": 공개 본문이 짧아 목록만 저장했습니다."
                        )
                        continue

                    docs.append({
                        "title": row["title"],
                        "url": row["url"],
                        "market": "국내",
                        "publisher": row["publisher"],
                        "kind": (
                            "증권사 공개페이지 발췌 · "
                            + CATEGORIES[category]
                            + " (PDF 전체 아님)"
                        ),
                        "published_at": row[
                            "published_at"
                        ],
                        "body": body,
                        "author": row.get(
                            "author", ""
                        ),
                    })

                store.save_documents(docs)

                saved += len(docs)
                completed.append(category)

                if progress:
                    progress(
                        f"{CATEGORIES[category]} 저장 완료 · "
                        f"공개 본문 {saved}건 · "
                        f"기존 전문 {reused}건 유지"
                    )

            except Exception as error:
                errors.append(
                    CATEGORIES[category]
                    + ": "
                    + str(error)[:300]
                )

    result = {
        "saved": saved,
        "reused": reused,
        "candidates": candidates,
        "errors": errors,
        "warnings": warnings,
        "blocked": any(
            "403" in error
            for error in errors
        ),
        "completed_categories": completed,
        "coverage": (
            "최근 7일 공개페이지 본문 발췌입니다. "
            "PDF 전체 내용이 아닙니다. "
            "기존 PDF 전문은 유지합니다."
        ),
    }

    store.log_run(
        "naver-fast",
        started,
        "partial" if errors or warnings else "success",
        result,
    )

    return result


def naver_document(report):
    detail = fetch(
        report["api_url"]
    ).json()

    if (
        not isinstance(detail, dict)
        or not detail.get("attachUrl")
    ):
        raise ValueError(
            "PDF 주소를 확보하지 못했습니다."
        )

    return {
        "title": detail.get(
            "title", report["title"]
        ),
        "url": report["url"],
        "publisher": (
            detail.get("brokerName")
            or report["publisher"]
        ),
        "published_at": parse_date(
            detail.get("writeDate")
            or report["published_at"]
        ).isoformat(),
        "body": pdf_text(
            detail["attachUrl"]
        ),
        "market": "국내",
        "kind": (
            "증권사 "
            + CATEGORIES[report["category"]]
            + " PDF 전문"
        ),
        "author": (
            detail.get("analystName")
            or report.get("author", "")
        ),
    }


def upgrade_naver_pdf(store, url):
    report = store.catalog(url=url)

    if not report:
        raise ValueError(
            "목록을 찾지 못했습니다. "
            "네이버 빠른 수집을 먼저 실행하세요."
        )

    parsed = urlparse(report["url"])

    if (
        parsed.hostname != "stock.naver.com"
        or not re.fullmatch(
            r"/research/(company|industry|invest|economy)/\d+",
            parsed.path,
        )
    ):
        raise ValueError(
            "지원하는 네이버 보고서 주소가 아닙니다."
        )

    doc = naver_document(report)
    store.save_document(doc)
    return len(doc["body"])


# ==================================================
# 인베스팅
# ==================================================

def investing_feed():
    root = ElementTree.fromstring(
        fetch(
            "https://kr.investing.com/rss/news.rss"
        ).content
    )

    items = []

    for item in root.findall(".//item"):
        title = item.findtext("title")
        url = item.findtext("link")

        if title and url:
            items.append({
                "title": title,
                "url": url,
            })

    if not items:
        raise ValueError(
            "RSS 항목을 찾지 못했습니다."
        )

    return items


def article_metadata(soup):
    objects = []

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(
                script.get_text()
            )
            objects.extend(
                data
                if isinstance(data, list)
                else [data]
            )
        except (ValueError, TypeError):
            continue

    while objects:
        data = objects.pop(0)

        if not isinstance(data, dict):
            continue

        objects.extend(
            data.get("@graph", [])
        )

        types = data.get("@type", [])

        if isinstance(types, str):
            types = [types]

        if any(
            value in (
                "NewsArticle",
                "Article",
                "AnalysisNewsArticle",
            )
            for value in types
        ):
            return data

    raise ValueError(
        "기사 메타데이터를 확인할 수 없습니다."
    )


def investing_article(item):
    response = fetch(item["url"])

    soup = BeautifulSoup(
        response.content,
        "html.parser",
    )

    meta = article_metadata(soup)

    if str(
        meta.get(
            "isAccessibleForFree", "true"
        )
    ).lower() == "false":
        raise ValueError(
            "구독 또는 접근 제한된 기사입니다."
        )

    date = meta.get("datePublished")

    if (
        not date
        or not re.search(
            r"(Z|[+-]\d{2}:?\d{2})$",
            date,
        )
    ):
        raise ValueError(
            "기사 발행 시각을 확인할 수 없습니다."
        )

    content = soup.select_one(
        '[data-test="article-content"], '
        '[class*="article_WYSIWYG"]'
    )

    if not content:
        raise ValueError(
            "기사 본문을 확보하지 못했습니다."
        )

    for tag in content.select(
        "script,style,aside,button"
    ):
        tag.decompose()

    body = content.get_text(
        "\n", strip=True
    )

    if len(body) < 200:
        raise ValueError(
            "기사 본문이 너무 짧습니다."
        )

    author = meta.get("author", {})

    if isinstance(author, list):
        author = ", ".join(
            value.get("name", "")
            for value in author
            if isinstance(value, dict)
        )
    elif isinstance(author, dict):
        author = author.get("name", "")

    return {
        "title": (
            meta.get("headline")
            or item["title"]
        ),
        "url": item["url"],
        "body": body,
        "market": "해외",
        "publisher": "Investing.com",
        "published_at": parse_date(
            date
        ).isoformat(),
        "kind": (
            "기고자 분석"
            if "/analysis/" in item["url"]
            else "언론 보도"
        ),
        "author": str(author),
    }


def collect_investing(store, progress=None):
    started = datetime.now(KST).isoformat()

    saved = 0
    reused = 0
    errors = []
    blocked = False
    candidates = []

    existing = store.existing_urls()
    today = datetime.now(KST).date()
    lower = today - timedelta(days=6)

    try:
        candidates = investing_feed()

        for index, item in enumerate(candidates):
            if item["url"] in existing:
                reused += 1
                continue

            if progress:
                progress(
                    "인베스팅 본문 확인 "
                    f"{index + 1}/{len(candidates)}"
                )

            try:
                doc = investing_article(item)

                if (
                    lower
                    <= parse_date(
                        doc["published_at"]
                    ).date()
                    <= today
                ):
                    store.save_document(doc)
                    saved += 1

            except SourceAccessError as error:
                errors.append(str(error))
                blocked = True
                break

            except Exception as error:
                errors.append(
                    item["title"][:60]
                    + ": "
                    + str(error)[:250]
                )

    except SourceAccessError as error:
        errors.append(str(error))
        blocked = True

    except Exception as error:
        errors.append(str(error)[:300])

    result = {
        "saved": saved,
        "reused": reused,
        "errors": errors,
        "blocked": blocked,
        "candidates": len(candidates),
        "coverage": (
            "인베스팅 최신 RSS 누적분입니다. "
            "과거 일주일 전체를 소급 확보하지는 못합니다."
        ),
    }

    store.log_run(
        "investing",
        started,
        "partial" if errors else "success",
        result,
    )

    return result


# ==================================================
# 무료 분류와 원문 발췌
# ==================================================

def select_documents(documents):
    result = []
    seen = set()

    for doc in documents:
        if doc["body_hash"] not in seen:
            result.append(doc)
            seen.add(doc["body_hash"])

    return result


def analysis_fingerprint(documents, assets):
    return digest(json.dumps(
        {
            "engine": "local-extractive-v1",
            "documents": sorted(
                (
                    doc["id"],
                    doc["body_hash"],
                    doc["title"],
                    doc["publisher"],
                )
                for doc in documents
            ),
            "holdings": sorted(
                (
                    asset["name"],
                    asset["kind"],
                )
                for asset in assets
            ),
        },
        sort_keys=True,
        ensure_ascii=False,
    ))


def choose_theme(doc):
    title = normalized(
        doc["title"]
    ).casefold()

    body = normalized(
        doc["body"]
    ).casefold()

    lead = body[:2500]
    best_theme = "기업 실적 · 업황"
    best_score = 0

    for theme, terms in THEMES.items():
        hits = [
            term.casefold()
            for term in terms
            if (
                term.casefold() in body
                or term.casefold() in title
            )
        ]

        title_hits = [
            term for term in hits
            if term in title
        ]

        lead_hits = [
            term for term in hits
            if term in lead
        ]

        score = sum(
            12 * (term in title)
            + 5 * (term in lead)
            + 0.25
            for term in hits
        )

        if not title_hits and len(lead_hits) < 2:
            score = 0

        if score > best_score:
            best_theme = theme
            best_score = score

    return best_theme


def excerpt(doc, theme):
    body = normalized(doc["body"])

    sentences = re.split(
        r"(?<=[.!?。])\s+|(?<=다\.)\s*",
        body,
    )

    keywords = THEMES.get(theme, [])
    candidates = []

    for number, sentence in enumerate(sentences):
        if len(sentence) < 35:
            continue

        if any(
            word in sentence
            for word in (
                "무단 배포",
                "투자판단의 최종",
                "Compliance Notice",
                "투자의견 비율",
            )
        ):
            continue

        for start in range(
            0, len(sentence), 220
        ):
            piece = sentence[start:start + 320]

            hangul = sum(
                "가" <= char <= "힣"
                for char in piece
            )

            if (
                len(piece) < 35
                or hangul < 20
                or "@" in piece
            ):
                continue

            if not re.search(
                r"증가|감소|확대|성장|상승|하락|"
                r"개선|악화|기록|전망|예상|유지|"
                r"부담|회복|축소",
                piece,
            ):
                continue

            score = sum(
                term.casefold()
                in piece.casefold()
                for term in keywords
            ) * 3

            score += 2 * bool(re.search(
                r"\d[\d,.]*\s*(%|억원|조원|원|달러)",
                piece,
            ))

            score += min(hangul / 60, 3)
            score -= number * 0.015

            candidates.append(
                (score, piece)
            )

    if not candidates:
        return body[:300]

    return max(
        candidates,
        key=lambda row: row[0],
    )[1]


def representative_documents(documents):
    ordered = sorted(
        documents,
        key=lambda doc: doc["published_at"],
        reverse=True,
    )

    result = []
    seen = set()

    for doc in ordered:
        if doc["publisher"] not in seen:
            result.append(doc)
            seen.add(doc["publisher"])

    for doc in ordered:
        if doc not in result:
            result.append(doc)

    return result[:3]


def analyze_free(
    documents,
    assets,
    market,
    load_document,
    progress=None,
):
    documents = select_documents(documents)

    if not documents:
        raise ValueError(
            "확보한 실제 본문이 없습니다."
        )

    def full(doc):
        result = load_document(
            doc["id"]
        )

        if not result:
            raise ValueError(
                "저장된 원문을 찾지 못했습니다."
            )

        return result

    grouped = defaultdict(list)

    for index, doc in enumerate(documents):
        current = full(doc)

        grouped[
            choose_theme(current)
        ].append(doc)

        del current

        if progress and (
            index % 20 == 0
            or index == len(documents) - 1
        ):
            progress(
                f"원문 분류 "
                f"{index + 1}/{len(documents)}건"
            )

    ranked = sorted(
        grouped.items(),
        key=lambda pair: (
            len({
                doc["publisher"]
                for doc in pair[1]
            }) * 2
            + math.log1p(len(pair[1])),
            len(pair[1]),
        ),
        reverse=True,
    )

    issues = []

    for theme, group in ranked[:3]:
        representatives = (
            representative_documents(group)
        )

        facts = [
            {
                "text": doc["title"],
                "source_id": doc["id"],
                "quote": excerpt(
                    full(doc), theme
                ),
            }
            for doc in representatives
        ]

        related = []
        used_names = set()

        for asset in assets:
            name = normalized(
                asset["name"]
            )

            if (
                len(name) < 2
                or name in used_names
            ):
                continue

            for doc in group:
                body = normalized(
                    full(doc)["body"]
                )

                location = body.casefold().find(
                    name.casefold()
                )

                if location < 0:
                    continue

                start = max(
                    0, location - 50
                )

                end = min(
                    len(body),
                    location + len(name) + 120,
                )

                related.append({
                    "name": asset["name"],
                    "reason": (
                        "등록 자산명이 본문에서 확인됩니다. "
                        "투자 영향은 별도로 판단하세요."
                    ),
                    "source_id": doc["id"],
                    "quote": body[start:end],
                })

                used_names.add(name)
                break

        publishers = len({
            doc["publisher"]
            for doc in group
        })

        basis = (
            "공개페이지 발췌 포함"
            if any(
                "공개페이지" in doc["kind"]
                for doc in group
            )
            else "저장된 PDF·기사 본문"
        )

        issues.append({
            "title": theme + " 리서치 모아보기",
            "category": theme,
            "basis": basis,
            "summary": facts[0]["quote"],
            "impact": (
                f"관련 본문 {len(group)}건 · "
                f"발행사/매체 {publishers}곳. "
                "같은 의견의 합의가 아니라 수집 범위입니다."
            ),
            "check_note": (
                "대표 원문과 서로 다른 출처를 비교하세요. "
                "이 화면은 새로운 AI 해석이나 "
                "투자 의견을 생성하지 않습니다."
            ),
            "facts": facts,
            "risks": [
                "키워드 분류와 원문 발췌 결과입니다. "
                "분류 오류나 문맥 생략이 있을 수 있습니다."
            ],
            "checks": [
                "발행일과 수치의 기준 시점을 확인하세요.",
                "작성자의 전망과 실제 발표 실적을 구분하세요.",
            ],
            "related_assets": related,
            "names": [
                asset["name"]
                for asset in related
            ],
        })

    return {
        "issues": issues,
        "method": "무료 원문 발췌 · 키워드 분류",
        "coverage": {
            "documents": len(documents),
            "api_calls": 0,
        },
    }


# ==================================================
# 카드뉴스 이미지
# ==================================================

def render_card(market, issues, generated_at):
    if not 1 <= len(issues) <= 3:
        raise ValueError(
            "카드로 만들 실제 정리 결과가 필요합니다."
        )

    font_path = os.getenv(
        "CARD_FONT_PATH",
        "/usr/share/fonts/opentype/noto/"
        "NotoSansCJK-Bold.ttc",
    )

    if not Path(font_path).exists():
        raise ValueError(
            "한글 폰트가 없습니다. "
            "packages.txt를 확인하세요."
        )

    image = Image.new(
        "RGB", (1080, 1350), "white"
    )
    draw = ImageDraw.Draw(image)
    fonts = {}

    def font(size):
        if size not in fonts:
            fonts[size] = ImageFont.truetype(
                font_path, size
            )
        return fonts[size]

    def write(
        text, x, y,
        size=30, color="#182127",
    ):
        draw.text(
            (x, y), text,
            font=font(size),
            fill=color,
        )

    def wrapped(
        text, x, y,
        size, width, max_lines,
    ):
        rows = []
        line = ""

        for char in text:
            if draw.textlength(
                line + char,
                font=font(size),
            ) > width:
                rows.append(line)
                line = ""

            line += char

        if line:
            rows.append(line)

        if len(rows) > max_lines:
            rows = rows[:max_lines]
            last = rows[-1]

            while (
                last
                and draw.textlength(
                    last + "…",
                    font=font(size),
                ) > width
            ):
                last = last[:-1]

            rows[-1] = last + "…"

        for row in rows:
            write(row, x, y, size)
            y += size + 12

    write(
        "강민 주식비서 · 강삐",
        50, 30, 25, "#68717a",
    )
    write(
        f"아침 7시 · {market}",
        50, 80, 29, "#008783",
    )
    write(
        f"오늘 주목할 주제 {len(issues)}",
        50, 130, 61,
    )
    write(
        "정리 생성 "
        + generated_at[:16]
        + " KST",
        50, 225, 23, "#68717a",
    )

    for index, item in enumerate(issues):
        y = 285 + index * 305

        draw.line(
            (40, y - 15, 1040, y - 15),
            fill="#dce1e5",
            width=2,
        )

        wrapped(
            item["category"],
            50, y, 27, 960, 1,
        )
        wrapped(
            item["title"],
            50, y + 45, 42, 960, 2,
        )
        wrapped(
            item["summary"],
            50, y + 155, 27, 960, 2,
        )

        draw.rectangle(
            (40, y + 244, 1040, y + 288),
            fill="#eef1f3",
        )

        wrapped(
            item.get(
                "basis",
                "저장된 실제 원문 발췌",
            ),
            55, y + 248, 23, 960, 1,
        )

    write(
        "공개페이지/PDF 범위와 출처는 앱에서 확인",
        50, 1250, 23, "#68717a",
    )
    write(
        "원문 발췌 · 투자 추천이 아닙니다.",
        50, 1290, 22, "#68717a",
    )

    result = BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


# ==================================================
# 모바일 UI
# ==================================================

st.set_page_config(
    page_title="강삐 · 강민 주식비서",
    page_icon="☀️",
    layout="centered",
)

st.markdown("""
<style>
.block-container {
    max-width: 560px;
    padding: 1.4rem 1rem 5rem;
}
h1 { font-size: 1.85rem !important; }
h2 { font-size: 1.4rem !important; }
h3 { font-size: 1.08rem !important; }
p { line-height: 1.6; }
.brand {
    font-size: 13px;
    color: #8590a3;
}
.badge {
    display: inline-block;
    background: #eaf0ff;
    color: #425bd0;
    padding: 4px 10px;
    border-radius: 8px;
    font-size: 12px;
}
button { min-height: 44px !important; }

[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
}
[data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
}

.st-key-navigation {
    background: #f6f7fb;
    border: 1px solid #e3e6ef;
    border-radius: 12px;
    padding: 4px;
}
.st-key-navigation [data-testid="stHorizontalBlock"] {
    gap: 4px !important;
}
.st-key-navigation button {
    border: 0 !important;
    background: transparent !important;
}
.st-key-navigation button p {
    color: #263249 !important;
    white-space: nowrap;
}
.st-key-navigation button[kind="primary"] {
    background: #505bd1 !important;
}
.st-key-navigation button[kind="primary"] p {
    color: white !important;
}

[data-testid="stRadio"] > div[role="radiogroup"] {
    display: flex;
    background: #f6f7fb;
    border-radius: 10px;
    padding: 4px;
    gap: 0 !important;
}
[data-testid="stRadio"] label[data-baseweb="radio"] {
    flex: 1;
    justify-content: center;
    padding: 8px 4px;
    margin: 0 !important;
    border-radius: 7px;
}
[data-testid="stRadio"] label[data-baseweb="radio"] p {
    color: #263249 !important;
}
[data-testid="stRadio"]
label[data-baseweb="radio"]:has(input:checked) {
    background: #505bd1;
}
[data-testid="stRadio"]
label[data-baseweb="radio"]:has(input:checked) p {
    color: white !important;
}
[data-testid="stRadio"]
label[data-baseweb="radio"] > div:first-child {
    display: none;
}
[data-testid="stToolbar"] {
    display: none;
}
@media(max-width: 600px) {
    .block-container {
        padding-top: 1rem;
    }
}
</style>
""", unsafe_allow_html=True)


store = ResearchStore()

portfolio = Portfolio(
    os.getenv(
        "PORTFOLIO_DB",
        "data/portfolio.db",
    )
)

st.session_state.setdefault(
    "page", "오늘"
)


def go(page, index=0):
    st.session_state.page = page
    st.session_state.selected = index


def edit(asset=None):
    st.session_state.asset = asset or {}
    st.session_state.form_revision = (
        st.session_state.get(
            "form_revision", 0
        ) + 1
    )
    st.session_state.form_open = True


def badge(text):
    st.markdown(
        '<span class="badge">'
        + html.escape(text)
        + "</span>",
        unsafe_allow_html=True,
    )


def context(market):
    docs = select_documents(
        store.documents(
            market,
            include_body=False,
        )
    )

    assets = portfolio.all()

    fingerprint = analysis_fingerprint(
        docs, assets
    )

    saved = store.analysis(
        market, fingerprint
    )

    return docs, assets, fingerprint, saved


@st.cache_data(
    show_spinner=False,
    max_entries=6,
)
def image_card(market, issues, date):
    return render_card(
        market, issues, date
    )


def source_view(docs, market):
    if not docs:
        st.info(
            "확보한 본문이 없습니다. "
            "톱니바퀴에서 빠른 수집을 실행하세요."
        )
        return

    st.caption(
        f"본문 {len(docs)}건 · "
        "목록은 10건씩, 본문은 한 건만 표시합니다."
    )

    page = st.selectbox(
        "원문 목록 페이지",
        range(
            1,
            (len(docs) + 9) // 10 + 1,
        ),
        key="source_page_" + market,
    )

    rows = docs[
        (page - 1) * 10:page * 10
    ]

    mapping = {
        doc["id"]: doc
        for doc in rows
    }

    key = st.selectbox(
        "읽을 원문",
        list(mapping),
        format_func=lambda value: (
            mapping[value]["title"]
        ),
        key=f"source_{market}_{page}",
    )

    doc = mapping[key]

    st.caption(
        doc["publisher"]
        + " · "
        + doc["kind"]
        + " · "
        + doc["published_at"][:10]
    )

    st.link_button(
        "원문 열기",
        doc["url"],
    )

    if st.checkbox(
        "선택한 본문 미리보기",
        key="preview_" + market,
    ):
        full = store.document(key)

        if full:
            st.text_area(
                "본문 일부",
                full["body"][:4000],
                height=230,
                disabled=True,
                key="body_" + key,
            )


def summarize(
    market, docs,
    assets, fingerprint,
):
    st.caption(
        "최근 7일 저장 본문을 키워드로 분류하고 "
        "실제 문장을 발췌합니다. "
        "유료 AI API를 사용하지 않습니다."
    )

    if st.button(
        "무료로 원문 정리",
        type="primary",
        disabled=not docs,
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "저장된 원문 정리 중…"
            ):
                progress = st.empty()

                result = analyze_free(
                    docs,
                    assets,
                    market,
                    load_document=store.document,
                    progress=progress.info,
                )

                store.save_analysis(
                    market,
                    fingerprint,
                    result,
                    [
                        doc["id"]
                        for doc in docs
                    ],
                )

            st.rerun()

        except Exception as error:
            st.error(str(error))


# ==================================================
# 상단
# ==================================================

st.markdown(
    '<div class="brand">강민 주식비서</div>',
    unsafe_allow_html=True,
)

a, b = st.columns([6, 1])
a.title("강삐")

b.button(
    "⚙",
    on_click=go,
    args=("설정",),
    help="수집 및 설정",
)

now = datetime.now(KST)

st.caption(
    f"{now:%Y.%m.%d} · 최근 7일 "
    f"{now - timedelta(days=6):%m.%d}"
    f"~{now:%m.%d} · KST"
)

with st.container(key="navigation"):
    for col, label in zip(
        st.columns(4),
        ["오늘", "분석", "인기글", "내 자산"],
    ):
        col.button(
            label,
            key="nav_" + label,
            on_click=go,
            args=(label,),
            type=(
                "primary"
                if st.session_state.page == label
                else "secondary"
            ),
            use_container_width=True,
        )

page = st.session_state.page


# ==================================================
# 오늘 / 분석
# ==================================================

if page in ("오늘", "분석"):
    market = st.radio(
        "시장",
        ["국내", "해외", "비트코인"],
        horizontal=True,
        width="stretch",
        label_visibility="collapsed",
        key="market",
    )

    docs, assets, fingerprint, saved = context(
        market
    )

    if page == "오늘":
        st.header(
            "오늘 알아둘 이야기"
        )

        if saved:
            st.caption(
                "원문 발췌 · 키워드 분류 · 생성 "
                + saved["generated_at"][:16]
            )

            for index, item in enumerate(
                saved["payload"]["issues"]
            ):
                with st.container(border=True):
                    badge(
                        item["category"]
                    )

                    st.subheader(
                        item["title"]
                    )

                    st.write(
                        item["summary"]
                    )

                    st.caption(
                        item["impact"]
                    )

                    st.button(
                        "분석 보기 ›",
                        key=f"issue_{market}_{index}",
                        on_click=go,
                        args=("분석", index),
                        use_container_width=True,
                    )

        else:
            st.info(
                "아직 정리 결과가 없습니다. "
                "자료를 수집하고 아래 버튼을 눌러주세요."
            )

        summarize(
            market, docs,
            assets, fingerprint,
        )

        with st.expander(
            "확보한 실제 원문",
            expanded=not bool(saved),
        ):
            source_view(docs, market)

        with st.expander(
            "카카오톡용 카드뉴스 · 국내 / 해외"
        ):
            st.caption(
                "국내 1장·해외 1장. "
                "공유용 PNG이며 자동 발송은 미연결입니다."
            )

            for col, target in zip(
                st.columns(2),
                ["국내", "해외"],
            ):
                target_saved = context(
                    target
                )[3]

                if not target_saved:
                    col.info(
                        target + " 정리 대기"
                    )
                    continue

                try:
                    data = image_card(
                        target,
                        target_saved[
                            "payload"
                        ]["issues"],
                        target_saved[
                            "generated_at"
                        ],
                    )

                    col.download_button(
                        target + " 카드 PNG",
                        data,
                        file_name=(
                            f"gangppi-{target}.png"
                        ),
                        mime="image/png",
                        use_container_width=True,
                    )

                except Exception as error:
                    col.warning(str(error))

    elif not saved:
        st.header(
            "원문 정리 대기"
        )

        summarize(
            market, docs,
            assets, fingerprint,
        )

        source_view(docs, market)

    else:
        issues = saved[
            "payload"
        ]["issues"]

        index = st.selectbox(
            "이슈 선택",
            range(len(issues)),
            index=min(
                st.session_state.get(
                    "selected", 0
                ),
                len(issues) - 1,
            ),
            format_func=lambda value: (
                issues[value]["title"]
            ),
        )

        item = issues[index]

        mapping = {
            doc["id"]: doc
            for doc in docs
        }

        badge(
            item["category"]
        )

        st.header(
            item["title"]
        )

        mode = st.radio(
            "읽기 방식",
            ["간편", "상세"],
            index=1,
            horizontal=True,
            width="stretch",
        )

        with st.container(border=True):
            st.subheader(
                "핵심 원문 발췌"
            )

            st.write(
                item["summary"]
            )

            st.caption(
                item["impact"]
            )

        if mode == "상세":
            for fact in item["facts"]:
                doc = mapping.get(
                    fact["source_id"]
                )

                if not doc:
                    continue

                with st.container(border=True):
                    st.write(
                        "**"
                        + doc["title"]
                        + "**"
                    )

                    st.caption(
                        doc["publisher"]
                        + " · "
                        + doc["kind"]
                    )

                    st.text(
                        fact["quote"]
                    )

                    st.link_button(
                        "원문 확인",
                        doc["url"],
                    )

            with st.expander(
                "보유자산명 언급"
            ):
                if not item["related_assets"]:
                    st.write(
                        "등록 자산명이 확인되지 않았습니다."
                    )

                for asset in item["related_assets"]:
                    st.write(
                        "**"
                        + asset["name"]
                        + "**"
                    )

                    st.write(
                        asset["reason"]
                    )

                    st.text(
                        asset["quote"]
                    )

            with st.expander(
                "확인할 점",
                expanded=True,
            ):
                for text in (
                    item["checks"]
                    + item["risks"]
                ):
                    st.write(
                        "• " + text
                    )

        st.info(
            item["check_note"]
        )


# ==================================================
# 내 자산
# ==================================================

elif page == "내 자산":
    st.header(
        "내 자산"
    )

    st.button(
        "＋ 자산 추가",
        on_click=edit,
        type="primary",
        use_container_width=True,
    )

    assets = portfolio.all()

    if not assets:
        st.info(
            "보유자산을 추가해 보세요."
        )

    for asset in assets:
        with st.container(border=True):
            st.subheader(
                asset["name"]
            )

            badge(
                asset["kind"]
            )

            a, b = st.columns(2)

            a.metric(
                "보유 수량",
                f'{asset["quantity"]:g}',
            )

            b.metric(
                "평균 매수가",
                f'{asset["price"]:,.2f} '
                f'{asset["currency"]}',
            )

            a.button(
                "수정",
                key="edit_" + asset["id"],
                on_click=edit,
                args=(asset,),
                use_container_width=True,
            )

            if b.button(
                "삭제",
                key="delete_" + asset["id"],
                use_container_width=True,
            ):
                st.session_state.delete_id = (
                    asset["id"]
                )

            if (
                st.session_state.get(
                    "delete_id"
                )
                == asset["id"]
            ):
                st.warning(
                    "삭제할까요?"
                )

                if st.button(
                    "삭제 확인",
                    key="confirm_" + asset["id"],
                ):
                    portfolio.delete(
                        asset["id"]
                    )
                    st.session_state.pop(
                        "delete_id", None
                    )
                    st.session_state.form_open = False
                    st.rerun()

                if st.button(
                    "삭제 취소",
                    key="cancel_" + asset["id"],
                ):
                    st.session_state.pop(
                        "delete_id", None
                    )
                    st.rerun()

    if st.session_state.get(
        "form_open"
    ):
        asset = st.session_state.get(
            "asset", {}
        )

        with st.form(
            "asset_form_"
            + str(
                st.session_state.get(
                    "form_revision", 0
                )
            )
        ):
            name = st.text_input(
                "종목",
                asset.get("name", ""),
            )

            kinds = [
                "국내주식",
                "해외주식",
                "국내 ETF",
                "해외 ETF",
                "비트코인",
            ]

            kind = st.selectbox(
                "자산 유형",
                kinds,
                index=kinds.index(
                    asset.get(
                        "kind", "국내주식"
                    )
                ),
            )

            quantity = st.number_input(
                "수량",
                min_value=0.0,
                value=float(
                    asset.get(
                        "quantity", 1
                    )
                ),
                format="%.6f",
            )

            price = st.number_input(
                "평균 매수가",
                min_value=0.0,
                value=float(
                    asset.get(
                        "price", 0
                    )
                ),
            )

            currency = st.selectbox(
                "통화",
                ["KRW", "USD"],
                index=["KRW", "USD"].index(
                    asset.get(
                        "currency", "KRW"
                    )
                ),
            )

            a, b = st.columns(2)

            if a.form_submit_button(
                "저장",
                type="primary",
                use_container_width=True,
            ):
                try:
                    portfolio.save(
                        name,
                        kind,
                        quantity,
                        price,
                        currency,
                        asset.get("id"),
                    )

                    st.session_state.form_open = False
                    st.rerun()

                except ValueError as error:
                    st.error(str(error))

            if b.form_submit_button(
                "취소",
                use_container_width=True,
            ):
                st.session_state.form_open = False
                st.rerun()

    st.download_button(
        "보유내역 백업",
        json.dumps(
            portfolio.all(),
            ensure_ascii=False,
            indent=2,
        ),
        "portfolio.json",
        "application/json",
    )

    st.caption(
        "개인용 공용 저장소입니다. "
        "Streamlit 재배포 후 데이터 보존에는 "
        "영구 저장소가 필요합니다."
    )


# ==================================================
# 인기글
# ==================================================

elif page == "인기글":
    st.header(
        "사람들이 많이 본 이야기"
    )

    st.info(
        "실제 전체 종목 토론방 수집은 미연결입니다. "
        "가상 순위를 표시하지 않습니다."
    )


# ==================================================
# 수집 / 설정
# ==================================================

else:
    st.header(
        "자료 수집 · 설정"
    )

    st.info(
        "빠른 수집은 공개페이지 본문 발췌입니다. "
        "PDF 전문은 필요한 보고서 한 건만 "
        "선택해서 읽습니다."
    )

    for source, label in [
        (
            "naver",
            "최근 7일 네이버 빠른 수집",
        ),
        (
            "investing",
            "인베스팅 뉴스 수집",
        ),
    ]:
        if st.button(
            label,
            key="collect_" + source,
            use_container_width=True,
        ):
            with st.spinner(
                "본문 수집 중…"
            ):
                progress = st.empty()

                if source == "naver":
                    result = collect_naver_fast(
                        store,
                        progress=progress.info,
                    )
                else:
                    result = collect_investing(
                        store,
                        progress=progress.info,
                    )

            st.success(
                f'본문 저장 {result["saved"]}건 · '
                f'기존 재사용 {result["reused"]}건'
            )

            if result.get("warnings"):
                st.warning(
                    "공개 본문이 짧은 "
                    f'{len(result["warnings"])}건은 '
                    "목록만 저장했습니다. "
                    "아래에서 PDF를 선택해 읽을 수 있습니다."
                )

            for error in result["errors"][:3]:
                st.error(error)

            if result.get("blocked"):
                st.warning(
                    "이 배포 서버의 접근이 거부됐습니다. "
                    "기존 자료는 보존됩니다. "
                    "실제 본문 직접 등록을 사용할 수 있습니다."
                )

            st.caption(
                result["coverage"]
            )

    with st.expander(
        "선택한 보고서 PDF 전문 읽기"
    ):
        catalog = store.catalog()

        if catalog:
            mapping = {
                row["url"]: row
                for row in catalog
            }

            chosen = st.selectbox(
                "보고서 선택",
                list(mapping),
                format_func=lambda url: (
                    mapping[url]["title"]
                ),
            )

            if st.button(
                "선택한 PDF 한 건만 읽기"
            ):
                try:
                    with st.spinner(
                        "PDF 한 건 처리 중 · 최대 40초…"
                    ):
                        chars = upgrade_naver_pdf(
                            store,
                            chosen,
                        )

                    st.success(
                        f"PDF 전문 {chars:,}자 저장. "
                        "원문 정리를 다시 실행하세요."
                    )

                except Exception as error:
                    st.error(str(error))

        else:
            st.caption(
                "빠른 수집을 먼저 실행하세요."
            )

    with st.expander(
        "수집 기록"
    ):
        for run in store.runs():
            st.caption(
                run["source"]
                + " · "
                + run["status"]
                + " · "
                + run["finished_at"][:16]
            )

            st.json(
                json.loads(
                    run["detail"]
                )
            )

    with st.expander(
        "실제 원문 직접 등록 · 기사 또는 PDF"
    ):
        st.caption(
            "403으로 가져오지 못한 기사도 "
            "정상적으로 읽을 수 있는 실제 본문을 "
            "직접 등록할 수 있습니다."
        )

        with st.form(
            "manual"
        ):
            title = st.text_input(
                "제목"
            )

            publisher = st.text_input(
                "출처"
            )

            url = st.text_input(
                "원문 HTTPS 링크"
            )

            date = st.date_input(
                "발행일",
                value=datetime.now(KST).date(),
                max_value=datetime.now(KST).date(),
            )

            market = st.selectbox(
                "시장",
                ["국내", "해외", "비트코인"],
            )

            body = st.text_area(
                "실제 기사 본문"
            )

            uploaded = st.file_uploader(
                "또는 PDF 업로드",
                type=["pdf"],
            )

            if st.form_submit_button(
                "본문 저장"
            ):
                try:
                    parsed = urlparse(url)

                    if (
                        not title.strip()
                        or not publisher.strip()
                        or parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError(
                            "제목·출처·HTTPS 링크를 확인하세요."
                        )

                    text = (
                        pdf_text(
                            uploaded.getvalue()
                        )
                        if uploaded
                        else body
                    )

                    store.save_document({
                        "title": title.strip(),
                        "publisher": publisher.strip(),
                        "url": url,
                        "market": market,
                        "body": text,
                        "kind": (
                            "직접 등록 PDF"
                            if uploaded
                            else "직접 등록 기사"
                        ),
                        "published_at": datetime.combine(
                            date,
                            datetime.min.time(),
                            tzinfo=KST,
                        ).isoformat(),
                    })

                    st.success(
                        "본문을 저장했습니다."
                    )

                except Exception as error:
                    st.error(str(error))

    st.caption(
        "OpenAI 키·크레딧 불필요. "
        "생성형 AI 분석이 아닌 원문 발췌 방식입니다. "
        "인베스팅 RSS는 최신 목록 누적 방식이며 "
        "일주일 전체를 소급 확보하지는 못합니다."
    )

st.caption(
    "강삐 · 실제 원문 발췌 · 투자 추천이 아닙니다. "
    "카카오톡 자동 발송은 미연결입니다."
)
