import html
import json
import os
from datetime import datetime, timedelta

import streamlit as st

from cards import render_card
from collectors import investing_feed, naver_reports, pdf_text
from content import ISSUES
from domain import KST, Portfolio, discussion_top, morning_cutoff


# ==================================================
# 기본 설정
# ==================================================

st.set_page_config(
    page_title="강삐 · 강민 주식비서",
    page_icon="☀️",
    layout="centered",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 540px;
        padding: 1.5rem 1rem 7rem;
    }

    header[data-testid="stHeader"] {
        background: transparent;
        pointer-events: none;
    }

    header[data-testid="stHeader"] button {
        pointer-events: auto;
    }

    [data-testid="stToolbar"] {
        display: none;
    }

    h1 {
        font-size: 1.9rem !important;
        letter-spacing: -1px;
        padding: .1rem 0 .6rem !important;
    }

    h2 {
        font-size: 1.45rem !important;
        letter-spacing: -.6px;
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
        margin-bottom: 2px;
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
        margin: 10px 5px 0 0;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
    }

    button {
        min-height: 42px !important;
    }

    button[kind="primary"],
    button[kind="primaryFormSubmit"] {
        background: #505bd1;
        border-color: #505bd1;
    }

    div[data-testid="stMetricValue"] {
        font-size: 1.15rem;
    }

    div[data-testid="stMetricLabel"] {
        font-size: 12px;
    }

    /* 휴대폰에서도 가로 배치 유지 */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
    }

    [data-testid="stColumn"] {
        min-width: 0 !important;
        width: auto !important;
    }

    /* 날짜 아래에 배치되는 메뉴 */
    .st-key-navigation {
        position: static;
        width: 100%;
        padding: 4px;
        background: #f6f7fb;
        border: 1px solid #e3e6ef;
        border-radius: 12px;
    }

    .st-key-navigation [data-testid="stHorizontalBlock"] {
        gap: 4px !important;
    }

    .st-key-navigation button {
        min-height: 46px !important;
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

    /* 국내 / 해외 / 비트코인, 간편 / 상세 */
    [data-testid="stRadio"],
    [data-testid="stRadio"] > div {
        width: 100% !important;
    }

    [data-testid="stRadio"] > div[role="radiogroup"] {
        background: #f6f7fb;
        border: 1px solid #e3e6ef;
        border-radius: 10px;
        padding: 3px;
        display: flex;
        gap: 0 !important;
    }

    [data-testid="stRadio"] label[data-baseweb="radio"] {
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

    /* AI 의견 상자 */
    .st-key-ai_note {
        background: #edf0ff;
        border-radius: 12px;
        padding: 15px;
        color: #263249 !important;
    }

    .st-key-ai_note p {
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
# 저장소와 화면 상태
# ==================================================

portfolio = Portfolio(
    os.getenv("PORTFOLIO_DB", "data/portfolio.db")
)

st.session_state.setdefault("page", "오늘")


def go(page, index=None):
    st.session_state.page = page

    if index is not None:
        st.session_state.selected = index


def edit(asset=None):
    st.session_state.edit_asset = asset or {}
    st.session_state.show_form = True

    # 편집 대상을 바꾸면 입력 폼도 새로 만듭니다.
    st.session_state.form_revision = (
        st.session_state.get("form_revision", 0) + 1
    )


def badge(text, color=""):
    st.markdown(
        f'<span class="badge {color}">'
        f"{html.escape(text)}</span>",
        unsafe_allow_html=True,
    )


# ==================================================
# 상단 이름과 날짜
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

weekdays = ["월", "화", "수", "목", "금", "토", "일"]

date_column, sample_column = st.columns([3, 1])

date_column.caption(
    f"{now.month}월 {now.day}일 "
    f"{weekdays[now.weekday()]}요일 · KST"
)

with sample_column:
    badge("샘플 데이터")


# ==================================================
# 날짜 아래 메뉴
# ==================================================

with st.container(key="navigation"):
    columns = st.columns(4)
    labels = ["오늘", "분석", "인기글", "내 자산"]
    icons = ["⌂", "⌕", "▤", "♙"]

    for column, label, icon in zip(
        columns,
        labels,
        icons,
    ):
        active = st.session_state.page == label

        column.button(
            f"{icon} {label}",
            key="nav_" + label,
            type="primary" if active else "secondary",
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
        list(ISSUES),
        horizontal=True,
        width="stretch",
        label_visibility="collapsed",
        key="market",
    )

    st.caption(
        "최근 7일 리서치 · 핵심 이슈 "
        + str(len(ISSUES[market]))
    )

    for index, item in enumerate(ISSUES[market]):
        with st.container(border=True):
            color = ["", "purple", "teal"][index % 3]

            badge(item["category"], color)
            st.subheader(item["title"])
            st.write(item["summary"])

            if index == 0:
                st.write(item["impact"])

            names_column, action_column = st.columns(
                [2, 1]
            )

            names_html = " ".join(
                f'<span class="tag">'
                f"{html.escape(name)}</span>"
                for name in item["names"]
            )

            names_column.markdown(
                names_html,
                unsafe_allow_html=True,
            )

            action_column.button(
                "분석 보기 ›",
                key=f"issue_{market}_{index}",
                use_container_width=True,
                on_click=go,
                args=("분석", index),
            )

    with st.container(border=True):
        text_column, action_column = st.columns([2, 1])

        text_column.write("**내 자산 관련 소식**")

        text_column.caption(
            f"보유자산 {len(portfolio.all())}개 · "
            "실제 연결 분석은 준비 중입니다."
        )

        action_column.button(
            "자산 등록",
            on_click=go,
            args=("내 자산",),
            use_container_width=True,
        )

    # 국내 1장 / 해외 1장 별도 생성
    with st.expander(
        "카카오톡용 카드뉴스 · 국내 / 해외"
    ):
        st.caption(
            "국내 1장, 해외 1장을 각각 생성합니다. "
            "현재 내용은 가상 예시이며 "
            "카카오톡 자동 발송은 미연결입니다."
        )

        card_columns = st.columns(2)

        for target, column in zip(
            ["국내", "해외"],
            card_columns,
        ):
            try:
                card_data = render_card(
                    target,
                    ISSUES[target],
                    os.getenv("CARD_FONT_PATH"),
                )

                column.download_button(
                    f"{target} 카드 PNG",
                    data=card_data,
                    file_name=(
                        f"gangppi-{target}-sample.png"
                    ),
                    mime="image/png",
                    key="card_" + target,
                    use_container_width=True,
                )

            except (ValueError, OSError) as error:
                column.warning(str(error))


# ==================================================
# 분석
# ==================================================

elif page == "분석":
    st.button(
        "‹ 오늘로",
        on_click=go,
        args=("오늘",),
    )

    market = st.session_state.get("market", "국내")
    items = ISSUES[market]

    index = min(
        st.session_state.get("selected", 0),
        len(items) - 1,
    )

    selected = st.selectbox(
        "이슈 선택",
        range(len(items)),
        index=index,
        format_func=lambda i: items[i]["title"],
    )

    item = items[selected]

    badge(item["category"])
    st.header(item["title"])

    mode = st.radio(
        "읽기 방식",
        ["간편", "상세"],
        index=1,
        horizontal=True,
        width="stretch",
        label_visibility="collapsed",
    )

    with st.container(border=True):
        st.subheader("핵심 요약")
        st.write(item["summary"])
        st.write(item["impact"])

    if mode == "상세":
        with st.container(border=True):
            st.subheader("보고서에서 확인할 내용")

            checks = [
                "투자 규모와 집행 일정",
                "기업의 실제 공급 관계",
                "실적에 반영되는 시점",
            ]

            for number, text in enumerate(checks, 1):
                st.write(f"**{number}**　{text}")

        with st.expander(
            "관련 종목 · ETF",
            expanded=True,
        ):
            for name in item["names"]:
                st.write(
                    f"**{name}**　"
                    "가상 자산 · 연결 관계 설명용"
                )

        with st.expander("다른 시각과 위험 요인"):
            st.write(item["risk"])

    with st.container(key="ai_note"):
        st.markdown("**✦ AI 의견 · 예시**")

        st.write(
            "계획 발표만으로 판단하기보다는 "
            "실제 수주와 실적을 확인할 필요가 있습니다."
        )

        st.caption(
            "AI API 미연결 · 미리 작성한 예시입니다."
        )

    with st.expander("📖 수주잔고"):
        st.write(
            "계약을 맺었지만 아직 납품하지 않은 "
            "물량입니다. 향후 매출의 단서지만 "
            "취소·지연될 수 있습니다."
        )

    with st.expander("출처 · 원문 보기"):
        st.write(
            "현재 내용은 가상 사례로 실제 보고서 "
            "출처가 없습니다. 설정에서 원문 수집 "
            "연결을 확인할 수 있습니다."
        )


# ==================================================
# 내 자산
# ==================================================

elif page == "내 자산":
    st.header("내 자산")
    st.write("보유내역을 직접 관리하세요.")

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
            "보유자산을 추가해 보세요. "
            "예시 자산은 자동 등록하지 않습니다."
        )

    for asset in assets:
        with st.container(border=True):
            st.subheader(asset["name"])
            badge(asset["kind"])

            quantity_column, price_column = st.columns(2)

            unit = (
                "개"
                if asset["kind"] == "비트코인"
                else "주"
            )

            quantity_column.metric(
                "보유 수량",
                f'{asset["quantity"]:g}{unit}',
            )

            price_column.metric(
                "평균 매수가",
                f'{asset["price"]:,.2f} '
                + asset["currency"],
            )

            quantity_column.button(
                "수정",
                key="edit_" + asset["id"],
                on_click=edit,
                args=(asset,),
                use_container_width=True,
            )

            if price_column.button(
                "삭제",
                key="delete_" + asset["id"],
                use_container_width=True,
            ):
                st.session_state.delete_id = asset["id"]

            if (
                st.session_state.get("delete_id")
                == asset["id"]
            ):
                st.warning("이 보유내역을 삭제할까요?")

                if st.button(
                    "삭제 확인",
                    key="confirm_" + asset["id"],
                ):
                    portfolio.delete(asset["id"])

                    st.session_state.pop(
                        "delete_id",
                        None,
                    )

                    current_edit = st.session_state.get(
                        "edit_asset",
                        {},
                    )

                    if (
                        current_edit.get("id")
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

    if st.session_state.get("show_form"):
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
                value=current.get("name", ""),
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
                    current.get("kind", "국내주식")
                ),
            )

            quantity = st.number_input(
                "수량",
                min_value=0.0,
                value=float(
                    current.get("quantity", 1)
                ),
                format="%.6f",
            )

            price = st.number_input(
                "평균 매수가",
                min_value=0.0,
                value=float(
                    current.get("price", 0)
                ),
            )

            currencies = ["KRW", "USD"]

            currency = st.selectbox(
                "통화",
                currencies,
                index=currencies.index(
                    current.get("currency", "KRW")
                ),
            )

            save_column, cancel_column = st.columns(2)

            if save_column.form_submit_button(
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
                    st.error(str(error))

            if cancel_column.form_submit_button(
                "취소",
                use_container_width=True,
            ):
                st.session_state.show_form = False
                st.rerun()

    st.caption(
        "이 앱 인스턴스의 공용 저장소입니다. "
        "개인용으로 사용하세요. "
        "재배포 후 보존은 영구 저장소가 필요합니다."
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
    st.header("사람들이 많이 본 이야기")

    cutoff = morning_cutoff()

    st.caption(
        f"{cutoff:%m/%d %H:%M} KST · "
        "직전 24시간 · 동일 사건 중복 제거"
    )

    st.info(
        "실제 토론방은 미연결입니다. "
        "순위 확인용 가상 게시글입니다."
    )

    titles = [
        "관세 발언과 무역 변화",
        "전력망 투자 확대",
        "유가와 운송비",
        "AI 설비 투자",
        "금리 발표 일정",
        "반도체 공급망",
        "자사주 소각",
        "환율 변동",
        "전쟁과 원자재",
        "디지털 자산 수급",
    ]

    posts = [
        {
            "title": title,
            "topic_id": str(index),
            "views": 25000 - index * 1700,
            "published_at": (
                cutoff
                - timedelta(hours=index + 1)
            ).isoformat(),
        }
        for index, title in enumerate(titles)
    ]

    # 같은 사건의 조회수가 낮은 글은 제외됩니다.
    posts.append({
        "title": "동일 관세 사건",
        "topic_id": "0",
        "views": 100,
        "published_at": (
            cutoff - timedelta(hours=2)
        ).isoformat(),
    })

    ranked_posts = discussion_top(
        posts,
        cutoff,
    )

    for rank, post in enumerate(ranked_posts, 1):
        with st.container(border=True):
            st.write(
                f'**{rank:02d}　{post["title"]}**'
            )

            st.caption(
                f'가상 조회수 {post["views"]:,} · '
                "확인되지 않은 작성자 주장"
            )


# ==================================================
# 설정 / 자료 연결
# ==================================================

else:
    st.header("설정 · 자료 연결")

    st.caption(
        "연결 결과는 샘플 분석을 "
        "자동 대체하지 않습니다."
    )

    with st.expander(
        "네이버 리서치",
        expanded=True,
    ):
        if st.button("공개 보고서 목록 확인"):
            try:
                with st.spinner("목록 확인 중…"):
                    st.session_state.reports = (
                        naver_reports()
                    )

            except Exception as error:
                st.session_state.reports = []
                st.error(f"수집 실패: {error}")

        reports = st.session_state.get(
            "reports",
            [],
        )

        for report in reports[:5]:
            st.link_button(
                report["title"],
                report["url"],
            )

        if (
            reports
            and reports[0]["pdf"]
            and st.button("첫 보고서 PDF 본문 확인")
        ):
            try:
                st.text_area(
                    "추출 본문 · AI 분석 전",
                    value=pdf_text(
                        reports[0]["pdf"]
                    ),
                    height=280,
                )

            except Exception as error:
                st.error(f"본문 미확보: {error}")

    with st.expander("인베스팅 RSS"):
        if st.button("RSS 연결 확인"):
            try:
                for item in investing_feed()[:10]:
                    st.link_button(
                        item["title"],
                        item["url"],
                    )

                st.caption(
                    "RSS 목록이며 기사 본문 "
                    "확보 결과가 아닙니다."
                )

            except Exception as error:
                st.error(f"연결 실패: {error}")

    st.info(
        "자동 AI 분석·06:30 집계 실행·"
        "07:00 카카오톡 발송은 아직 미연결입니다."
    )


st.caption(
    "강삐 · 가상 데이터 MVP · "
    "실제 뉴스 및 투자 추천이 아닙니다."
)
