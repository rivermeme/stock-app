import hashlib
import html
import json
import math
import os
import re
import sqlite3
import time
import uuid

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from threading import Event, local
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader


# ==================================================
# 기본 설정
# ==================================================

st.set_page_config(
    page_title="강삐 · 강민 주식비서",
    page_icon="☀️",
    layout="centered",
)

KST = ZoneInfo("Asia/Seoul")
ENGINE_NAME = "무료 로컬 발췌"

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


# ==================================================
# 공통 함수
# ==================================================

def normalized(text):
    return " ".join(text.split())


def digest(text):
    return hashlib.sha256(
        normalized(text).encode()
    ).hexdigest()


def morning_cutoff():
    now = datetime.now(KST)
    cutoff = now.replace(
        hour=6,
        minute=30,
        second=0,
        microsecond=0,
    )

    if now < cutoff:
        cutoff -= timedelta(days=1)

    return cutoff


def period_text():
    today = datetime.now(KST).date()
    start = today - timedelta(days=6)
    return f"{start:%m/%d} ~ {today:%m/%d} · KST"


# ==================================================
# SQLite 저장소
# ==================================================

class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    @contextmanager
    def connection(self):
        con = sqlite3.connect(
            self.path,
            timeout=30,
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


class ResearchStore(Database):
    def __init__(self, path):
        super().__init__(path)

        with self.connection() as con:
            con.executescript("""
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

    def save_document(self, doc):
        date = datetime.fromisoformat(
            doc["published_at"]
        )

        if date.tzinfo is None:
            raise ValueError(
                "발행 시각에 시간대가 필요합니다."
            )

        if date > datetime.now(KST) + timedelta(minutes=5):
            raise ValueError(
                "미래 발행 시각의 자료는 저장하지 않습니다."
            )

        if len(normalized(doc["body"])) < 200:
            raise ValueError(
                "본문이 부족하여 저장하지 않았습니다. "
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

        with self.connection() as con:
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

        return doc_id

    def documents(self, market):
        now = datetime.now(KST)

        lower = (
            now - timedelta(days=6)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        with self.connection() as con:
            rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT *
                    FROM documents
                    WHERE market=?
                    ORDER BY published_at DESC
                    """,
                    (market,),
                )
            ]

        return [
            row
            for row in rows
            if lower
            <= datetime.fromisoformat(
                row["published_at"]
            )
            <= now
        ]

    def existing_urls(self):
        with self.connection() as con:
            return {
                row["url"]
                for row in con.execute(
                    "SELECT url FROM documents"
                )
            }

    def log_run(
        self,
        source,
        started_at,
        status,
        detail,
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
        self,
        market,
        fingerprint,
        payload,
        document_ids,
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
                    ENGINE_NAME,
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
# 공개 원문 요청
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
                    "이 서버의 자동 요청이 거부됐습니다. "
                    "해당 출처 수집을 중단합니다."
                )

            response.raise_for_status()

            chunks = []
            total = 0

            for chunk in response.iter_content(
                64 * 1024
            ):
                total += len(chunk)

                if total > max_bytes:
                    raise ValueError(
                        "원문 다운로드 용량 제한 "
                        "20MB를 초과했습니다."
                    )

                chunks.append(chunk)

            response._content = b"".join(chunks)
            response._content_consumed = True

            return response

    raise ValueError(
        "리다이렉트 횟수를 초과했습니다."
    )


def parse_date(value):
    value = value.strip()

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        try:
            result = parsedate_to_datetime(
                value
            )
        except (ValueError, TypeError):
            formats = [
                "%Y.%m.%d",
                "%y.%m.%d",
                "%Y-%m-%d",
                "%Y-%m-%d %H:%M:%S",
            ]

            for fmt in formats:
                try:
                    result = datetime.strptime(
                        value,
                        fmt,
                    )
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(
                    "발행일 형식을 확인할 수 없습니다."
                )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=KST
        )

    return result.astimezone(KST)


def pdf_text(url_or_bytes):
    if isinstance(url_or_bytes, bytes):
        content = url_or_bytes
    else:
        content = fetch(
            url_or_bytes
        ).content

    if len(content) > 20_000_000:
        raise ValueError(
            "PDF 최대 크기는 20MB입니다."
        )

    if not content.lstrip().startswith(
        b"%PDF"
    ):
        raise ValueError(
            "PDF 응답이 아닙니다. "
            "원문 주소를 확인하세요."
        )

    reader = PdfReader(
        BytesIO(content)
    )

    if len(reader.pages) > 200:
        raise ValueError(
            "이 실행 환경에서는 200쪽 이하 "
            "PDF를 처리합니다."
        )

    text = "\n".join(
        page.extract_text() or ""
        for page in reader.pages
    )

    if len(text.strip()) < 200:
        raise ValueError(
            "본문 추출 실패: 이미지 PDF는 "
            "OCR 또는 별도 확인이 필요합니다."
        )

    return text


# ==================================================
# 네이버: 최근 7일 날짜 경계까지 수집
# ==================================================

