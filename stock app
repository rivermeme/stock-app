import html
import json
import os
from datetime import datetime, timedelta
import streamlit as st
from cards import render_card
from collectors import naver_reports, pdf_text, investing_feed
from content import ISSUES
from domain import Portfolio, KST, discussion_top, morning_cutoff

st.set_page_config(page_title='모닝브리프', page_icon='☀️', layout='centered')
st.markdown('''<style>
.block-container{max-width:740px;padding-top:2rem;padding-bottom:5rem} h1{font-size:1.85rem!important;letter-spacing:-1.2px} h2{font-size:1.4rem!important} h3{font-size:1.1rem!important} .eyebrow{color:#008783;font-weight:700;font-size:.86rem}.issue{border:1px solid #e1e5ee;border-radius:14px;padding:20px;margin:12px 0;background:#fff}.issue h3{margin:8px 0}.issue p{color:#596173;line-height:1.7;margin:5px 0}.tag{background:#eef0ff;border-radius:7px;padding:5px 10px;font-size:.8rem;color:#4252bb}.sample{color:#485aca;background:#eef1ff;padding:6px 10px;border-radius:8px;font-size:.8rem}.meta{font-size:.85rem;color:#7b8391}div[data-testid="stMetricValue"]{font-size:1.35rem}button{min-height:44px} @media(max-width:600px){.block-container{padding:1.2rem 1rem 4rem}h1{font-size:1.65rem!important}}
</style>''', unsafe_allow_html=True)
portfolio = Portfolio(os.getenv('PORTFOLIO_DB', 'data/portfolio.db'))
if 'page' not in st.session_state: st.session_state.page = '오늘'
if 'next_page' in st.session_state: st.session_state.page = st.session_state.pop('next_page')

def navigate(page):
    st.session_state.next_page = page

st.markdown('<span class="eyebrow">아침을 여는 투자 이야기</span>', unsafe_allow_html=True)
left, right = st.columns([3,1])
left.title('모닝브리프')
right.markdown('<span class="sample">샘플 데이터</span>', unsafe_allow_html=True)
now = datetime.now(KST)
st.caption(now.strftime('%Y년 %m월 %d일') + ' · 매일 아침 7시 KST')
page = st.radio('메뉴', ['오늘', '분석', '인기글', '내 자산', '자료 연결'], horizontal=True, label_visibility='collapsed', key='page')
st.divider()

if page in ('오늘', '분석'):
    market = st.radio('시장', list(ISSUES), horizontal=True, key='market') or '국내'
    items = ISSUES[market]
    if page == '오늘':
        st.header('오늘 알아둘 이야기')
        st.caption('최근 7일 리서치를 위한 화면 · 현재는 가상 예시입니다')
        for index, item in enumerate(items):
            names = ' '.join(f'<span class="tag">{html.escape(n)}</span>' for n in item['names'])
            st.markdown(f'<div class="issue"><span class="eyebrow">{item["category"]}</span><h3>{item["title"]}</h3><p>{item["summary"]}<br>{item["impact"]}</p><br>{names}</div>', unsafe_allow_html=True)
            if st.button('분석 보기 →', key=f'open-{market}-{index}', use_container_width=True):
                st.session_state.selected = index
                navigate('분석'); st.rerun()
        with st.container(border=True):
            st.subheader('내 자산과 연결해 보세요')
            st.write(f'등록된 자산 {len(portfolio.all())}개 · 실제 연결 분석은 아직 제공하지 않습니다.')
            st.button('자산 등록', on_click=navigate, args=('내 자산',))
        if market != '비트코인':
            try:
                st.download_button('카카오톡용 카드뉴스 PNG 다운로드', render_card(market, items, os.getenv('CARD_FONT_PATH')), file_name=f'morning-{market}-sample.png', mime='image/png', use_container_width=True)
            except ValueError as error: st.info(str(error))
        st.caption('이미지 저장 기능입니다. 카카오톡 자동 발송은 연결되지 않았습니다.')
    else:
        selected = st.selectbox('이슈 선택', range(len(items)), index=min(st.session_state.get('selected',0),len(items)-1), format_func=lambda i: items[i]['title'])
        item = items[selected]
        st.markdown(f'<span class="eyebrow">{item["category"]}</span>', unsafe_allow_html=True)
        st.header(item['title'])
        mode = st.radio('읽기 방식', ['간편', '상세'], index=1, horizontal=True)
        with st.container(border=True):
            st.subheader('핵심 요약'); st.write(item['summary']); st.write(item['impact'])
        if mode == '상세':
            with st.container(border=True):
                st.subheader('보고서에서 확인할 내용')
                st.write('1. 투자 규모와 집행 일정\n\n2. 기업의 실제 공급 관계\n\n3. 실적에 반영되는 시점')
            with st.expander('관련 종목 · ETF', expanded=True):
                for name in item['names']: st.write(f'**{name}** — 가상 기업 · 연결 관계 설명용')
            with st.expander('다른 시각과 위험 요인'): st.write(item['risk'])
        st.info('✦ AI 점검 의견 · 예시\n\n계획 발표만으로 판단하기보다는 실제 수주와 실적을 확인할 필요가 있습니다. 현재 AI API는 연결되지 않았습니다.')
        with st.expander('📖 수주잔고란?'): st.write('계약을 맺었지만 아직 납품하지 않은 물량입니다. 향후 매출의 단서지만 취소·지연될 수 있습니다.')
        with st.expander('출처 · 원문 보기'): st.write('이 내용은 UI 검증을 위한 가상 사례입니다. 실제 보고서 출처가 없으며 실시간 분석이 아닙니다. 자료 연결 메뉴에서 실제 원문 접근을 확인할 수 있습니다.')
