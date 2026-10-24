from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def render_card(market, issues, font_path=None):
    font_path = font_path or (
        "/usr/share/fonts/opentype/noto/"
        "NotoSansCJK-Bold.ttc"
    )

    if not Path(font_path).exists():
        raise ValueError(
            "한글 폰트가 필요합니다. CARD_FONT_PATH에 "
            "Noto Sans CJK 폰트 경로를 설정하세요."
        )

    image = Image.new("RGB", (1080, 1350), "white")
    draw = ImageDraw.Draw(image)

    def font(size):
        return ImageFont.truetype(font_path, size)

    def write(text, xy, size=32, fill="#182127"):
        draw.text(
            xy,
            text,
            fill=fill,
            font=font(size),
        )

    def wrapped(text, x, y, size, width):
        line = ""
        text_font = font(size)

        for char in text:
            if draw.textlength(
                line + char,
                font=text_font,
            ) > width:
                write(line, (x, y), size)
                y += size + 12
                line = ""

            line += char

        write(line, (x, y), size)
        return y + size + 12

    write(
        f"아침 7시 · {market} / 가상 예시",
        (50, 35),
        28,
    )
    write("오늘 주목할 이슈 3", (50, 95), 67)
    write(
        "이슈 → 산업 영향 → 주목 종목",
        (50, 190),
        30,
        "#68717a",
    )

    for index, item in enumerate(issues[:3]):
        top = 280 + index * 320

        draw.line(
            (40, top - 15, 1040, top - 15),
            fill="#dce1e5",
            width=2,
        )

        write(
            item["category"],
            (50, top),
            28,
            "#008783",
        )

        y = wrapped(
            item["title"],
            50,
            top + 52,
            45,
            960,
        )

        wrapped(
            item["summary"],
            50,
            y + 10,
            29,
            960,
        )

        draw.rectangle(
            (40, top + 224, 1040, top + 292),
            fill="#eef1f3",
        )

        write(
            "관련 종목   " + " · ".join(item["names"]),
            (65, top + 237),
            30,
            "#008783",
        )

    write(
        "디자인 확인용 가상 사례 · "
        "실제 뉴스나 종목 추천이 아닙니다.",
        (50, 1280),
        22,
        "#7c848d",
    )

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