def naver_reports(
    category,
    progress=None,
):
    if category not in CATEGORIES:
        raise ValueError(
            "지원하지 않는 분류입니다."
        )

    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=6)

    result = []
    seen = set()
    index = 0

    while True:
        url = (
            "https://stock.naver.com/api/"
            "stockSecurity/researches/v2/"
            f"{category}?index={index}&size=15"
        )

        data = fetch(url).json()

        if (
            not isinstance(data, dict)
            or not isinstance(
                data.get("items"),
                list,
            )
        ):
            raise CollectionIncomplete(
                "네이버 목록 형식이 변경됐습니다. "
                "일주일 수집을 완료하지 못했습니다."
            )

        items = data["items"]

        if not items:
            if data.get("hasNext"):
                raise CollectionIncomplete(
                    "빈 목록인데 다음 페이지가 "
                    "있다고 응답했습니다."
                )
            break

        dates = []
        new_ids = 0

        for item in items:
            nid = str(
                item.get("nid", "")
            )

            if (
                not nid.isdigit()
                or not item.get("writeDate")
                or not item.get("title")
            ):
                raise CollectionIncomplete(
                    "필수 보고서 정보가 누락되어 "
                    "기간 전체 목록을 확인하지 못했습니다."
                )

            date = parse_date(
                item["writeDate"]
            )

            dates.append(date.date())

            if nid in seen:
                continue

            seen.add(nid)
            new_ids += 1

            if (
                date.date() < cutoff
                or date.date() > today
            ):
                continue

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
                    "analystName",
                    "",
                ),
            })

        if progress:
            progress(
                f"{CATEGORIES[category]}: "
                f"기간 내 목록 {len(result)}건 확인"
            )

        if (
            any(date < cutoff for date in dates)
            or not data.get("hasNext")
        ):
            break

        if not new_ids:
            raise CollectionIncomplete(
                "같은 목록이 반복됩니다. "
                "날짜 경계에 도달하지 못했습니다."
            )

        index += 1
        time.sleep(0.15)

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
            "보고서 PDF 주소를 확보하지 못했습니다."
        )

    date = parse_date(
        detail.get("writeDate")
        or report["published_at"]
    )

    return {
        "title": (
            detail.get("title")
            or report["title"]
        ),
        "url": report["url"],
        "publisher": (
            detail.get("brokerName")
            or report["publisher"]
        ),
        "published_at": date.isoformat(),
        "body": pdf_text(
            detail["attachUrl"]
        ),
        "market": "국내",
        "kind": (
            "증권사 "
            + CATEGORIES[report["category"]]
            + " PDF"
        ),
        "author": (
            detail.get("analystName")
            or report.get("author", "")
        ),
    }


# ==================================================
# 인베스팅 RSS / 실제 기사 본문
# ==================================================

def investing_feed():
    response = fetch(
        "https://kr.investing.com/rss/news.rss"
    )

    root = ElementTree.fromstring(
        response.content
    )

    result = []

    for item in root.findall(".//item"):
        title = item.findtext("title")
        url = item.findtext("link")

        if title and url:
            result.append({
                "title": title,
                "url": url,
            })

    if not result:
        raise ValueError(
            "RSS 항목을 찾지 못했습니다."
        )

    return result


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

    accessible = str(
        meta.get(
            "isAccessibleForFree",
            "true",
        )
    ).lower()

    if accessible == "false":
        raise ValueError(
            "구독 또는 접근 제한된 본문은 "
            "수집하지 않습니다."
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
            "시간대가 포함된 기사 발행일을 "
            "확보하지 못했습니다."
        )

    content = soup.select_one(
        '[data-test="article-content"], '
        '[class*="article_WYSIWYG"]'
    )

    if not content:
        raise ValueError(
            "기사 본문 미확보. "
            "제목만으로 정리하지 않습니다."
        )

    for tag in content.select(
        "script, style, aside, button"
    ):
        tag.decompose()

    body = content.get_text(
        "\n",
        strip=True,
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
        "published_at": parse_date(
            date
        ).isoformat(),
        "publisher": "Investing.com",
        "kind": (
            "기고자 분석"
            if "/analysis/" in item["url"]
            else "언론 보도"
        ),
        "author": str(author),
    }


# ==================================================
# 수집 실행
# ==================================================

