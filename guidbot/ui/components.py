"""
ui/components.py  ─  UI 컴포넌트 라이브러리 v7.0 (디자인 리뉴얼)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v7.0 디자인 리뉴얼 — 4가지 핵심 개선]

1. 눈 피로 감소 / 톤 다운
   · 흰 배경 #FFFFFF → 오프화이트 #F7F9FB
   · 카드 배경 채도 낮춤: #F6F8FC → #F1F4F8
   · 테두리 연하게: #e2e8f0 → #DDE4ED
   · 본문 텍스트: 순검정 제거 → 소프트 네이비 #1C2D42

2. 폰트 스케일 3단계로 통일
   · 기존 8가지(0.65~0.9375rem) → XS=0.72 / SM=0.82 / BASE=0.90rem

3. 출처 카드 — '원문 발췌' + 'PDF 보기' 카드 내 통합
   · st.expander 제거 → HTML <details> 인라인 토글 (카드 안에 완전 포함)
   · PDF 버튼: 전체너비 큰 버튼 → 소형 st.download_button

4. 바로가기 버튼 통일
   · sidebar.py 에서 HTML 렌더 pill 5개로 통일 (회람·지침·원무·간호·지원)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import streamlit as st

from ui.theme import UITheme as T

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  v7.0 디자인 상수 (3단계 폰트 + 톤다운 팔레트)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_FS_XS = "0.72rem"  # 배지·레이블·메타
_FS_SM = "0.82rem"  # 본문 보조·카드 내용
_FS_BASE = "0.90rem"  # 본문·버튼

_TX1 = "#1C2D42"  # 주 텍스트 (소프트 네이비)
_TX2 = "#3A5068"  # 보조 텍스트
_TX3 = "#6B85A0"  # 힌트·메타
_BG1 = "#F1F4F8"  # 카드 배경
_BG2 = "#E6ECF4"  # 카드 인셋 (발췌)
_BD = "#DDE4ED"  # 테두리

_TRUST_HIGH = "#0088A3"
_TRUST_MID = "#9A6B0A"
_TRUST_LOW = "#8A9BB0"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  사이드바 컴포넌트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def logo_header() -> None:
    st.markdown(
        f"""
        <div style="padding:1.25rem 0.5rem 0.6rem;text-align:center;">
            <div style="width:46px;height:46px;margin:0 auto 0.65rem;position:relative;">
                <div style="
                    width:100%;height:100%;border-radius:12px;
                    background:linear-gradient(135deg,{T.P600} 0%,{T.P800} 100%);
                    position:absolute;top:0;left:0;
                    box-shadow:0 3px 10px rgba(0,40,100,0.28);
                "></div>
                <div style="
                    position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);
                    width:24px;height:8px;
                    background:rgba(255,255,255,0.92);border-radius:2px;
                "></div>
                <div style="
                    position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);
                    width:8px;height:24px;
                    background:rgba(255,255,255,0.92);border-radius:2px;
                "></div>
                <div style="
                    position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);
                    width:6px;height:6px;
                    background:{T.A400};border-radius:50%;
                "></div>
            </div>
            <div style="
                font-size:0.92rem;font-weight:700;
                color:rgba(255,255,255,0.92);
                letter-spacing:-0.01em;line-height:1.3;
            ">좋은문화병원</div>
            <div style="
                font-size:0.60rem;color:{T.A400};
                letter-spacing:0.10em;text-transform:uppercase;
                margin-top:0.18rem;
            ">AI 가이드봇</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_label(text: str, icon: str = "") -> None:
    prefix = f"{icon} " if icon else ""
    st.markdown(
        f"""
        <div style="
            font-size:0.60rem;font-weight:700;
            letter-spacing:0.12em;text-transform:uppercase;
            color:rgba(255,255,255,0.36);
            padding:0 0.2rem 0.45rem;
        ">{prefix}{text}</div>
        """,
        unsafe_allow_html=True,
    )