elif page == '내 자산':
    st.header('내 자산')
    st.write('보유내역을 직접 관리하세요.')
    st.caption('이 배포 인스턴스의 공용 저장소입니다. 개인용으로 배포하세요. 클라우드 재배포 시 영구 디스크가 필요합니다.')
    assets = portfolio.all()
    st.button('＋ 자산 추가', type='primary', use_container_width=True, on_click=lambda: st.session_state.update(edit_asset={}, show_form=True))
    for asset in assets:
        with st.container(border=True):
            st.subheader(asset['name']); st.caption(asset['kind'])
            a,b,c,d = st.columns([2,2,1,1])
            a.metric('보유 수량', f'{asset["quantity"]:g}')
            b.metric('평균 매수가', f'{asset["price"]:,.2f} {asset["currency"]}')
            c.button('수정', key='edit'+asset['id'], on_click=lambda x=asset: st.session_state.update(edit_asset=x, show_form=True))
            if d.button('삭제', key='del'+asset['id']): st.session_state.delete_id = asset['id']
            if st.session_state.get('delete_id') == asset['id']:
                st.warning('이 자산을 삭제할까요?')
                if st.button('삭제 확인', key='confirm'+asset['id']):
                    portfolio.delete(asset['id']); st.session_state.pop('delete_id'); st.rerun()
    if not assets: st.info('아직 보유자산이 없습니다. 자산 추가로 시작하세요.')
    if st.session_state.get('show_form'):
        current = st.session_state.get('edit_asset', {})
        with st.form('asset_form', clear_on_submit=False):
            st.subheader('보유내역 수정' if current else '새 자산 등록')
            name = st.text_input('종목', value=current.get('name',''))
            kinds = ['국내주식','해외주식','국내 ETF','해외 ETF','비트코인']
            kind = st.selectbox('자산 유형', kinds, index=kinds.index(current.get('kind','국내주식')))
            quantity = st.number_input('수량', min_value=0.0, value=float(current.get('quantity',1)), format='%.6f')
            price = st.number_input('평균 매수가', min_value=0.0, value=float(current.get('price',0)))
            currency = st.selectbox('통화', ['KRW','USD'], index=['KRW','USD'].index(current.get('currency','KRW')))
            save,cancel = st.columns(2)
            if save.form_submit_button('저장', type='primary', use_container_width=True):
                try:
                    portfolio.save(name,kind,quantity,price,currency,current.get('id'))
                    st.session_state.show_form=False; st.rerun()
                except ValueError as error: st.error(str(error))
            if cancel.form_submit_button('취소', use_container_width=True):
                st.session_state.show_form=False; st.rerun()
    st.download_button('보유내역 백업 JSON', json.dumps(assets,ensure_ascii=False,indent=2), 'portfolio.json','application/json')
elif page == '인기글':
    st.header('사람들이 많이 본 이야기')
    cutoff = morning_cutoff()
    st.caption(f'{cutoff:%m/%d %H:%M} KST 기준 · 직전 24시간 · 주제 중복 제거')
    st.info('전체 종목 토론방 수집은 미연결입니다. 아래 10개는 순위 동작 확인용 가상 게시글입니다.')
    titles=['관세 발언과 무역 변화','전력망 투자 확대','유가와 운송비','AI 설비 투자','금리 발표 일정','반도체 공급망','자사주 소각','환율 변동','전쟁과 원자재','디지털 자산 수급']
    posts=[dict(title=t,topic_id=str(i),views=25000-i*1700,published_at=(cutoff-timedelta(hours=i+1)).isoformat()) for i,t in enumerate(titles)]
    posts.append(dict(title='같은 관세 발언의 다른 제목',topic_id='0',views=100,published_at=(cutoff-timedelta(hours=2)).isoformat()))
    for rank, post in enumerate(discussion_top(posts,cutoff),1):
        with st.container(border=True):
            st.write(f'**{rank:02d}　{post["title"]}**')
            st.caption(f'가상 조회수 {post["views"]:,} · 사실 확인 안 됨 · 작성자 주장')
else:
    st.header('자료 연결')
    st.write('원문 접근 결과를 직접 확인합니다. 수집 결과가 샘플 분석을 자동 대체하지는 않습니다.')
    with st.expander('네이버 리서치', expanded=True):
        if st.button('공개 보고서 목록 확인'):
            try:
                st.session_state.reports = naver_reports(); st.success('목록 수집 성공')
            except Exception as error: st.error(f'수집 실패: {error}')
        reports = st.session_state.get('reports', [])
        for report in reports[:5]:
            st.link_button(report['title'], report['url'])
        if reports and reports[0]['pdf'] and st.button('첫 보고서 PDF 본문 확인'):
            try: st.text_area('추출 본문 · AI 분석 전',pdf_text(reports[0]['pdf']),height=280)
            except Exception as error: st.error(f'본문 미확보: {error}')
    with st.expander('인베스팅 RSS'):
        if st.button('RSS 연결 확인'):
            try:
                for item in investing_feed()[:10]: st.link_button(item['title'], item['url'])
                st.caption('RSS 목록만 확인한 결과이며 기사 본문 확보와 구분됩니다.')
            except Exception as error: st.error(f'연결 실패: {error}. 접근 제한을 우회하지 않습니다.')
    st.subheader('확보한 원문 직접 읽기')
    st.text_area('이용 가능한 원문을 붙여넣으세요', height=200)
    st.caption('현재는 읽기용 입력입니다. 자동 요약·번역은 AI 연동 후 제공할 수 있습니다.')
st.divider()
st.caption('모닝브리프 · 가상 데이터 MVP / 투자 판단을 위한 실제 리서치 서비스는 아직 연결되지 않았습니다.')