def collect_sources(
    store,
    source,
    progress=None,
):
    started = datetime.now(KST).isoformat()
    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=6)

    saved = []
    reused = 0
    skipped = 0
    errors = []
    candidates = []
    completed_categories = []
    blocked = False

    existing = store.existing_urls()

    try:
        if source == "investing":
            candidates = investing_feed()

        elif source == "naver":
            for category in CATEGORIES:
                try:
                    candidates.extend(
                        naver_reports(
                            category,
                            progress=progress,
                        )
                    )
                    completed_categories.append(
                        category
                    )
                except Exception as error:
                    errors.append(
                        f"{CATEGORIES[category]}: "
                        f"{type(error).__name__}: "
                        f"{str(error)[:400]}"
                    )

        else:
            raise ValueError(
                "지원하지 않는 출처입니다."
            )

        candidates = list({
            item["url"]: item
            for item in candidates
        }.values())

        pending = []

        for item in candidates:
            if item["url"] in existing:
                reused += 1
            else:
                pending.append(item)

        stop = Event()

        def download(item):
            if stop.is_set():
                return None

            try:
                if source == "investing":
                    return investing_article(item)

                return naver_document(item)

            except SourceAccessError:
                stop.set()
                raise

            finally:
                time.sleep(0.2)

        with ThreadPoolExecutor(
            max_workers=4
        ) as executor:
            jobs = {
                executor.submit(
                    download,
                    item,
                ): item
                for item in pending
            }

            for index, future in enumerate(
                as_completed(jobs)
            ):
                item = jobs[future]

                if progress:
                    progress(
                        f"본문 확인 "
                        f"{index + 1}/{len(pending)} · "
                        f"저장 {len(saved)} · "
                        f"기존 재사용 {reused}"
                    )

                try:
                    doc = future.result()

                    if doc is None:
                        continue

                    date = parse_date(
                        doc["published_at"]
                    ).date()

                    if date < cutoff or date > today:
                        skipped += 1
                        continue

                    saved.append(
                        store.save_document(doc)
                    )

                except SourceAccessError as error:
                    if not blocked:
                        errors.append(
                            f'{item["title"][:70]}: '
                            f"{error}"
                        )
                    blocked = True

                except Exception as error:
                    errors.append(
                        f'{item["title"][:70]}: '
                        f"{type(error).__name__}: "
                        f"{str(error)[:400]}"
                    )

    except SourceAccessError as error:
        errors.append(str(error))
        blocked = True

    except Exception as error:
        errors.append(
            f"{type(error).__name__}: "
            f"{str(error)[:400]}"
        )

    detail = {
        "saved": len(set(saved)),
        "reused": reused,
        "candidates": len(candidates),
        "outside_window": skipped,
        "errors": errors,
        "blocked": blocked,
        "period_start": cutoff.isoformat(),
        "period_end": today.isoformat(),
        "completed_categories": completed_categories,
        "coverage": (
            "最近7日 누적분과 현재 RSS 목록입니다. "
            "RSS 이전 자료의 소급 확보는 보장되지 않습니다."
            if source == "investing"
            else
            "오늘 포함 최근 7일 · 날짜 경계까지 탐색 · "
            "기존 본문 재사용"
        ),
    }

    if errors and (saved or reused):
        status = "partial"
    elif errors:
        status = "failed"
    elif saved or reused:
        status = "success"
    else:
        status = "empty"

    store.log_run(
        source,
        started,
        status,
        detail,
    )

    return detail


