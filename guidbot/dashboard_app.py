"""
dashboard_app.py  ─  좋은문화병원 병동 현황 대시보드 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[목적]
    이 파일은 병원 운영 현황을 실시간으로 확인하는
    "병동 대시보드 전용 Streamlit 앱"의 진입점입니다.
    RAG 챗봇(main.py)과 완전히 분리된 독립 프로그램입니다.

[분리 이유]
    ┌─────────────────────────────────────────────────────┐
    │  main.py (포트 8502)         dashboard_app.py (8503) │
    │  ─────────────────          ─────────────────────── │
    │  · RAG 규정 검색              · 병동 입퇴원 현황     │
    │  · SQL 대시보드               · 진료과별 재원 파이   │
    │  · 데이터 분석                · 주간 추이 차트       │
    │  → 사용자: 전 직원            → 사용자: 통계과/수간호 │
    └─────────────────────────────────────────────────────┘

    분리 이점:
    1. main.py는 무거운 AI 리소스(FAISS, LLM)를 로드하지만
       dashboard_app.py는 Oracle 조회만 하므로 훨씬 가볍다
    2. 한쪽이 오류로 다운되어도 나머지는 계속 동작
    3. 각 앱을 독립적으로 배포/업데이트 가능

[실행 방법]
    # 병동 대시보드 전용 (포트 8503)
    streamlit run dashboard_app.py --server.port 8503

    # RAG 챗봇 (포트 8502) — 기존 그대로
    streamlit run main.py --server.port 8502

[VB6 연동 URL]
    (별도 page= 파라미터 불필요 — 항상 병동 화면 표시)

[의존 모듈]
    ui/hospital_dashboard.py  ← 실제 화면 렌더링 (main.py와 공유)
    db/oracle_client.py       ← Oracle 연결
    config/settings.py        ← 환경 변수 설정
    ui/theme.py               ← CSS 테마
"""

from __future__ import annotations

# ── 표준 라이브러리 ────────────────────────────────────────────────────
import time  # 갱신 시각 표시용
from pathlib import Path  # 파일 경로 처리용

# ── 서드파티 라이브러리 ────────────────────────────────────────────────
import streamlit as st  # Streamlit 웹 프레임워크

# ── 내부 모듈 ─────────────────────────────────────────────────────────
from config.settings import settings  # 환경 설정 (DB 주소 등)
from ui.theme import UITheme as T  # 공통 CSS 테마
from ui.hospital_dashboard import render_hospital_dashboard  # 실제 대시보드 UI
from utils.logger import get_logger  # 로거

# ── 로거 초기화 ────────────────────────────────────────────────────────
# 이 모듈 전용 로거. 로그 파일은 settings.log_dir 에 저장됨
logger = get_logger(__name__, log_dir=settings.log_dir)


# ══════════════════════════════════════════════════════════════════════
# Streamlit 페이지 설정
# st.set_page_config()는 반드시 다른 st 호출 전 최상단에 위치해야 함
# ══════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="병동 현황 대시보드 | 좋은문화병원",
    page_icon="🏥",
    layout="wide",  # 화면 전체 너비 사용
    initial_sidebar_state="collapsed",  # 사이드바 기본 접힘 (대시보드는 전체화면)
)

