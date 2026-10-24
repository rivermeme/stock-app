from io import BytesIO
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


HEADERS = {
    "User-Agent": (
        "MorningBrief/0.1 "
        "(personal research; source connectivity check)"
    )
}


def fetch(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    return response


def naver_reports():
    url = (
        "https://finance.naver.com/"
        "research/company_list.naver"
    )
    response = fetch(url)
    response.encoding = "euc-kr"

    soup = BeautifulSoup(response.text, "html.parser")
    reports = []

    for row in soup.select("table.type_1 tr"):
        title = row.select_one('a[href*="company_read"]')
        pdf = row.select_one('a[href*=".pdf"]')

        if title:
            reports.append({
                "title": title.get_text(" ", strip=True),
                "url": urljoin(url, title["href"]),
                "pdf": (
                    urljoin(url, pdf["href"])
                    if pdf else None
                ),
            })

    if not reports:
        raise ValueError(
            "보고서 목록을 찾지 못했습니다. "
            "페이지 구조 변경 또는 접근 제한을 확인하세요."
        )

    return reports


def pdf_text(url):
    host = urlparse(url).hostname or ""

    if (
        host != "pstatic.net"
        and not host.endswith(".pstatic.net")
    ):
        raise ValueError("지원하지 않는 PDF 호스트입니다.")

    response = fetch(url)

    if len(response.content) > 20_000_000:
        raise ValueError("PDF 용량 제한을 초과했습니다.")

    reader = PdfReader(BytesIO(response.content))
    text = "\n".join(
        page.extract_text() or ""
        for page in reader.pages
    )

    if len(text.strip()) < 100:
        raise ValueError(
            "본문 미확보: OCR 또는 별도 확인이 필요합니다."
        )

    return text


def investing_feed():
    response = fetch(
        "https://kr.investing.com/rss/news.rss"
    )
    root = ElementTree.fromstring(response.content)

    items = [
        {
            "title": item.findtext("title"),
            "url": item.findtext("link"),
        }
        for item in root.findall(".//item")
        if item.findtext("title") and item.findtext("link")
    ]

    if not items:
        raise ValueError("RSS 항목을 찾지 못했습니다.")

    return items