def status_indicator(is_healthy: bool, message: str) -> None:
    if is_healthy:
        dot = f"""
        <div style="position:relative;width:9px;height:9px;flex-shrink:0;">
            <div style="
                position:absolute;inset:0;border-radius:50%;
                background:{T.A500};opacity:0.28;
                animation:ping 1.5s ease-in-out infinite;
            "></div>
            <div style="position:absolute;inset:1px;border-radius:50%;
                background:{T.A500};"></div>
        </div>"""
        tc = "rgba(255,255,255,0.85)"
    else:
        dot = '<div style="width:9px;height:9px;border-radius:50%;background:#EF4444;flex-shrink:0;"></div>'
        tc = "#FCA5A5"

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.45rem;margin-bottom:0.7rem;">'
        f"{dot}"
        f'<span style="font-size:{_FS_SM};font-weight:600;color:{tc};">{message}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def info_grid(items: list[tuple[str, str]]) -> None:
    rows = ""
    for i, (label, value) in enumerate(items):
        sep = (
            ""
            if i == 0
            else '<div style="height:1px;background:rgba(255,255,255,0.05);margin:0.38rem 0;"></div>'
        )
        rows += (
            f"{sep}"
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="font-size:{_FS_XS};font-weight:600;letter-spacing:0.06em;'
            f'text-transform:uppercase;color:rgba(255,255,255,0.36);">{label}</span>'
            f'<span style="font-size:{_FS_SM};font-weight:700;color:{T.A400};'
            f'font-variant-numeric:tabular-nums;">{value}</span>'
            f"</div>"
        )
    st.markdown(
        f'<div style="background:rgba(0,0,0,0.16);border:1px solid rgba(255,255,255,0.06);'
        f'border-radius:9px;padding:0.75rem;">{rows}</div>',
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  메인 영역 컴포넌트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def page_header() -> None:
    st.markdown(
        f"""
        <div style="padding:1.5rem 0 0.5rem;animation:fadeSlideUp 0.4s ease both;">
            <div style="
                font-size:{_FS_XS};font-weight:600;
                letter-spacing:0.12em;text-transform:uppercase;
                color:{T.A500};margin-bottom:0.4rem;
                display:flex;align-items:center;gap:0.4rem;
            ">
                <span style="display:inline-block;width:13px;height:2px;
                    background:{T.A500};border-radius:1px;"></span>
                좋은문화병원 · 내부 지식 관리 시스템
            </div>
            <h1 style="
                font-size:clamp(1.55rem,3.5vw,2.1rem);
                font-weight:800;letter-spacing:-0.03em;
                color:{_TX1};margin:0 0 0.1rem;line-height:1.15;
            ">좋은문화병원 가이드봇</h1>
            <div style="
                width:34px;height:3px;
                background:linear-gradient(90deg,{T.A500},{T.A400});
                border-radius:2px;margin:0.5rem 0 0.7rem;
            "></div>
            <p style="font-size:{_FS_BASE};color:{_TX2};margin:0;line-height:1.65;">
                병원 규정·지침·취업규칙을 AI로 빠르게 검색합니다.
                근거 문서와 조항을 함께 제공합니다.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tip_banner(tip_text: str) -> None:
    st.markdown(
        f"""
        <div style="
            background:#EBF8FA;border:1px solid #BEE8EF;
            border-left:3px solid {T.A500};border-radius:8px;
            padding:0.65rem 0.85rem;margin:0.6rem 0;
            display:flex;align-items:flex-start;gap:0.55rem;
        ">
            <span style="font-size:{_FS_BASE};flex-shrink:0;margin-top:0.05rem;">💡</span>
            <div>
                <div style="font-size:{_FS_XS};font-weight:700;
                    letter-spacing:0.08em;text-transform:uppercase;
                    color:{T.A600};margin-bottom:0.18rem;">오늘의 팁</div>
                <div style="font-size:{_FS_SM};color:{_TX2};line-height:1.55;">
                    {tip_text}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def error_banner(title: str, description: str) -> None:
    st.markdown(
        f"""
        <div style="
            background:#FEF3F2;border:1px solid #FECDD3;
            border-left:3px solid #EF4444;border-radius:8px;
            padding:0.65rem 0.85rem;margin:0.6rem 0;
            display:flex;align-items:flex-start;gap:0.55rem;
        ">
            <span style="font-size:{_FS_BASE};flex-shrink:0;">⚠️</span>
            <div>
                <div style="font-weight:700;color:#DC2626;
                    font-size:{_FS_SM};margin-bottom:0.18rem;">{title}</div>
                <div style="font-size:{_FS_SM};color:{_TX2};line-height:1.5;">
                    {description}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  홈 화면
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_QUICK_QUESTIONS = [
    ("📋", "연차휴가 신청 절차가 어떻게 되나요?"),
    ("💰", "야간근로 수당 계산 기준이 궁금합니다"),
    ("📄", "재직증명서는 어떻게 발급받나요?"),
    ("⚕️", "감염관리 지침 핵심 내용을 알려주세요"),
    ("👔", "복무 규정 위반 시 징계 기준은?"),
    ("🏥", "당직 근무 수당 지급 기준을 알고 싶어요"),
]


def home_screen() -> None:
    """
    홈 화면 — 예시 질문 칩 버튼.

    [v2.0 수정]
    - st.markdown() → st.button() 변환 (클릭 이벤트 연결)
    - 버튼 클릭 시 prefill_prompt session_state 설정 → st.rerun() → 자동 질문 입력
    - 레이아웃 압축: 2열 그리드로 공간 효율 극대화
    - 메인 채팅 영역 최대화를 위해 헤더 높이 최소화
    """
    import streamlit as st

    # 압축된 헤더 — 채팅 영역 최대화
    st.markdown(
        f"""
        <div style="padding:1.2rem 0 0.8rem;display:flex;align-items:center;gap:0.5rem;">
            <span style="font-size:1rem;opacity:0.5;">💬</span>
            <span style="font-size:0.85rem;font-weight:600;color:{_TX2};
                letter-spacing:-0.01em;">예시 질문을 선택하거나 직접 입력하세요</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 예시 질문 칩 버튼 — st.button 클릭 시 prefill_prompt 설정
    # 버튼 스타일: 칩형(chip) — 얇은 테두리, 컴팩트한 패딩
    st.markdown(
        f"""
        <style>
        div[data-testid="stButton"][id^="chip_"] > button {{
            background: {_BG1} !important;
            border: 1px solid {_BD} !important;
            border-radius: 20px !important;
            padding: 0.35rem 0.8rem !important;
            font-size: 12px !important;
            color: {_TX2} !important;
            text-align: left !important;
            white-space: nowrap !important;
            height: auto !important;
            line-height: 1.4 !important;
            font-weight: 400 !important;
            transition: all 120ms ease !important;
        }}
        div[data-testid="stButton"][id^="chip_"] > button:hover {{
            background: {_BG2} !important;
            border-color: {T.A500} !important;
            color: {_TX1} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(2, gap="small")
    for i, (icon, question) in enumerate(_QUICK_QUESTIONS):
        with cols[i % 2]:
            label = f"{icon} {question}"
            if st.button(
                label,
                key=f"chip_q_{i}",
                use_container_width=True,
            ):
                st.session_state["prefill_prompt"] = question
                st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  출처 카드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def source_section_header(count: int) -> None:
    st.markdown(
        f"""
        <div style="
            display:flex;align-items:center;gap:0.38rem;
            margin:1rem 0 0.45rem;
            padding-bottom:0.38rem;
            border-bottom:1px solid {_BD};
        ">
            <span style="font-size:{_FS_SM};">📎</span>
            <span style="font-size:{_FS_SM};font-weight:700;color:{_TX1};">참조 규정</span>
            <span style="
                background:{T.A500};color:white;
                font-size:{_FS_XS};font-weight:700;
                padding:0.08rem 0.38rem;border-radius:99px;
            ">{count}건</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def source_trust_card(
    rank: int,
    source: str,
    page: str,
    score: float = 0.5,
    article: str = "",
    revision_date: str = "",
    chunk_text: str = "",
    doc_path: Optional[Path] = None,
    card_ns: str = "",
    pdf_bytes: Optional[bytes] = None,   # 파일 없을 때 bytes로 직접 다운로드
) -> None:
    """
    출처 카드 v7.0 — 통합 레이아웃.

    핵심 변경:
    - st.expander("원문 발췌") → HTML <details> 토글 (카드 안에 인라인 포함)
    - "PDF 원문 보기" 큰 버튼 → 소형 st.download_button
    - 두 요소 모두 카드와 한 덩어리로 보이도록 처리
    """
    # 순위 accent
    accent = {1: T.P800, 2: T.A500, 3: T.A600}.get(rank, _TX3)

    # 신뢰도 (sigmoid 정규화)
    pct = score if 0.0 <= score <= 1.0 else 1.0 / (1.0 + math.exp(-score))
    pct_int = round(pct * 100)
    bar_w = max(pct_int, 5)

    if pct >= 0.75:
        tlabel, tcol = "높음", _TRUST_HIGH
    elif pct >= 0.45:
        tlabel, tcol = "보통", _TRUST_MID
    else:
        tlabel, tcol = "낮음", _TRUST_LOW

    # 조항
    art_html = (
        f'<span style="color:{_TX3};font-size:{_FS_XS};"> · {article}</span>'
        if article
        else ""
    )

    # 개정일
    if revision_date:
        date_html = (
            f'<span style="background:rgba(0,151,178,0.07);color:{T.A600};'
            f"font-size:{_FS_XS};font-weight:600;"
            f"padding:0.08rem 0.4rem;border-radius:4px;"
            f'border:1px solid rgba(0,151,178,0.18);">개정 {revision_date}</span>'
        )
    else:
        date_html = (
            f'<span style="background:{_BG2};color:{_TX3};'
            f"font-size:{_FS_XS};padding:0.08rem 0.4rem;"
            f'border-radius:4px;border:1px solid {_BD};">개정일 미등록</span>'
        )

    # 원문 발췌 — HTML <details> 인라인 (st.expander 제거)
    chunk_html = ""
    if chunk_text and chunk_text.strip():
        preview = chunk_text.strip()[:280]
        chunk_html = (
            f'<details style="margin-top:0.52rem;">'
            f'<summary style="font-size:{_FS_XS};font-weight:600;color:{_TX3};'
            f"cursor:pointer;list-style:none;"
            f'display:inline-flex;align-items:center;gap:0.28rem;user-select:none;">'
            f'<span style="font-size:0.58rem;">▶</span> 원문 발췌'
            f"</summary>"
            f'<div style="margin-top:0.3rem;padding:0.45rem 0.55rem;'
            f"background:{_BG2};border-radius:5px;border-left:2px solid {_BD};"
            f'font-size:{_FS_XS};color:{_TX2};line-height:1.7;white-space:pre-wrap;">'
            f"{preview}</div>"
            f"</details>"
        )

    # 카드 렌더
    st.markdown(
        f"""
        <div style="
            background:{_BG1};border:1px solid {_BD};
            border-left:3px solid {accent};
            border-radius:9px;padding:0.72rem 0.85rem;margin-bottom:0.45rem;
        ">
            <div style="display:flex;align-items:flex-start;
                justify-content:space-between;gap:0.45rem;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:{_FS_SM};font-weight:700;color:{_TX1};
                        margin-bottom:0.28rem;
                        display:flex;align-items:center;gap:0.32rem;flex-wrap:wrap;">
                        <span style="background:{accent};color:white;
                            font-size:{_FS_XS};font-weight:800;
                            padding:0.06rem 0.38rem;border-radius:4px;
                            flex-shrink:0;">{rank}</span>
                        <span style="overflow:hidden;text-overflow:ellipsis;
                            white-space:nowrap;max-width:210px;"
                            title="{source}">{source}</span>
                        <span style="color:{_TX3};font-size:{_FS_XS};
                            font-family:monospace;white-space:nowrap;">
                            p.{page}{art_html}</span>
                    </div>
                    {date_html}
                </div>
                <div style="display:flex;flex-direction:column;
                    align-items:flex-end;gap:0.18rem;flex-shrink:0;">
                    <span style="font-size:{_FS_XS};font-weight:700;
                        color:{tcol};">신뢰도 {tlabel}</span>
                    <div style="width:48px;height:3px;background:{_BD};
                        border-radius:2px;overflow:hidden;">
                        <div style="height:100%;width:{bar_w}%;
                            background:{tcol};border-radius:2px;"></div>
                    </div>
                    <span style="font-size:{_FS_XS};color:{_TX3};
                        font-family:monospace;">{pct_int}%</span>
                </div>
            </div>
            {chunk_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # PDF 다운로드 — doc_path OR pdf_bytes 어느 쪽이든 버튼 렌더
    ns_part = f"{card_ns}_" if card_ns else ""
    uid = f"dl_{ns_part}{rank}_{abs(hash(source)) % 100000}"
    # bytes 확보 우선순위: doc_path → 전달받은 pdf_bytes
    _dl_data: Optional[bytes] = None
    if doc_path is not None:
        try: _dl_data = Path(doc_path).read_bytes()
        except Exception: pass
    if _dl_data is None and pdf_bytes is not None:
        _dl_data = pdf_bytes
    if _dl_data is not None:
        _fname = Path(source).name  # 파일명만 사용
        try:
            st.download_button(
                label=f"↓ PDF 원문  ·  {_fname}",
                data=_dl_data,
                file_name=_fname,
                mime="application/pdf",
                key=uid,
                use_container_width=False,
            )
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  하위 호환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def source_item(rank: int, source: str, page: str = "", article: str = "") -> None:
    article_part = f" · {article}" if article else ""
    page_part = f" (p.{page})" if page else ""
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:0.42rem;'
        f"padding:0.42rem 0.58rem;background:{_BG1};border-radius:7px;"
        f'border-left:3px solid {T.A500};margin-bottom:0.32rem;">'
        f'<span style="font-weight:700;color:{T.A500};flex-shrink:0;'
        f'font-size:{_FS_SM};">{rank}.</span>'
        f'<span style="color:{_TX2};font-size:{_FS_SM};line-height:1.45;">'
        f"{source}{page_part}{article_part}</span></div>",
        unsafe_allow_html=True,
    )


def empty_state(*args, **kwargs) -> None:
    home_screen()