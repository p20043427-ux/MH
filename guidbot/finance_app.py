"""
finance_app.py  ─  좋은문화병원 원무 현황 대시보드 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[역할]
  원무팀 / 통계과 전용 대시보드 실행 파일

[실행]
  streamlit run finance_app.py --server.port 8503

[접속]
  http://서버IP:8503

[3탭 구성]
  탭1 실시간 현황  — KPI / 진료과 대기·진료·완료 / 키오스크 / 퇴원 파이프라인
  탭2 수납·미수금  — 보험유형별 파이 / 30일 수납 추세 / 진료과별 수납 / 미수금 연령별
  탭3 통계·분석   — 외래 추세 라인 / 평균 대기시간 추세 / 재원일수 분포

[분리 구성]
  ┌─────────────────────────────────────────────────────┐
  │  dashboard_app.py  (포트 8501)  병동 대시보드       │
  │  finance_app.py    (포트 8503)  원무 대시보드  ★   │
  │  main.py           (포트 8502)  AI 규정 챗봇        │
  └─────────────────────────────────────────────────────┘

[헬스체크]
  http://서버IP:8503/?health=1
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────────────
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import settings
from ui.theme import UITheme as T
from ui.finance_dashboard import render_finance_dashboard
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)


# ══════════════════════════════════════════════════════════════════════
# Windows 프로세스 우선순위
# ══════════════════════════════════════════════════════════════════════
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000,
        )
        logger.info("Windows 프로세스 우선순위: ABOVE_NORMAL 설정 완료")
    except Exception as _e:
        logger.debug(f"우선순위 설정 실패 (무시): {_e}")


# ══════════════════════════════════════════════════════════════════════
# 페이지 설정
# ══════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="원무 현황 대시보드 | 좋은문화병원",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(T.get_global_css(), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# 헬스체크 (?health=1)
# ══════════════════════════════════════════════════════════════════════
if st.query_params.get("health", "") == "1":
    _h: dict = {"status": "ok", "app": "finance_dashboard", "ts": time.time()}
    try:
        from db.oracle_client import test_connection
        _ok, _msg = test_connection()
        _h["oracle"] = _ok
        _h["oracle_msg"] = _msg
    except Exception as _e:
        _h["oracle"] = False
        _h["oracle_msg"] = str(_e)
    try:
        import psutil
        _p = psutil.Process()
        _h["memory_rss_mb"] = round(_p.memory_info().rss / 1024 / 1024, 1)
        _h["system_memory_pct"] = psutil.virtual_memory().percent
    except ImportError:
        _h["psutil"] = "not installed"
    st.json(_h)
    st.stop()


# ══════════════════════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════════════════════
def _render_sidebar() -> str:
    """
    원무 대시보드 사이드바.
    반환: 현재 role ("user" | "admin")
    """
    with st.sidebar:
        # 병원 로고
        st.markdown(
            '<div style="display:flex;align-items:center;gap:10px;'
            "padding:12px 0 16px;border-bottom:1px solid rgba(255,255,255,0.15);"
            'margin-bottom:16px;">'
            '<span style="font-size:22px;">💼</span>'
            "<div>"
            '<div style="font-size:14px;font-weight:700;color:#FFFFFF;">좋은문화병원</div>'
            '<div style="font-size:10px;color:rgba(255,255,255,0.5);">원무 현황 대시보드</div>'
            "</div></div>",
            unsafe_allow_html=True,
        )

        # Oracle 연결 상태
        if "fin_oracle_ok" not in st.session_state:
            _ok = False
            try:
                from db.oracle_client import test_connection
                _ok, _ = test_connection()
            except Exception:
                pass
            st.session_state["fin_oracle_ok"] = _ok

        _oracle_ok = st.session_state.get("fin_oracle_ok", False)
        _oc_bg  = "rgba(22,163,74,0.15)"  if _oracle_ok else "rgba(245,158,11,0.15)"
        _oc_bd  = "rgba(22,163,74,0.3)"   if _oracle_ok else "rgba(245,158,11,0.3)"
        _oc_dot = "#16A34A" if _oracle_ok else "#F59E0B"
        _oc_lbl = "Oracle 연결 정상"      if _oracle_ok else "Oracle 미연결"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:6px;'
            f"background:{_oc_bg};border:1px solid {_oc_bd};"
            f'border-radius:6px;padding:6px 10px;margin-bottom:10px;">'
            f'<span style="width:8px;height:8px;border-radius:50%;'
            f'background:{_oc_dot};display:inline-block;flex-shrink:0;"></span>'
            f'<span style="font-size:12px;font-weight:600;color:{_oc_dot};">{_oc_lbl}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )

        # 마지막 갱신
        _last_ts = st.session_state.get("fin_last_ts", time.strftime("%Y-%m-%d %H:%M"))
        st.markdown(
            f'<div style="font-size:11px;color:rgba(255,255,255,0.45);margin-bottom:16px;">'
            f'마지막 갱신: {_last_ts}</div>',
            unsafe_allow_html=True,
        )

        # 다른 앱 이동 링크
        st.markdown(
            '<div style="margin-bottom:8px;">'
            '<a href="http://192.1.1.231:8501/" target="_blank" style="'
            "display:flex;align-items:center;gap:6px;"
            "background:rgba(30,64,175,0.20);border:1px solid rgba(30,64,175,0.35);"
            'border-radius:7px;padding:8px 12px;text-decoration:none;margin-bottom:6px;">'
            '<span style="font-size:14px;">🏥</span>'
            "<div>"
            '<div style="font-size:12px;font-weight:600;color:rgba(255,255,255,0.88);">병동 대시보드</div>'
            '<div style="font-size:10px;color:rgba(255,255,255,0.40);">입퇴원 현황 (8501)</div>'
            "</div>"
            '<span style="margin-left:auto;font-size:11px;color:rgba(255,255,255,0.35);">↗</span>'
            "</a>"
            '<a href="http://192.1.1.231:8502/" target="_blank" style="'
            "display:flex;align-items:center;gap:6px;"
            "background:rgba(30,64,175,0.20);border:1px solid rgba(30,64,175,0.35);"
            'border-radius:7px;padding:8px 12px;text-decoration:none;">'
            '<span style="font-size:14px;">💬</span>'
            "<div>"
            '<div style="font-size:12px;font-weight:600;color:rgba(255,255,255,0.88);">AI 챗봇</div>'
            '<div style="font-size:10px;color:rgba(255,255,255,0.40);">규정·지침 검색 (8502)</div>'
            "</div>"
            '<span style="margin-left:auto;font-size:11px;color:rgba(255,255,255,0.35);">↗</span>'
            "</a></div>",
            unsafe_allow_html=True,
        )

        st.divider()

        # 시스템 모니터링 (psutil)
        try:
            import psutil
            _proc    = psutil.Process()
            _mem_mb  = round(_proc.memory_info().rss / 1024 / 1024, 0)
            _sys_mem = psutil.virtual_memory()
            _cpu_pct = psutil.cpu_percent(interval=None)
            _mem_color = (
                "#EF4444" if _sys_mem.percent > 85
                else "#F59E0B" if _sys_mem.percent > 70
                else "rgba(255,255,255,0.45)"
            )
            st.markdown(
                f'<div style="font-size:10px;color:rgba(255,255,255,0.45);">'
                f'<div style="margin-bottom:3px;">🖥️ CPU: {_cpu_pct:.0f}%</div>'
                f'<div style="margin-bottom:3px;color:{_mem_color};">'
                f'💾 RAM: {_sys_mem.percent:.0f}% '
                f'({round(_sys_mem.available/1024**3,1)}GB 여유)</div>'
                f'<div>📦 이 앱: {_mem_mb:.0f} MB</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
        except ImportError:
            pass

        st.divider()

        # 관리자 로그인
        _role: str = st.session_state.get("fin_role", "user")
        with st.expander("🔐 관리자", expanded=(_role == "admin")):
            if _role == "admin":
                st.markdown(
                    '<div style="font-size:11px;font-weight:700;'
                    'color:#4ADE80;margin-bottom:8px;">✓ 관리자 인증 완료</div>',
                    unsafe_allow_html=True,
                )
                if st.button("로그아웃", key="fin_admin_logout", use_container_width=True):
                    st.session_state["fin_role"] = "user"
                    logger.info("원무 대시보드 관리자 로그아웃")
                    st.rerun()
            else:
                _pw = st.text_input(
                    "패스워드", type="password",
                    key="fin_admin_pw",
                    placeholder="관리자 패스워드 입력",
                    label_visibility="collapsed",
                )
                if _pw:
                    try:
                        if settings.check_admin(_pw):
                            st.session_state["fin_role"] = "admin"
                            logger.info("원무 대시보드 관리자 인증 성공")
                            st.rerun()
                        else:
                            st.markdown(
                                '<div style="font-size:11px;color:#EF4444;'
                                'font-weight:600;margin-top:4px;">'
                                '패스워드가 올바르지 않습니다</div>',
                                unsafe_allow_html=True,
                            )
                    except Exception as _e:
                        st.error(f"인증 오류: {_e}")

        # 버전
        st.markdown(
            '<div style="font-size:10px;color:rgba(255,255,255,0.25);'
            'text-align:center;padding-top:12px;">'
            "원무 대시보드 v1.0<br>좋은문화병원 통계과"
            "</div>",
            unsafe_allow_html=True,
        )

    return st.session_state.get("fin_role", "user")


# ══════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════
def main() -> None:
    logger.info("finance_app v1.0 시작 — 원무 대시보드 (포트 8503)")

    current_role = _render_sidebar()

    if "fin_last_ts" not in st.session_state:
        st.session_state["fin_last_ts"] = time.strftime("%Y-%m-%d %H:%M")

    # 일반 유저: 원무 대시보드
    # 관리자:   원무 대시보드 + 📊 모니터링 탭
    if current_role == "admin":
        tab_fin, tab_mon = st.tabs(["💼 원무 현황", "📊 모니터링"])
        with tab_fin:
            try:
                render_finance_dashboard()
            except Exception as e:
                st.error(f"원무 대시보드 로드 오류\n\n{e}")
                logger.error(f"finance_app 렌더 오류: {e}", exc_info=True)
        with tab_mon:
            try:
                from ui.dashboard_log_viewer import render_dashboard_monitor
                render_dashboard_monitor()
            except ImportError:
                st.error("`ui/dashboard_log_viewer.py` 를 확인하세요.")
            except Exception as _me:
                st.error(f"모니터링 뷰어 오류: {_me}")
    else:
        try:
            render_finance_dashboard()
        except Exception as e:
            st.error(f"원무 대시보드 로드 오류\n\n{e}")
            logger.error(f"finance_app 렌더 오류: {e}", exc_info=True)


if __name__ == "__main__":
    main()