# ==================================================
# 무료 원문 분류 / 발췌
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
    data = {
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
    }

    return digest(
        json.dumps(
            data,
            sort_keys=True,
            ensure_ascii=False,
        )
    )


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
            term
            for term in hits
            if term in title
        ]

        lead_hits = [
            term
            for term in hits
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

    excluded = [
        "무단 배포",
        "투자판단의 최종",
        "Compliance Notice",
        "투자의견 비율",
    ]

    for number, sentence in enumerate(sentences):
        if len(sentence) < 35:
            continue

        if any(
            word in sentence
            for word in excluded
        ):
            continue

        windows = [
            sentence[start:start + 320]
            for start in range(
                0,
                len(sentence),
                220,
            )
        ]

        for piece in windows:
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

            score += 2 * bool(
                re.search(
                    r"\d[\d,.]*\s*"
                    r"(%|억원|조원|원|달러)",
                    piece,
                )
            )

            score += min(
                hangul / 60,
                3,
            )

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
    progress=None,
):
    documents = select_documents(documents)

    if not documents:
        raise ValueError(
            "최근 7일 내 확보한 실제 본문이 없습니다."
        )

    grouped = defaultdict(list)

    for index, doc in enumerate(documents):
        grouped[
            choose_theme(doc)
        ].append(doc)

        if progress and (
            index % 20 == 0
            or index == len(documents) - 1
        ):
            progress(
                f"원문 분류 "
                f"{index + 1}/{len(documents)}건 · "
                "유료 API 사용 없음"
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

        facts = []

        for doc in representatives:
            facts.append({
                "text": doc["title"],
                "source_id": doc["id"],
                "quote": excerpt(
                    doc,
                    theme,
                ),
            })

        related = []
        used_names = set()

        for asset in assets:
            name = normalized(asset["name"])

            if (
                len(name) < 2
                or name in used_names
            ):
                continue

            for doc in group:
                body = normalized(doc["body"])

                location = (
                    body.casefold().find(
                        name.casefold()
                    )
                )

                if location < 0:
                    continue

                start = max(
                    0,
                    location - 50,
                )

                end = min(
                    len(body),
                    location + len(name) + 120,
                )

                related.append({
                    "name": asset["name"],
                    "reason": (
                        "원문에서 등록 자산명이 확인됩니다. "
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

        issues.append({
            "title": (
                theme + " 리서치 모아보기"
            ),
            "category": theme,
            "summary": facts[0]["quote"],
            "impact": (
                f"관련 단어 기준 본문 {len(group)}건 · "
                f"발행사/매체 {publishers}곳. "
                "동일 의견의 합의가 아니라 수집 범위입니다."
            ),
            "check_note": (
                "아래 원문 발췌와 서로 다른 출처를 "
                "비교하세요. 이 화면은 새로운 AI 해석이나 "
                "투자 의견을 생성하지 않습니다."
            ),
            "facts": facts,
            "risks": [
                "키워드 분류와 원문 발췌 결과입니다. "
                "분류가 부정확하거나 문맥이 "
                "생략될 수 있습니다."
            ],
            "checks": [
                "원문 발행일과 수치의 기준 시점을 확인하세요.",
                "작성자의 전망과 실제 발표 실적을 구분하세요.",
            ],
            "related_assets": related,
            "names": [
                asset["name"]
                for asset in related
            ],
            "source_ids": [
                doc["id"]
                for doc in representatives
            ],
            "document_count": len(group),
        })

    return {
        "issues": issues,
        "method": (
            "무료 로컬 원문 발췌 · 키워드 분류"
        ),
        "coverage": {
            "documents": len(documents),
            "full_text_processed": True,
            "api_calls": 0,
        },
    }


# ==================================================
# 국내 / 해외 카드뉴스 이미지
# ==================================================

def render_card(
    market,
    issues,
    generated_at,
):
    if (
        market not in ("국내", "해외")
        or not 1 <= len(issues) <= 3
    ):
        raise ValueError(
            "카드로 만들 실제 원문 정리 결과가 필요합니다."
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
        "RGB",
        (1080, 1350),
        "white",
    )

    draw = ImageDraw.Draw(image)
    fonts = {}

    def font(size):
        if size not in fonts:
            fonts[size] = ImageFont.truetype(
                font_path,
                size,
            )
        return fonts[size]

    def write(
        text,
        x,
        y,
        size=30,
        color="#182127",
    ):
        draw.text(
            (x, y),
            text,
            font=font(size),
            fill=color,
        )

    def wrapped(
        text,
        x,
        y,
        size,
        width,
        max_lines,
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

        names = item.get("names", [])

        label = (
            "자산명 언급  " + " · ".join(names)
            if names
            else "상세 원문과 출처는 앱에서 확인"
        )

        wrapped(
            label,
            55, y + 248, 23, 960, 1,
        )

    write(
        "원문 핵심 발췌 · 키워드 분류 · "
        "상세 출처는 앱에서 확인",
        50, 1250, 23, "#68717a",
    )

    write(
        "매수·매도 추천이 아닙니다.",
        50, 1290, 22, "#68717a",
    )

    result = BytesIO()
    image.save(
        result,
        format="PNG",
    )

    return result.getvalue()


# ==================================================
# 모바일 화면 스타일
# ==================================================

st.markdown(
    """
    <style>
    .block-container {
        max-width: 540px;
        padding: 1.5rem 1rem 7rem;
    }

    [data-testid="stToolbar"] {
        display: none;
    }

    h1 {
        font-size: 1.9rem !important;
        letter-spacing: -1px;
    }

    h2 {
        font-size: 1.45rem !important;
    }

    h3 {
        font-size: 1.08rem !important;
    }

    p {
        line-height: 1.6;
    }

    .brand {
        font-size: 13px;
        color: #777f92;
    }

    .badge {
        display: inline-block;
        border-radius: 8px;
        padding: 4px 10px;
        background: #eaf0ff;
        color: #425bd0;
        font-size: 12px;
        font-weight: 600;
    }

    .purple {
        background: #f0eaff;
        color: #8d51c9;
    }

    .teal {
        background: #e3f2ef;
        color: #148579;
    }

    .tag {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 8px;
        background: #eef0ff;
        color: #303c77;
        font-size: 13px;
        margin: 5px;
    }

    button {
        min-height: 44px !important;
    }

    button[kind="primary"],
    button[kind="primaryFormSubmit"] {
        background: #505bd1;
        border-color: #505bd1;
    }

    [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
    }

    [data-testid="stColumn"] {
        min-width: 0 !important;
        width: auto !important;
    }

    div[data-testid="stMetricValue"] {
        font-size: 1.15rem;
    }

    .st-key-navigation {
        position: static;
        width: 100%;
        padding: 4px;
        background: #f6f7fb;
        border: 1px solid #e3e6ef;
        border-radius: 12px;
    }

    .st-key-navigation
    [data-testid="stHorizontalBlock"] {
        gap: 4px !important;
    }

    .st-key-navigation button {
        background: transparent !important;
        border: 0 !important;
        border-radius: 8px;
        padding: 4px !important;
        color: #263249 !important;
    }

    .st-key-navigation button p {
        color: #263249 !important;
        font-weight: 600;
        white-space: nowrap;
    }

    .st-key-navigation button[kind="primary"] {
        background: #505bd1 !important;
        color: #ffffff !important;
    }

    .st-key-navigation button[kind="primary"] p {
        color: #ffffff !important;
    }

    .st-key-navigation button:focus-visible {
        outline: 2px solid #505bd1 !important;
        outline-offset: 2px;
    }

    [data-testid="stRadio"],
    [data-testid="stRadio"] > div {
        width: 100% !important;
    }

    [data-testid="stRadio"]
    > div[role="radiogroup"] {
        background: #f6f7fb;
        border: 1px solid #e3e6ef;
        border-radius: 10px;
        padding: 3px;
        display: flex;
        gap: 0 !important;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"] {
        flex: 1;
        justify-content: center;
        margin: 0 !important;
        padding: 8px 4px;
        border-radius: 7px;
        color: #263249 !important;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"] p {
        color: #263249 !important;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"]:has(input:checked) {
        background: #505bd1;
        color: #ffffff !important;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"]:has(input:checked) p {
        color: #ffffff !important;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"] > div:first-child {
        display: none;
    }

    [data-testid="stRadio"]
    label[data-baseweb="radio"]:focus-within {
        outline: 2px solid #505bd1;
        outline-offset: 2px;
    }

    .st-key-check_note {
        background: #edf0ff;
        border-radius: 12px;
        padding: 15px;
    }

    .st-key-check_note,
    .st-key-check_note p {
        color: #263249 !important;
    }

    @media (max-width: 600px) {
        .block-container {
            padding-top: 1rem;
        }

        h1 {
            font-size: 1.7rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==================================================
# 앱 상태
# ==================================================

store = ResearchStore(
    os.getenv(
        "RESEARCH_DB",
        "data/research.db",
    )
)

portfolio = Portfolio(
    os.getenv(
        "PORTFOLIO_DB",
        "data/portfolio.db",
    )
)

st.session_state.setdefault(
    "page",
    "오늘",
)


def go(page, index=None):
    st.session_state.page = page

    if index is not None:
        st.session_state.selected = index


def edit(asset=None):
    st.session_state.edit_asset = asset or {}
    st.session_state.show_form = True
    st.session_state.form_revision = (
        st.session_state.get(
            "form_revision",
            0,
        ) + 1
    )


def badge(text, color=""):
    st.markdown(
        f'<span class="badge {color}">'
        f"{html.escape(text)}</span>",
        unsafe_allow_html=True,
    )


def context(market):
    documents = store.documents(market)
    selected = select_documents(documents)
    assets = portfolio.all()

    fingerprint = analysis_fingerprint(
        selected,
        assets,
    )

    saved = store.analysis(
        market,
        fingerprint,
    )

    return (
        documents,
        selected,
        assets,
        fingerprint,
        saved,
    )


def source_panel(documents):
    if not documents:
        st.info(
            "최근 7일 내 본문을 확보한 자료가 없습니다. "
            "설정에서 수집하거나 실제 원문을 등록하세요."
        )
        return

    st.caption(
        f"최근 7일 저장 본문 {len(documents)}건. "
        "해당 기간 전체 시장 자료를 "
        "모두 확보했다는 뜻은 아닙니다."
    )

    for doc in documents:
        with st.expander(doc["title"]):
            st.caption(
                f'{doc["publisher"]} · '
                f'{doc["kind"]} · '
                f'{doc["published_at"][:16]} KST'
            )

            if doc.get("author"):
                st.caption(
                    "작성자: " + doc["author"]
                )

            st.link_button(
                "원문 열기",
                doc["url"],
            )

            st.text(
                doc["body"][:4000]
            )

            st.caption(
                "실제 본문 미리보기입니다."
            )


def generate(market):
    (
        documents,
        selected,
        assets,
        fingerprint,
        saved,
    ) = context(market)

    st.caption(
        f"최근 7일 중복 제외 본문 "
        f"{len(selected)}건 전체 · "
        "원문 발췌와 키워드 분류 · "
        "유료 모델 API 호출 0회"
    )

    if st.button(
        "무료로 원문 정리",
        key="analyze_" + market,
        disabled=not selected,
        type="primary",
        use_container_width=True,
    ):
        try:
            with st.spinner(
                "원문을 분류하고 "
                "핵심 문장을 추출하고 있습니다…"
            ):
                progress = st.empty()

                payload = analyze_free(
                    selected,
                    assets,
                    market,
                    progress=progress.info,
                )

                store.save_analysis(
                    market,
                    fingerprint,
                    payload,
                    [
                        doc["id"]
                        for doc in selected
                    ],
                )

            st.rerun()

        except Exception as error:
            st.error(
                f"결과를 저장하지 않았습니다: {error}"
            )

    if saved:
        st.caption(
            f'정리 생성 '
            f'{saved["generated_at"][:16]} KST · '
            f'{saved["model"]} · '
            f'본문 {len(saved["document_ids"])}건'
        )


# ==================================================
# 상단 이름 / 날짜 / 메뉴
# ==================================================

st.markdown(
    '<div class="brand">강민 주식비서</div>',
    unsafe_allow_html=True,
)

heading, settings = st.columns([6, 1])

heading.title("강삐")

settings.button(
    "⚙",
    key="settings",
    help="자료 연결 및 설정",
    on_click=go,
    args=("설정",),
)

page = st.session_state.page
now = datetime.now(KST)

weekdays = [
    "월", "화", "수", "목", "금", "토", "일"
]

date_column, status_column = st.columns(
    [3, 1]
)

date_column.caption(
    f"{now.month}월 {now.day}일 "
    f"{weekdays[now.weekday()]}요일 · KST"
)

with status_column:
    badge("무료 원문 정리")

with st.container(key="navigation"):
    for column, label, icon in zip(
        st.columns(4),
        ["오늘", "분석", "인기글", "내 자산"],
        ["⌂", "⌕", "▤", "♙"],
    ):
        column.button(
            f"{icon} {label}",
            key="nav_" + label,
            type=(
                "primary"
                if page == label
                else "secondary"
            ),
            use_container_width=True,
            on_click=go,
            args=(label,),
        )


# ==================================================
# 오늘
# ==================================================

if page == "오늘":
    st.header("오늘 알아둘 이야기")

    market = st.radio(
        "시장",
        ["국내", "해외", "비트코인"],
        horizontal=True,
        width="stretch",
        label_visibility="collapsed",
        key="market",
    )

    st.caption(
        "자료 기간: " + period_text()
    )

    (
        documents,
        selected,
        assets,
        fingerprint,
        saved,
    ) = context(market)

    if saved:
        for index, item in enumerate(
            saved["payload"]["issues"]
        ):
            with st.container(border=True):
                badge(
                    item["category"],
                    ["", "purple", "teal"][index % 3],
                )

                st.subheader(
                    item["title"]
                )

                st.write(
                    item["summary"]
                )

                st.caption(
                    "수집 범위 · 원문 비교"
                )

                st.write(
                    item["impact"]
                )

                if item["names"]:
                    st.markdown(
                        " ".join(
                            '<span class="tag">'
                            + html.escape(name)
                            + "</span>"
                            for name in item["names"]
                        ),
                        unsafe_allow_html=True,
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
            "현재 자료와 보유내역에 맞는 "
            "정리 결과가 없습니다. "
            "설정에서 자료를 수집한 다음 "
            "무료로 원문 정리를 눌러주세요."
        )

    generate(market)

    with st.expander(
        "확보한 실제 원문",
        expanded=not bool(saved),
    ):
        source_panel(documents)

    with st.container(border=True):
        st.write(
            "**내 자산 연결**"
        )

        st.caption(
            f"등록 자산 {len(assets)}개. "
            "원문에서 자산명이 언급되는지 확인합니다. "
            "투자 영향을 자동 판단하는 기능은 아닙니다."
        )

        st.button(
            "자산 관리",
            on_click=go,
            args=("내 자산",),
        )

    with st.expander(
        "카카오톡용 카드뉴스 · 국내 / 해외"
    ):
        st.caption(
            "국내 1장·해외 1장. "
            "실제 정리 결과가 있는 시장만 생성합니다. "
            "카카오톡 자동 발송은 아직 미연결입니다."
        )

        for target, column in zip(
            ["국내", "해외"],
            st.columns(2),
        ):
            target_analysis = context(
                target
            )[4]

            if not target_analysis:
                column.info(
                    f"{target} 원문 정리 대기"
                )
                continue

            try:
                data = render_card(
                    target,
                    target_analysis[
                        "payload"
                    ]["issues"],
                    target_analysis[
                        "generated_at"
                    ],
                )

                column.download_button(
                    f"{target} 카드 PNG",
                    data=data,
                    file_name=(
                        f"gangppi-{target}.png"
                    ),
                    mime="image/png",
                    key="card_" + target,
                    use_container_width=True,
                )

            except (ValueError, OSError) as error:
                column.warning(
                    str(error)
                )


# ==================================================
# 상세
# ==================================================

elif page == "분석":
    st.button(
        "‹ 오늘로",
        on_click=go,
        args=("오늘",),
    )

    market = st.session_state.get(
        "market",
        "국내",
    )

    (
        documents,
        selected,
        assets,
        fingerprint,
        saved,
    ) = context(market)

    if not saved:
        st.header(
            "원문 정리 대기"
        )

        generate(market)
        source_panel(documents)

    else:
        issues = saved[
            "payload"
        ]["issues"]

        index = min(
            st.session_state.get(
                "selected",
                0,
            ),
            len(issues) - 1,
        )

        selected_index = st.selectbox(
            "이슈 선택",
            range(len(issues)),
            index=index,
            format_func=lambda value: (
                issues[value]["title"]
            ),
        )

        item = issues[
            selected_index
        ]

        mapping = {
            doc["id"]: doc
            for doc in documents
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
            label_visibility="collapsed",
        )

        with st.container(border=True):
            st.subheader(
                "핵심 원문 발췌"
            )

            st.write(
                item["summary"]
            )

            st.caption(
                "수집 범위 · 원문 비교"
            )

            st.write(
                item["impact"]
            )

        if mode == "상세":
            with st.container(border=True):
                st.subheader(
                    "대표 원문과 출처"
                )

                for fact in item["facts"]:
                    doc = mapping[
                        fact["source_id"]
                    ]

                    st.write(
                        fact["text"]
                    )

                    st.caption(
                        f'{doc["publisher"]} · '
                        f'{doc["kind"]}'
                    )

                    st.text(
                        "원문 발췌: "
                        + fact["quote"]
                    )

                    st.link_button(
                        "출처 확인",
                        doc["url"],
                    )

            with st.expander(
                "등록 자산명 언급",
                expanded=True,
            ):
                if not item["related_assets"]:
                    st.write(
                        "본문에서 등록 자산명을 "
                        "확인하지 못했습니다."
                    )

                for asset in item["related_assets"]:
                    st.write(
                        f'**{asset["name"]}** — '
                        f'{asset["reason"]}'
                    )

                    st.text(
                        "언급된 원문: "
                        + asset["quote"]
                    )

                    st.link_button(
                        "언급 원문",
                        mapping[
                            asset["source_id"]
                        ]["url"],
                    )

            with st.expander(
                "읽을 때 참고할 점"
            ):
                for risk in item["risks"]:
                    st.write(
                        "• " + risk
                    )

            with st.expander(
                "다음에 확인할 사항"
            ):
                for check in item["checks"]:
                    st.write(
                        "• " + check
                    )

        with st.container(
            key="check_note"
        ):
            st.markdown(
                "**확인할 점**"
            )

            st.write(
                item["check_note"]
            )

            st.caption(
                "유료 모델 API를 사용하지 않는 "
                "원문 발췌 방식입니다."
            )

        with st.expander(
            "📖 수주잔고"
        ):
            st.write(
                "계약을 맺었지만 아직 납품하지 않은 "
                "물량입니다. 향후 매출의 단서지만 "
                "취소·지연될 수 있습니다."
            )

        with st.expander(
            "정리 범위 · 전체 출처"
        ):
            st.caption(
                f'생성: '
                f'{saved["generated_at"][:16]} KST · '
                f'{saved["model"]}'
            )

            st.write(
                "최근 7일 저장 본문 "
                f'{len(saved["document_ids"])}건을 '
                "키워드로 분류하고 대표 문장을 발췌했습니다. "
                "수집 실패 자료는 포함되지 않습니다."
            )

            for source_id in saved["document_ids"]:
                doc = mapping.get(
                    source_id
                )

                if doc:
                    st.link_button(
                        doc["title"],
                        doc["url"],
                    )


# ==================================================
# 내 자산
# ==================================================

elif page == "내 자산":
    st.header(
        "내 자산"
    )

    st.write(
        "보유내역을 직접 관리하세요."
    )

    st.button(
        "＋ 자산 추가",
        key="add_asset",
        type="primary",
        use_container_width=True,
        on_click=edit,
    )

    assets = portfolio.all()

    if not assets:
        st.info(
            "아직 보유자산이 없습니다. "
            "자산 추가로 시작하세요."
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

            unit = (
                "개"
                if asset["kind"] == "비트코인"
                else "주"
            )

            a.metric(
                "보유 수량",
                f'{asset["quantity"]:g}{unit}',
            )

            b.metric(
                "평균 매수가",
                f'{asset["price"]:,.2f} '
                + asset["currency"],
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
                    "이 보유내역을 삭제할까요?"
                )

                if st.button(
                    "삭제 확인",
                    key="confirm_" + asset["id"],
                ):
                    portfolio.delete(
                        asset["id"]
                    )

                    st.session_state.pop(
                        "delete_id",
                        None,
                    )

                    if (
                        st.session_state.get(
                            "edit_asset",
                            {},
                        ).get("id")
                        == asset["id"]
                    ):
                        st.session_state.show_form = False

                    st.rerun()

                if st.button(
                    "삭제 취소",
                    key="cancel_" + asset["id"],
                ):
                    st.session_state.pop(
                        "delete_id",
                        None,
                    )

                    st.rerun()

    if st.session_state.get(
        "show_form"
    ):
        current = st.session_state.get(
            "edit_asset",
            {},
        )

        revision = st.session_state.get(
            "form_revision",
            0,
        )

        with st.form(
            "asset_form_" + str(revision),
            border=True,
        ):
            st.subheader(
                "보유내역 수정"
                if current
                else "자산 추가"
            )

            name = st.text_input(
                "종목",
                value=current.get(
                    "name",
                    "",
                ),
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
                    current.get(
                        "kind",
                        "국내주식",
                    )
                ),
            )

            quantity = st.number_input(
                "수량",
                min_value=0.0,
                value=float(
                    current.get(
                        "quantity",
                        1,
                    )
                ),
                format="%.6f",
            )

            price = st.number_input(
                "평균 매수가",
                min_value=0.0,
                value=float(
                    current.get(
                        "price",
                        0,
                    )
                ),
            )

            currencies = [
                "KRW",
                "USD",
            ]

            currency = st.selectbox(
                "통화",
                currencies,
                index=currencies.index(
                    current.get(
                        "currency",
                        "KRW",
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
                        current.get("id"),
                    )

                    st.session_state.show_form = False
                    st.rerun()

                except ValueError as error:
                    st.error(
                        str(error)
                    )

            if b.form_submit_button(
                "취소",
                use_container_width=True,
            ):
                st.session_state.show_form = False
                st.rerun()

    st.caption(
        "이 앱 인스턴스의 공용 저장소입니다. "
        "개인용으로 사용하세요. "
        "Streamlit 재배포 후 보존은 "
        "영구 저장소가 필요합니다."
    )

    st.download_button(
        "보유내역 백업 JSON",
        data=json.dumps(
            portfolio.all(),
            ensure_ascii=False,
            indent=2,
        ),
        file_name="portfolio.json",
        mime="application/json",
    )


# ==================================================
# 인기글
# ==================================================

elif page == "인기글":
    st.header(
        "사람들이 많이 본 이야기"
    )

    cutoff = morning_cutoff()

    st.caption(
        f"{cutoff:%m/%d %H:%M} KST · "
        "직전 24시간"
    )

    st.info(
        "실제 전체 종목 토론방 데이터는 "
        "아직 연결되지 않았습니다. "
        "가상 순위와 조회수는 표시하지 않습니다."
    )


# ==================================================
# 설정 / 수집 / 직접 등록
# ==================================================

else:
    st.header(
        "설정 · 실제 자료 연결"
    )

    st.subheader(
        "원문 수집"
    )

    st.caption(
        "기간: " + period_text()
    )

    st.write(
        "네이버는 최근 7일의 날짜 경계까지 읽고, "
        "이미 저장한 본문은 재사용합니다."
    )

    st.caption(
        "인베스팅은 RSS 수집분을 누적합니다. "
        "첫 실행에 과거 일주일 전체를 "
        "복원할 수 있는 방식은 아닙니다."
    )

    for source, label in [
        ("naver", "네이버 리서치 수집"),
        ("investing", "인베스팅 뉴스 수집"),
    ]:
        if st.button(
            label,
            key="collect_" + source,
            use_container_width=True,
        ):
            with st.spinner(
                "원문을 확인하고 저장하고 있습니다…"
            ):
                progress = st.empty()

                result = collect_sources(
                    store,
                    source,
                    progress=progress.info,
                )

            if result["saved"]:
                st.success(
                    f'새 본문 {result["saved"]}건 저장 · '
                    f'기존 {result["reused"]}건 재사용'
                )

            elif result["reused"]:
                st.success(
                    f'기존 본문 '
                    f'{result["reused"]}건 재사용'
                )

            elif not result["errors"]:
                st.warning(
                    "최근 7일 내 저장 가능한 "
                    "본문이 없습니다."
                )

            if result["blocked"]:
                st.warning(
                    "이 배포 서버에서 출처 접근이 "
                    "거부됐습니다. 기존 자료는 보존됩니다. "
                    "아래 기사 본문 직접 등록을 "
                    "사용할 수 있습니다."
                )

            for error in result["errors"]:
                st.error(
                    error
                )

            st.caption(
                result["coverage"]
            )

    with st.expander(
        "수집 실행 기록"
    ):
        runs = store.runs()

        if not runs:
            st.write(
                "아직 수집 기록이 없습니다."
            )

        for run in runs:
            st.write(
                f'{run["source"]} · '
                f'{run["status"]} · '
                f'{run["finished_at"][:16]} KST'
            )

            st.json(
                json.loads(
                    run["detail"]
                )
            )

    st.subheader(
        "무료 정리 방식"
    )

    st.success(
        "OpenAI API 미사용 · "
        "API 키 불필요 · "
        "모델 호출 비용 없음"
    )

    st.write(
        "최근 7일 원문을 키워드로 묶고 "
        "실제 문장을 발췌합니다. "
        "새로운 AI 의견이나 인과관계 분석은 "
        "생성하지 않습니다."
    )

    with st.expander(
        "보유한 보고서 PDF 직접 등록"
    ):
        st.caption(
            "실제 이용 가능한 원문만 등록하세요. "
            "발행일과 원문 링크를 확인해 입력하세요."
        )

        with st.form(
            "upload_document"
        ):
            uploaded = st.file_uploader(
                "PDF 파일",
                type=["pdf"],
            )

            title = st.text_input(
                "보고서 제목"
            )

            publisher = st.text_input(
                "발행사"
            )

            url = st.text_input(
                "공개 원문 링크 (https://)"
            )

            published = st.date_input(
                "발행일",
                value=datetime.now(KST).date(),
                max_value=datetime.now(KST).date(),
            )

            market = st.selectbox(
                "시장",
                ["국내", "해외", "비트코인"],
            )

            if st.form_submit_button(
                "실제 본문 저장"
            ):
                try:
                    parsed = urlparse(
                        url
                    )

                    if (
                        not uploaded
                        or not title.strip()
                        or not publisher.strip()
                        or parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError(
                            "파일, 제목, 발행사, "
                            "HTTPS 원문 링크를 확인하세요."
                        )

                    body = pdf_text(
                        uploaded.getvalue()
                    )

                    date = datetime.combine(
                        published,
                        datetime.min.time(),
                        tzinfo=KST,
                    ).isoformat()

                    store.save_document({
                        "title": title.strip(),
                        "publisher": publisher.strip(),
                        "url": url,
                        "body": body,
                        "market": market,
                        "kind": "사용자 등록 보고서",
                        "published_at": date,
                    })

                    st.success(
                        "실제 본문을 저장했습니다. "
                        "오늘 화면에서 원문 정리를 눌러주세요."
                    )

                except Exception as error:
                    st.error(
                        f"저장하지 않았습니다: {error}"
                    )

    with st.expander(
        "접근이 차단된 기사 본문 직접 등록"
    ):
        st.caption(
            "브라우저에서 정상적으로 읽을 수 있고 "
            "이용 가능한 실제 본문을 붙여넣으세요."
        )

        with st.form(
            "paste_article"
        ):
            article_title = st.text_input(
                "기사 제목"
            )

            article_url = st.text_input(
                "원문 HTTPS 링크"
            )

            article_source = st.text_input(
                "출처 이름",
                value="Investing.com",
            )

            article_day = st.date_input(
                "기사 발행일",
                value=datetime.now(KST).date(),
                max_value=datetime.now(KST).date(),
            )

            article_market = st.selectbox(
                "정리 시장",
                ["해외", "국내", "비트코인"],
            )

            article_body = st.text_area(
                "실제 원문 본문",
                height=220,
            )

            if st.form_submit_button(
                "기사 본문 저장"
            ):
                try:
                    parsed = urlparse(
                        article_url
                    )

                    if (
                        not article_title.strip()
                        or not article_source.strip()
                        or parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError(
                            "제목, 출처, HTTPS 원문 링크를 "
                            "확인하세요."
                        )

                    date = datetime.combine(
                        article_day,
                        datetime.min.time(),
                        tzinfo=KST,
                    ).isoformat()

                    store.save_document({
                        "title": article_title.strip(),
                        "url": article_url,
                        "publisher": article_source.strip(),
                        "market": article_market,
                        "kind": "사용자 직접 등록 기사",
                        "body": article_body,
                        "published_at": date,
                    })

                    st.success(
                        "실제 기사 본문을 저장했습니다."
                    )

                except Exception as error:
                    st.error(
                        str(error)
                    )

    st.info(
        "전체 종목 토론방 수집과 "
        "오전 7시 카카오톡 자동 발송은 "
        "아직 연결되지 않았습니다."
    )


st.caption(
    "강삐 · 실제 원문 발췌 · "
    "투자 추천이 아닙니다."
)