# ── 공통 CSS 테마 적용 ─────────────────────────────────────────────────
# ui/theme.py 에서 정의한 CSS를 전역으로 주입
st.markdown(T.get_global_css(), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# 상단 미니 사이드바 (접힘 상태에서도 앱 정보 표시)
# ══════════════════════════════════════════════════════════════════════
def _render_mini_sidebar() -> None:
    """
    대시보드 전용 미니 사이드바.
    RAG 챗봇 사이드바와 완전히 다른 구성.
    역할: Oracle 상태 표시 + 챗봇 이동 링크 + 관리 정보
    """
    with st.sidebar:
        # ── 병원 로고 영역 ───────────────────────────────────────────
        st.markdown(
            '<div style="display:flex;align-items:center;gap:10px;'
            "padding:12px 0 16px;border-bottom:1px solid rgba(255,255,255,0.15);"
            'margin-bottom:16px;">'
            '<span style="font-size:22px;">🏥</span>'
            "<div>"
            '<div style="font-size:14px;font-weight:700;color:#FFFFFF;">좋은문화병원</div>'
            '<div style="font-size:10px;color:rgba(255,255,255,0.5);">병동 현황 대시보드</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )

        # ── Oracle 연결 상태 확인 ────────────────────────────────────
        # Oracle 접속 시도는 비용이 크므로 세션당 1회만 수행
        # 이후 새로고침 버튼 클릭 시 초기화됨
        if "dash_oracle_ok" not in st.session_state:
            _ok = False
            try:
                from db.oracle_client import test_connection

                _ok, _ = test_connection()
            except Exception:
                pass
            st.session_state["dash_oracle_ok"] = _ok

        _oracle_ok = st.session_state.get("dash_oracle_ok", False)

        # Oracle 상태 배지
        if _oracle_ok:
            st.markdown(
                '<div style="display:flex;align-items:center;gap:6px;'
                "background:rgba(22,163,74,0.15);border:1px solid rgba(22,163,74,0.3);"
                'border-radius:6px;padding:6px 10px;margin-bottom:10px;">'
                '<span style="width:8px;height:8px;border-radius:50%;'
                'background:#16A34A;display:inline-block;flex-shrink:0;"></span>'
                '<span style="font-size:12px;font-weight:600;color:#16A34A;">Oracle 연결 정상</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="display:flex;align-items:center;gap:6px;'
                "background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.3);"
                'border-radius:6px;padding:6px 10px;margin-bottom:10px;">'
                '<span style="width:8px;height:8px;border-radius:50%;'
                'background:#F59E0B;display:inline-block;flex-shrink:0;"></span>'
                '<span style="font-size:12px;font-weight:600;color:#F59E0B;">데모 데이터</span>'
                "</div>",
                unsafe_allow_html=True,
            )

        # ── 갱신 시각 표시 ──────────────────────────────────────────
        _last_ts = st.session_state.get("dash_last_ts", time.strftime("%Y-%m-%d %H:%M"))
        st.markdown(
            f'<div style="font-size:11px;color:rgba(255,255,255,0.45);'
            f'margin-bottom:16px;">마지막 갱신: {_last_ts}</div>',
            unsafe_allow_html=True,
        )

        # ── 링크: RAG 챗봇으로 이동 ─────────────────────────────────
        st.markdown(
            '<div style="margin-bottom:16px;">'
            '<a href="http://192.1.1.234:8502/" target="_blank" style="'
            "display:flex;align-items:center;gap:6px;"
            "background:rgba(30,64,175,0.20);border:1px solid rgba(30,64,175,0.35);"
            'border-radius:7px;padding:8px 12px;text-decoration:none;">'
            '<span style="font-size:14px;">💬</span>'
            "<div>"
            '<div style="font-size:12px;font-weight:600;color:rgba(255,255,255,0.88);">AI 챗봇</div>'
            '<div style="font-size:10px;color:rgba(255,255,255,0.40);">규정·지침 검색</div>'
            "</div>"
            '<span style="margin-left:auto;font-size:11px;color:rgba(255,255,255,0.35);">↗</span>'
            "</a></div>",
            unsafe_allow_html=True,
        )

        st.divider()

        # ── 버전 정보 ────────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:10px;color:rgba(255,255,255,0.25);'
            'text-align:center;padding-top:8px;">'
            "병동 대시보드 v1.0<br>"
            "좋은문화병원 통계과"
            "</div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════
# 메인 함수 — Streamlit 진입점
# ══════════════════════════════════════════════════════════════════════
def main() -> None:
    """
    dashboard_app.py 진입점.

    [화면 구성]
    1. 사이드바: Oracle 상태 + 챗봇 링크
    2. 메인 영역: render_hospital_dashboard(tab="ward") 호출

    [tab 고정]
    이 앱은 병동 대시보드만 표시.
    원무/외래는 main.py 관리자 모드에서만 접근 가능.
    """
    logger.info("dashboard_app 진입 — 병동 대시보드")

    # 사이드바 렌더
    _render_mini_sidebar()

    # ── 갱신 시각 세션 초기화 ──────────────────────────────────────
    # dashboard_app에서는 별도 새로고침 버튼을 헤더에 두므로
    # session_state 키를 ward 탭 기준으로 초기화
    if "dash_last_ts" not in st.session_state:
        st.session_state["dash_last_ts"] = time.strftime("%Y-%m-%d %H:%M")

    # ── 병동 대시보드 렌더 ─────────────────────────────────────────
    # hospital_dashboard.py 의 render_hospital_dashboard() 를 직접 호출.
    # tab="ward" 고정 → 병동 탭만 표시 (원무/외래는 이 앱에서 제외)
    try:
        render_hospital_dashboard(tab="ward")
    except Exception as e:
        # 오류 발생 시 사용자에게 안내하고 로그에 기록
        st.error(
            f"대시보드 로드 중 오류가 발생했습니다.\n\n"
            f"오류 내용: {e}\n\n"
            f"Oracle 연결 상태를 확인하거나 관리자에게 문의하세요."
        )
        logger.error(f"dashboard_app 렌더 오류: {e}", exc_info=True)


# ── 스크립트 직접 실행 시 진입점 ──────────────────────────────────────
# Streamlit은 이 파일을 직접 import하거나
# `streamlit run dashboard_app.py` 로 실행함
if __name__ == "__main__":
    main()
