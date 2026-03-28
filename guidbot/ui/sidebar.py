"""
ui/sidebar.py  ─  사이드바 렌더링 v7.2
[v7.2] 회람 문서 버튼에 최근 업데이트 일자 + 1줄 요약 추가
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import streamlit as st

from config.settings import settings
from ui.theme import UITheme as T
from ui.components import section_label, status_indicator, info_grid
from utils.logger import get_logger
from utils.exceptions import GuidbotError

logger = get_logger(__name__, log_dir=settings.log_dir)

_SEARCH_MODES: list[dict] = [
    {"id": "fast", "label": "빠른 검색", "meta": "3건 · 빠른 응답"},
    {"id": "standard", "label": "표준 검색", "meta": "5건 · 균형 검색"},
    {"id": "deep", "label": "심층 검색", "meta": "10건 · 정밀 분석"},
    {"id": "separator", "label": "", "meta": ""},
    {"id": "data_analysis", "label": "데이터 분석", "meta": "Oracle DB · 차트"},
]

_DASH_TABS: dict = {}
_DEFAULT_SEARCH_MODE = "standard"


@dataclass
class DBHealth:
    is_healthy: bool
    message: str
    doc_count: int
    file_count: int = 0
    recent_files: List[Tuple[str, str]] = field(default_factory=list)


_SIDEBAR_BTN_CSS = """
<style>
/* secondary (기본) */
[data-testid="stSidebar"] div[data-testid="stButton"] > button,
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
    width: 100% !important;
    text-align: left !important;
    padding: 0.42rem 0.75rem !important;
    border-radius: 7px !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    line-height: 1.35 !important;
    box-shadow: none !important;
    transition: background 140ms ease, border-color 140ms ease, color 140ms ease !important;
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    color: rgba(255,255,255,0.80) !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:hover,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
    background: rgba(255,255,255,0.12) !important;
    border-color: rgba(255,255,255,0.28) !important;
    color: rgba(255,255,255,0.96) !important;
}
/* primary (선택/강조) */
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    width: 100% !important;
    text-align: left !important;
    padding: 0.42rem 0.75rem !important;
    border-radius: 7px !important;
    font-size: 12px !important;
    font-weight: 700 !important;
    line-height: 1.35 !important;
    box-shadow: none !important;
    transition: background 140ms ease !important;
    background: rgba(37,99,235,0.28) !important;
    border: 1.5px solid rgba(37,99,235,0.60) !important;
    color: rgba(255,255,255,0.97) !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:hover,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
    background: rgba(37,99,235,0.40) !important;
}
/* 포커스/활성 */
[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus,
[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus-visible,
[data-testid="stSidebar"] div[data-testid="stButton"] > button:active,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:focus,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:focus-visible,
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:active,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:focus,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:focus-visible,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:active {
    outline: none !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:active {
    background: rgba(255,255,255,0.12) !important;
    color: rgba(255,255,255,0.96) !important;
}
[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:active {
    background: rgba(37,99,235,0.40) !important;
    color: rgba(255,255,255,0.97) !important;
}
/* 버튼 내부 텍스트 */
[data-testid="stSidebar"] div[data-testid="stButton"] > button p,
[data-testid="stSidebar"] div[data-testid="stButton"] > button span,
[data-testid="stSidebar"] div[data-testid="stButton"] > button div {
    color: inherit !important;
    background: transparent !important;
    font-size: inherit !important;
}
/* 버튼 간격 */
[data-testid="stSidebar"] div[data-testid="stButton"] {
    margin-bottom: 0.22rem !important;
}
[data-testid="stSidebar"] .sb-btn-wrap {
    margin-top: 0.1rem;
}
</style>
"""


def _init_session_state() -> None:
    if "search_mode" not in st.session_state:
        st.session_state["search_mode"] = _DEFAULT_SEARCH_MODE
    if "active_page" not in st.session_state:
        st.session_state["active_page"] = "main"
    if "role" not in st.session_state:
        st.session_state["role"] = "user"


def _render_logo_header() -> None:
    st.markdown(
        f"""
        <div style="padding:0.6rem 0.5rem 0.45rem;display:flex;align-items:center;gap:0.65rem;">
            <div style="width:28px;height:28px;position:relative;flex-shrink:0;">
                <div style="width:100%;height:100%;border-radius:8px;
                    background:linear-gradient(135deg,{T.P600} 0%,{T.P800} 100%);
                    position:absolute;inset:0;box-shadow:0 2px 8px rgba(0,40,100,0.4);"></div>
                <div style="position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);width:14px;height:3.5px;
                    background:rgba(255,255,255,0.95);border-radius:1.5px;"></div>
                <div style="position:absolute;top:50%;left:50%;
                    transform:translate(-50%,-50%);width:3.5px;height:14px;
                    background:rgba(255,255,255,0.95);border-radius:1.5px;"></div>
            </div>
            <div>
                <div style="font-size:0.82rem;font-weight:700;color:rgba(255,255,255,0.95);
                    letter-spacing:-0.015em;line-height:1.2;">좋은문화병원</div>
                <div style="font-size:0.5rem;color:{T.A400};
                    letter-spacing:0.1em;text-transform:uppercase;margin-top:0.05rem;">AI Guide Bot</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_search_mode_selector() -> None:
    current: str = st.session_state.get("search_mode", _DEFAULT_SEARCH_MODE)
    section_label("검색 모드", "")
    st.markdown('<div class="sb-btn-wrap">', unsafe_allow_html=True)

    _cur_role = st.session_state.get("role", "user")

    for mode in _SEARCH_MODES:
        mode_id = mode["id"]
        if mode_id in ("separator", "separator2"):
            st.markdown(
                '<hr style="margin:0.25rem 0 0.3rem;border:none;'
                'border-top:1px solid rgba(255,255,255,0.10);">',
                unsafe_allow_html=True,
            )
            continue
        if mode.get("admin_only") and _cur_role != "admin":
            continue
        selected = current == mode_id
        btn_type = "primary" if selected else "secondary"
        arrow = "▸ " if selected else "   "
        meta = mode.get("meta", "")
        label = (
            f"{arrow}{mode['label']}  ·  {meta}" if meta else f"{arrow}{mode['label']}"
        )
        if st.button(
            label, key=f"smode_{mode_id}", type=btn_type, use_container_width=True
        ):
            if mode_id in _DASH_TABS:
                st.session_state["search_mode"] = mode_id
                st.session_state["active_page"] = "hospital_dashboard"
                st.session_state["dashboard_tab"] = _DASH_TABS[mode_id]
                st.rerun()
            elif st.session_state["search_mode"] != mode_id:
                st.session_state["search_mode"] = mode_id
                st.session_state["active_page"] = "main"
                logger.info(f"검색 모드 변경: {mode_id}")
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def _render_shortcuts() -> None:
    """
    바로가기 — v7.4
    · 회람 문서: 전폭 버튼 + 관리자 업데이트 노트 직접 입력
    · 진료/원무/간호: 이모지 없이 3열 인라인 그리드 (준비중)
    · 모든 스타일 인라인 — CSS 클래스 미사용 (사이드바 적용 보장)
    """
    section_label("바로가기", "")

    _role     = st.session_state.get("role", "user")
    _is_admin = _role == "admin"

    # ── 업데이트 노트 세션 기본값 ─────────────────────────────────────
    if "shortcut_note" not in st.session_state:
        st.session_state["shortcut_note"] = "2026-03-28  산부인과 지침 추가"
    _note = st.session_state["shortcut_note"]

    # ── 공통 인라인 스타일 상수 ────────────────────────────────────────
    _S_TITLE = (
        "font-size:13px;font-weight:700;color:#FFFFFF;"
        "letter-spacing:-0.01em;flex:1;"
    )
    _S_ARR   = "font-size:12px;color:#7BE0F5;font-weight:700;"
    _S_NOTE  = (
        "font-size:11px;color:#FFFFFF !important;margin-top:4px;"
        "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
        "font-weight:600;"
    )

    # ── 회람 문서 버튼 ────────────────────────────────────────────────
    _DOCS_URL = (
        "https://docs.google.com/document/d/"
        "1WW05jXoSw65WY2vZYkqTxPSBWrv9anvSknWDGWZlj_k/edit"
    )
    st.markdown(
        f'<a href="{_DOCS_URL}" target="_blank" rel="noopener"'
        f' style="display:block;background:rgba(0,140,180,0.22);'
        f'border:1.5px solid rgba(0,190,220,0.40);border-radius:8px;'
        f'padding:0.48rem 0.7rem;text-decoration:none;margin-bottom:5px;">'
        f'<div style="display:flex;align-items:center;">'
        f'<span style="{_S_TITLE}">회람 문서</span>'
        f'<span style="{_S_ARR}">↗</span>'
        f'</div>'
        f'<div style="{_S_NOTE}">🕒 {_note}</div>'
        f'</a>',
        unsafe_allow_html=True,
    )

    # ── 노트 인라인 편집 — 수정 버튼 토글 (누구나 사용 가능) ────────
    _edit_key = "shortcut_note_editing"
    if _edit_key not in st.session_state:
        st.session_state[_edit_key] = False

    # 수정 버튼 / 취소 버튼 — 노트 한 줄 아래 소형 버튼
    _btn_label = "✏️ 노트 수정" if not st.session_state[_edit_key] else "✕ 닫기"
    if st.button(_btn_label, key="shortcut_note_toggle", use_container_width=True):
        st.session_state[_edit_key] = not st.session_state[_edit_key]
        st.rerun()

    if st.session_state[_edit_key]:
        # 입력창 글자 잘 보이도록 CSS — 흰 배경 + 검은 글자
        st.markdown(
            """<style>
            /* 사이드바 입력창: 흰 배경 + 검은 글자 강제 */
            [data-testid="stSidebar"] input[type="text"] {
                color: #1E293B !important;
                background: #FFFFFF !important;
                -webkit-text-fill-color: #1E293B !important;
                border: 2px solid #38BDF8 !important;
                border-radius: 6px !important;
                font-size: 12px !important;
                font-weight: 600 !important;
                caret-color: #0284C7 !important;
            }
            [data-testid="stSidebar"] input[type="text"]:focus {
                color: #1E293B !important;
                background: #FFFFFF !important;
                -webkit-text-fill-color: #1E293B !important;
            }
            [data-testid="stSidebar"] input[type="text"]::placeholder {
                color: #94A3B8 !important;
                -webkit-text-fill-color: #94A3B8 !important;
                opacity: 1 !important;
            }
            </style>""",
            unsafe_allow_html=True,
        )
        _new_note = st.text_input(
            "업데이트 노트",
            value=_note,
            key="shortcut_note_input",
            placeholder="예) 2026-03-28  산부인과 지침 추가",
            label_visibility="collapsed",
        )
        if st.button("💾 저장", key="shortcut_note_save",
                     use_container_width=True, type="primary"):
            st.session_state["shortcut_note"] = _new_note
            st.session_state[_edit_key] = False
            st.rerun()

    # ── 진료 / 원무 / 간호 — 3열 그리드 (준비중) ──────────────────────
    # 준비중 항목: 이모지 없이 텍스트만, 밝은 색상으로 가시성 확보
    _CELL_S = (
        "flex:1;border:1px solid rgba(255,255,255,0.15);"
        "border-radius:7px;padding:7px 4px;text-align:center;"
        "background:rgba(255,255,255,0.05);"
    )
    _LBL_S  = "display:block;font-size:12px;font-weight:700;color:#D1E8F5;"
    _SUB_S  = "display:block;font-size:9px;color:#7BAEC4;margin-top:2px;"

    _cells = "".join([
        f'<div style="{_CELL_S}">'
        f'<span style="{_LBL_S}">{lbl}</span>'
        f'<span style="{_SUB_S}">준비중</span>'
        f'</div>'
        for lbl in ("진료", "원무", "간호")
    ])
    st.markdown(
        f'<div style="display:flex;gap:4px;margin-top:5px;">{_cells}</div>',
        unsafe_allow_html=True,
    )


def _render_system_status(db_health: DBHealth) -> None:
    """시스템 상태 — v7.2"""
    section_label("시스템 상태", "")

    _role     = st.session_state.get("role", "user")
    _is_admin = _role == "admin"

    oracle_enabled: bool = getattr(settings, "oracle_enabled", False)
    if "oracle_status" not in st.session_state:
        st.session_state["oracle_status"] = None

    _oc_ok, _oc_msg = True, "비활성"
    if oracle_enabled:
        if st.session_state["oracle_status"] is None:
            try:
                from db.oracle_client import test_connection
                _ok, _msg = test_connection()
                st.session_state["oracle_status"] = (_ok, _msg)
            except Exception as _exc:
                st.session_state["oracle_status"] = (False, f"모듈 오류: {_exc}")
        _oc_ok, _oc_msg = st.session_state["oracle_status"]

    status_indicator(db_health.is_healthy, db_health.message)
    if oracle_enabled:
        status_indicator(_oc_ok, f"Oracle · {_oc_msg}")

    if _is_admin:
        try:
            from utils.startup_optimizer import is_warmup_ready
            _bm25_ready = "준비 완료" if is_warmup_ready() else "워밍업 중..."
        except Exception:
            _bm25_ready = "-"

        info_grid(
            [
                ("청크 (벡터)", f"{db_health.doc_count:,} 개"),
                ("원본 PDF",    f"{db_health.file_count:,} 개"),
                ("AI 엔진",    "Gemini"),
                ("검색 엔진",  f"RAG+BM25({_bm25_ready})"),
            ]
        )

        if oracle_enabled:
            with st.expander("⚙️ Oracle 설정", expanded=False):
                st.markdown(
                    '<div style="font-size:10px;color:rgba(255,255,255,0.32);'
                    'margin-bottom:0.35rem;">연결 관리 — 신중하게 사용하세요</div>',
                    unsafe_allow_html=True,
                )
                if st.button("재연결", key="btn_oracle_reconnect",
                             use_container_width=True, help="Oracle 연결 풀 초기화 후 재연결"):
                    st.session_state["oracle_status"] = None
                    try:
                        from db.oracle_client import close_pool
                        close_pool()
                    except Exception:
                        pass
                    st.rerun()

                if st.button("상태 확인", key="btn_oracle_check",
                             use_container_width=True, help="연결 상태 즉시 재확인"):
                    st.session_state["oracle_status"] = None
                    st.rerun()

                if st.button("스키마 캐시 초기화", key="btn_schema_cache_clear",
                             use_container_width=True,
                             help="COLUMN_DESCS 수정 후 — 다음 SQL 생성 시 DB에서 재로드"):
                    try:
                        from db.oracle_access_config import get_access_config_manager
                        get_access_config_manager().invalidate_cache()
                        st.success("캐시 초기화 완료")
                    except Exception as _e:
                        st.warning(f"캐시 초기화 실패: {_e}")

                if not _oc_ok:
                    st.markdown(
                        '<div style="font-size:11px;color:rgba(255,255,255,0.70);'
                        'line-height:1.9;margin-top:0.3rem;">'
                        "<b>체크리스트</b><br>"
                        "① .env → ORACLE_HOST / PORT / SERVICE_NAME<br>"
                        "② Oracle 리스너: <code>lsnrctl status</code><br>"
                        "③ 방화벽 1521 포트<br>"
                        "④ <code>pip install oracledb</code></div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown(
                '<div style="font-size:11px;color:rgba(255,255,255,0.28);margin-top:0.2rem;">'
                "Oracle 비활성 — .env ORACLE_ENABLED=true</div>",
                unsafe_allow_html=True,
            )


def _render_recent_files(recent_files: List[Tuple[str, str]]) -> None:
    if not recent_files:
        return
    section_label("최근 업로드", "")
    for fname, fdate in recent_files:
        short = (fname[:20] + "…") if len(fname) > 22 else fname
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:0.18rem 0;border-bottom:1px solid rgba(255,255,255,0.05);">'
            f'<span style="font-size:11px;color:rgba(255,255,255,0.62);overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">{short}</span>'
            f'<span style="font-size:10px;color:rgba(255,255,255,0.28);'
            f'flex-shrink:0;margin-left:4px;">{fdate}</span></div>',
            unsafe_allow_html=True,
        )


def _render_monitoring_panel() -> None:
    if not settings.monitoring_enabled:
        return
    stats = {
        "query_count": 0,
        "error_rate": 0.0,
        "avg_search_ms": 0,
        "avg_stream_ms": 0,
        "last_queries": [],
    }
    try:
        from utils.monitor import get_metrics as _gm
        stats.update(_gm().get_stats())
    except Exception as exc:
        logger.warning(f"모니터링 stats 로드 실패: {exc}")

    section_label("사용 통계", "")
    raw_error = stats.get("error_rate", 0.0)
    error_pct = raw_error if raw_error > 1.0 else raw_error * 100
    info_grid(
        [
            ("총 질문",   f"{stats.get('query_count', 0):,}회"),
            ("평균 검색", f"{stats.get('avg_search_ms', 0):.0f}ms"),
            ("평균 응답", f"{stats.get('avg_stream_ms', 0):.0f}ms"),
            ("오류율",    f"{error_pct:.1f}%"),
        ]
    )
    last_queries: list = stats.get("last_queries", [])
    if last_queries:
        st.markdown(
            '<div style="margin-top:0.6rem;font-size:10px;color:rgba(255,255,255,0.28);'
            "font-weight:600;text-transform:uppercase;letter-spacing:.08em;"
            'margin-bottom:0.28rem;">최근 질문</div>',
            unsafe_allow_html=True,
        )
        for q in last_queries[:3]:
            short_q = (q[:25] + "…") if len(q) > 25 else q
            st.markdown(
                f'<div style="font-size:11px;color:rgba(255,255,255,0.52);'
                f"padding:0.18rem 0;border-bottom:1px solid rgba(255,255,255,0.05);"
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f"{short_q}</div>",
                unsafe_allow_html=True,
            )


def _handle_admin_upload(uploaded_files) -> None:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.document_loaders import PyPDFLoader
    from utils.text_cleaner import process as preprocess
    from core.vector_store import VectorStoreManager

    all_docs = []
    prog = st.progress(0, text="파일 분석 중...")
    for idx, uf in enumerate(uploaded_files):
        save_path = settings.local_work_dir / uf.name
        save_path.write_bytes(uf.getbuffer())
        try:
            for page in PyPDFLoader(str(save_path)).load():
                result = preprocess(page.page_content, settings.min_text_length)
                if result:
                    page.page_content = result.content
                    page.metadata["source"] = uf.name
                    all_docs.append(page)
        except Exception as exc:
            st.error(f"❌ {uf.name} 처리 실패: {exc}")
            logger.error(f"PDF 처리 실패 [{uf.name}]: {exc}", exc_info=True)
        prog.progress((idx + 1) / len(uploaded_files))
    prog.empty()
    if not all_docs:
        st.warning("유효한 텍스트를 추출할 수 없습니다.")
        return
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    ).split_documents(all_docs)
    with st.spinner("DB 업데이트 중..."):
        try:
            manager = VectorStoreManager(
                db_path=settings.rag_db_path,
                model_name=settings.embedding_model,
                cache_dir=str(settings.local_work_dir),
            )
            if manager.append(chunks):
                st.cache_resource.clear()
                st.success(f"✅ {len(uploaded_files)}개 파일 추가 완료!")
                st.rerun()
            else:
                st.error("DB 업데이트에 실패했습니다.")
        except GuidbotError as exc:
            st.error(exc.message)


def _render_admin_panel() -> None:
    current_role: str = st.session_state.get("role", "user")
    _admin_expanded = current_role == "admin"

    with st.expander("관리자 전용", expanded=_admin_expanded):
        if current_role == "admin":
            st.markdown(
                f'<div style="background:rgba(74,222,128,0.10);'
                f"border:1px solid rgba(74,222,128,0.25);border-radius:6px;"
                f"padding:0.35rem 0.6rem;margin-bottom:0.5rem;font-size:11px;"
                f'color:{T.SUCCESS_SIDEBAR} !important;font-weight:600;">관리자 인증 완료</div>',
                unsafe_allow_html=True,
            )
            if st.button("로그아웃", use_container_width=True, key="admin_logout"):
                st.session_state["role"] = "user"
                logger.info("관리자 로그아웃")
                st.rerun()

            st.markdown(
                '<hr style="margin:0.45rem 0;border:none;'
                'border-top:1px solid rgba(255,255,255,0.08);">',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="font-size:10px;color:rgba(255,255,255,0.28);font-weight:600;'
                'letter-spacing:0.07em;text-transform:uppercase;margin-bottom:0.25rem;">'
                "전산팀 도구</div>",
                unsafe_allow_html=True,
            )

            if st.button("SQL 대시보드", key="admin_goto_sql", use_container_width=True,
                         help="직접 SQL 입력/실행 + 자유 시각화 + AI 분석"):
                st.session_state["active_page"] = "sql_dashboard"
                st.rerun()
            if st.button("문서 관리", key="admin_goto_docs", use_container_width=True,
                         help="쿼리 예제 · 테이블 명세 등록 / 관리"):
                st.session_state["active_page"] = "doc_manager"
                st.rerun()

            st.markdown(
                '<hr style="margin:0.45rem 0;border:none;'
                'border-top:1px solid rgba(255,255,255,0.08);">',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="font-size:10px;color:rgba(255,255,255,0.28);font-weight:600;'
                'letter-spacing:0.07em;text-transform:uppercase;margin-bottom:0.25rem;">'
                "PDF 추가</div>",
                unsafe_allow_html=True,
            )
            new_files = st.file_uploader(
                "PDF 파일 선택",
                accept_multiple_files=True,
                type=["pdf"],
                key="admin_upload",
                label_visibility="collapsed",
            )
            if st.button("DB 업데이트", use_container_width=True, type="primary",
                         key="admin_db_update"):
                if new_files:
                    _handle_admin_upload(new_files)
                else:
                    st.warning("업로드할 PDF를 먼저 선택해주세요.")
        else:
            pw = st.text_input(
                "패스워드", type="password", key="admin_pw",
                placeholder="관리자 패스워드 입력",
                label_visibility="collapsed",
            )
            if pw:
                if settings.check_admin(pw):
                    st.session_state["role"] = "admin"
                    logger.info("관리자 인증 성공")
                    st.rerun()
                else:
                    st.markdown(
                        f'<div style="font-size:11px;color:{T.ERROR_SIDEBAR} !important;'
                        f'font-weight:600;margin-top:0.28rem;">패스워드가 올바르지 않습니다</div>',
                        unsafe_allow_html=True,
                    )
                    logger.warning("관리자 인증 실패")


def render_sidebar(db_health: DBHealth) -> str:
    """사이드바 전체 렌더링 → 현재 역할 반환."""
    _init_session_state()

    try:
        with st.sidebar:
            st.markdown(_SIDEBAR_BTN_CSS, unsafe_allow_html=True)
            _render_logo_header()
            st.divider()
            _render_search_mode_selector()
            st.divider()
            _render_shortcuts()
            st.divider()
            _render_system_status(db_health)
            st.divider()
            if db_health.recent_files:
                _render_recent_files(db_health.recent_files)
                st.divider()
            _render_monitoring_panel()
            st.divider()
            _render_admin_panel()

    except Exception as exc:
        logger.error(f"사이드바 렌더링 오류: {exc}", exc_info=True)
        try:
            with st.sidebar:
                st.error(f"⚠️ 사이드바 오류: {type(exc).__name__}")
        except Exception:
            pass

    return st.session_state.get("role", "user")