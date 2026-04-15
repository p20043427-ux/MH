"""
vector_db_admin.py — RAG CMS 관리자 앱 v2.2  -2026-04-15 업데이트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.2 변경사항]
  ✅ FAISS → CMS 동기화 기능 추가 (기존 216개 소스 자동 등록)
  ✅ 수동 백업 생성 버튼 추가 + 백업 생성 시점 안내
  ✅ 디자인 완전 통일 (폰트 14px 기준, 일관된 컴포넌트 스타일)
  ✅ 문서 목록: CMS 미등록 시 FAISS 동기화 안내 배너 표시

[실행]
    streamlit run vector_db_admin.py --server.port 8505
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent 
sys.path.insert(0, str(_ROOT))

import streamlit as st

st.set_page_config(
    page_title="RAG CMS | 좋은문화병원",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 서비스 임포트 ──────────────────────────────────────────────────────
try:
    from services.cms_service import (
        CMSService, CMSStats, DocumentMeta, ChunkRecord,
        AuditLog, get_cms_service, pdf_to_markdown, markdown_to_chunks,
    )
    _CMS_OK = True
    _CMS_ERR = ""
except ImportError as _ie:
    _CMS_OK = False
    _CMS_ERR = str(_ie)

try:
    from services.vector_admin_service import (
        VectorAdminService, VectorDBStats, SourceFileInfo,
        get_admin_service, is_building,
    )
    _VEC_OK = True
except ImportError:
    _VEC_OK = False
    def is_building(): return False  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════
#  디자인 시스템
#  ▸ 기준 폰트: 14px (body) / 12px (caption) / 16px (section)
#  ▸ 색상: Primary #2563EB / Text #111827 / Muted #6B7280
#  ▸ 카드: 흰 배경 + #E5E7EB 테두리 + 8px 반경 + 가벼운 그림자
# ══════════════════════════════════════════════════════════════════════

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700&display=swap');

/* ── 디자인 토큰 ── */
:root {
    --f:      "Noto Sans KR", "Apple SD Gothic Neo", -apple-system, sans-serif;
    --fs-xs:  11px;
    --fs-sm:  12px;
    --fs-md:  14px;
    --fs-lg:  15px;
    --fs-xl:  17px;
    --fs-h:   22px;

    --c-bg:   #F3F4F6;
    --c-card: #FFFFFF;
    --c-bd:   #E5E7EB;
    --c-bd2:  #D1D5DB;

    --c-t1:   #111827;
    --c-t2:   #374151;
    --c-t3:   #6B7280;
    --c-t4:   #9CA3AF;

    --c-p:    #2563EB;
    --c-p2:   #1D4ED8;
    --c-pl:   #EFF6FF;
    --c-ok:   #16A34A;   --c-okb: #F0FDF4;
    --c-warn: #D97706;   --c-wb:  #FFFBEB;
    --c-err:  #DC2626;   --c-eb:  #FEF2F2;
    --c-info: #0369A1;   --c-ib:  #F0F9FF;
    --c-grn:  #059669;   --c-gb:  #ECFDF5;

    --c-sb:   #111827;  /* 사이드바 */

    --sh: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
    --sh2: 0 4px 12px rgba(0,0,0,.10), 0 2px 4px rgba(0,0,0,.06);
    --r:  8px;
}

/* ── 전역 ── */
html, body, .stApp {
    font-family: var(--f) !important;
    font-size: var(--fs-md) !important;
    background: var(--c-bg) !important;
    color: var(--c-t1) !important;
    -webkit-font-smoothing: antialiased;
}
.main .block-container {
    background: var(--c-bg) !important;
    padding: 1.2rem 1.8rem 3rem !important;
    max-width: 1400px !important;
}

/* ── 헤더 배너 ── */
.cms-hdr {
    background: linear-gradient(135deg, #0F2A6B 0%, #1E3A8A 40%, #2563EB 100%);
    border-radius: 12px;
    padding: 14px 24px;
    margin-bottom: 16px;
    display: flex; align-items: center; gap: 14px;
    box-shadow: 0 4px 16px rgba(37,99,235,.30);
}
.cms-hdr h1 {
    color: #fff !important;
    font-size: var(--fs-xl) !important;
    font-weight: 700 !important;
    margin: 0 0 2px !important;
    letter-spacing: -.02em;
}
.cms-hdr p {
    color: rgba(255,255,255,.60) !important;
    font-size: var(--fs-sm) !important;
    margin: 0 !important;
}

/* ── KPI 카드 ── */
.kpi {
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 14px 16px 12px;
    box-shadow: var(--sh);
    position: relative; overflow: hidden;
}
.kpi::before {
    content: ''; position: absolute;
    top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--c-p), #60A5FA);
}
.kpi.grn::before { background: linear-gradient(90deg, var(--c-grn), #34D399); }
.kpi .ico { font-size: 18px; margin-bottom: 6px; display: block; }
.kpi .val { font-size: 22px; font-weight: 700; color: var(--c-t1); line-height: 1.1; }
.kpi .lbl { font-size: var(--fs-xs); color: var(--c-t3); margin-top: 4px;
            font-weight: 600; text-transform: uppercase; letter-spacing: .4px; }
.kpi .sub { font-size: var(--fs-xs); color: var(--c-t4); margin-top: 1px; }

/* ── 배지 ── */
.bdg {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 20px;
    font-size: var(--fs-xs);
    font-weight: 600;
    line-height: 1.8;
}
.bdg-ok   { background: var(--c-okb);  color: var(--c-ok);   border: 1px solid #BBF7D0; }
.bdg-warn { background: var(--c-wb);   color: var(--c-warn); border: 1px solid #FDE68A; }
.bdg-err  { background: var(--c-eb);   color: var(--c-err);  border: 1px solid #FECACA; }
.bdg-info { background: var(--c-ib);   color: var(--c-info); border: 1px solid #BAE6FD; }
.bdg-gray { background: #F9FAFB;       color: var(--c-t3);   border: 1px solid var(--c-bd); }
.bdg-grn  { background: var(--c-gb);   color: var(--c-grn);  border: 1px solid #A7F3D0; }

/* ── 섹션 제목 ── */
.sec {
    font-size: var(--fs-lg);
    font-weight: 700;
    color: var(--c-t1);
    padding: 0 0 8px;
    border-bottom: 2px solid var(--c-bd);
    margin: 20px 0 14px;
    display: flex; align-items: center; gap: 6px;
}

/* ── 문서 카드 ── */
.doc {
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-left: 4px solid var(--c-p);
    border-radius: var(--r);
    padding: 11px 15px;
    margin-bottom: 6px;
    box-shadow: var(--sh);
}
.doc .dt {
    font-size: var(--fs-md);
    font-weight: 600;
    color: var(--c-t1);
    margin: 0 0 4px;
}
.doc .dm {
    font-size: var(--fs-sm);
    color: var(--c-t3);
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
}

/* ── 상세 패널 ── */
.det-panel {
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    border-radius: var(--r);
    padding: 14px 18px;
    box-shadow: var(--sh2);
    margin: 4px 0 12px;
}

/* ── 청크 박스 ── */
.ck-box {
    background: #F9FAFB;
    border: 1px solid var(--c-bd);
    border-radius: 6px;
    padding: 10px 14px;
    font-size: var(--fs-sm);
    line-height: 1.75;
    color: var(--c-t2);
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--f);
}

/* ── FAISS 정보 박스 ── */
.faiss-box {
    background: var(--c-gb);
    border: 1px solid #A7F3D0;
    border-radius: var(--r);
    padding: 12px 16px;
    font-size: var(--fs-sm);
    color: #064E3B;
}
.faiss-box code {
    background: rgba(5,150,105,.1) !important;
    color: #065F46 !important;
    border-color: #6EE7B7 !important;
}

/* ── 동기화 배너 ── */
.sync-banner {
    background: linear-gradient(135deg, #EFF6FF, #DBEAFE);
    border: 1px solid #BFDBFE;
    border-radius: var(--r);
    padding: 16px 20px;
    margin-bottom: 16px;
}
.sync-banner h3 { color: var(--c-p) !important; font-size: var(--fs-lg) !important;
                  font-weight: 700 !important; margin: 0 0 6px !important; }
.sync-banner p  { color: var(--c-t2) !important; font-size: var(--fs-sm) !important;
                  margin: 0 !important; }

/* ── 로그 행 ── */
.log-row {
    font-size: var(--fs-sm);
    padding: 5px 0;
    border-bottom: 1px solid #F3F4F6;
    color: var(--c-t2);
    line-height: 1.5;
}

/* ── 탭 ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 2px;
    background: var(--c-card);
    border: 1px solid var(--c-bd);
    padding: 3px 4px;
    border-radius: var(--r);
    box-shadow: var(--sh);
}
.stTabs [data-baseweb="tab"] {
    color: var(--c-t3) !important;
    font-weight: 500 !important;
    font-size: var(--fs-sm) !important;
    border-radius: 6px !important;
    padding: 6px 14px !important;
}
.stTabs [data-baseweb="tab"]:hover {
    color: var(--c-t1) !important;
    background: var(--c-bg) !important;
}
.stTabs [aria-selected="true"] {
    background: var(--c-p) !important;
    color: #fff !important;
    font-weight: 700 !important;
    box-shadow: 0 2px 6px rgba(37,99,235,.35) !important;
}

/* ── 사이드바 ── */
[data-testid="stSidebar"] { background: var(--c-sb) !important; }
[data-testid="stSidebar"] > div { background: var(--c-sb) !important; }
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div,
[data-testid="stSidebar"] label { color: rgba(255,255,255,.78) !important; font-size: var(--fs-sm) !important; }
[data-testid="stSidebar"] strong,
[data-testid="stSidebar"] b { color: #fff !important; }
[data-testid="stSidebar"] a { color: #93C5FD !important; text-decoration: none !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.12) !important; }
[data-testid="stSidebar"] button {
    background: rgba(255,255,255,.08) !important;
    border: 1px solid rgba(255,255,255,.16) !important;
    color: rgba(255,255,255,.88) !important;
    border-radius: 6px !important;
    font-size: var(--fs-sm) !important;
}
[data-testid="stSidebar"] button:hover { background: rgba(255,255,255,.15) !important; }
[data-testid="stSidebar"] table { width: 100%; border-collapse: collapse; }
[data-testid="stSidebar"] td {
    padding: 4px 6px;
    border-bottom: 1px solid rgba(255,255,255,.07);
    color: rgba(255,255,255,.70) !important;
    font-size: var(--fs-sm) !important;
}
[data-testid="stSidebar"] td:last-child { color: #fff !important; font-weight: 600; text-align: right; }

/* ── 입력 위젯 ── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
    background: var(--c-card) !important;
    color: var(--c-t1) !important;
    border: 1px solid var(--c-bd) !important;
    border-radius: 6px !important;
    font-size: var(--fs-md) !important;
    font-family: var(--f) !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder { color: var(--c-t4) !important; }
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {
    border-color: var(--c-p) !important;
    box-shadow: 0 0 0 2px rgba(37,99,235,.12) !important;
}

/* ── selectbox / multiselect ── */
[data-testid="stSelectbox"] > div > div {
    background: var(--c-card) !important;
    border: 1px solid var(--c-bd) !important;
    border-radius: 6px !important;
}
[data-testid="stSelectbox"] span { color: var(--c-t1) !important; font-size: var(--fs-md) !important; }

/* ── number input ── */
[data-testid="stNumberInput"] input {
    background: var(--c-card) !important;
    color: var(--c-t1) !important;
    border: 1px solid var(--c-bd) !important;
}

/* ── radio ── */
[data-testid="stRadio"] label,
[data-testid="stRadio"] label p { color: var(--c-t2) !important; font-size: var(--fs-md) !important; }

/* ── checkbox ── */
[data-testid="stCheckbox"] label p { color: var(--c-t2) !important; font-size: var(--fs-md) !important; }

/* ── expander ── */
[data-testid="stExpander"] {
    background: var(--c-card) !important;
    border: 1px solid var(--c-bd) !important;
    border-radius: var(--r) !important;
    box-shadow: var(--sh) !important;
    margin-bottom: 4px !important;
}
details summary p { color: var(--c-t2) !important; font-size: var(--fs-md) !important; font-weight: 500 !important; }
details summary:hover p { color: var(--c-p) !important; }
[data-testid="stExpander"] > div { background: #FAFAFA !important; border-radius: 0 0 var(--r) var(--r) !important; }

/* ── 버튼 ── */
.main [data-testid="stButton"] button[kind="primary"] {
    background: var(--c-p) !important; border: none !important;
    color: #fff !important; font-weight: 600 !important;
    border-radius: 6px !important; font-size: var(--fs-md) !important;
    box-shadow: 0 1px 4px rgba(37,99,235,.25) !important;
}
.main [data-testid="stButton"] button[kind="primary"]:hover { background: var(--c-p2) !important; }
.main [data-testid="stButton"] button[kind="secondary"] {
    background: var(--c-card) !important; border: 1px solid var(--c-bd) !important;
    color: var(--c-t2) !important; border-radius: 6px !important; font-size: var(--fs-md) !important;
}
.main [data-testid="stButton"] button[kind="secondary"]:hover {
    border-color: var(--c-p) !important; color: var(--c-p) !important;
}

/* ── 업로드 ── */
[data-testid="stFileUploaderDropzone"] {
    background: var(--c-pl) !important;
    border: 2px dashed #93C5FD !important;
    border-radius: var(--r) !important;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--c-p) !important; }

/* ── 프로그레스 ── */
.stProgress > div > div {
    background: linear-gradient(90deg, var(--c-p), #60A5FA) !important;
    border-radius: 4px !important;
}

/* ── 데이터프레임 ── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--c-bd) !important;
    border-radius: var(--r) !important;
    box-shadow: var(--sh) !important;
}

/* ── 알림 ── */
[data-testid="stAlert"] { border-radius: var(--r) !important; font-size: var(--fs-md) !important; }

/* ── 일반 텍스트 ── */
h1, h2, h3 { color: var(--c-t1) !important; font-family: var(--f) !important; }
[data-testid="stMarkdownContainer"] p { color: var(--c-t2) !important; font-size: var(--fs-md) !important; }
[data-testid="stCaptionContainer"] p  { color: var(--c-t3) !important; font-size: var(--fs-sm) !important; }
code {
    background: var(--c-pl) !important;
    color: #1E40AF !important;
    border: 1px solid #BFDBFE !important;
    border-radius: 4px !important;
    padding: 1px 5px !important;
    font-size: .85em !important;
}
hr { border-color: var(--c-bd) !important; margin: 12px 0 !important; }

/* ── metric ── */
[data-testid="stMetric"] label { font-size: var(--fs-sm) !important; color: var(--c-t3) !important; }
[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 700 !important; color: var(--c-t1) !important; }
</style>
"""


def _apply_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
#  캐시
# ══════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def _get_cms() -> "CMSService":
    return get_cms_service()


@st.cache_resource(show_spinner=False)
def _get_vec() -> Optional["VectorAdminService"]:
    if not _VEC_OK:
        return None
    try:
        return get_admin_service()
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def _cms_stats(_cms) -> "CMSStats":
    return _cms.get_stats()


@st.cache_data(ttl=60, show_spinner=False)
def _vec_stats(_vec) -> Optional["VectorDBStats"]:
    if _vec is None:
        return None
    try:
        return _vec.get_stats()
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def _docs(_cms, status: str, dept: str, search: str, page: int, ps: int):
    return _cms.list_documents(status_filter=status, department_filter=dept,
                               search=search, page=page, page_size=ps)


@st.cache_data(ttl=60, show_spinner=False)
def _sources(_vec) -> list:
    if _vec is None:
        return []
    try:
        return _vec.list_sources()
    except Exception:
        return []


def _cc() -> None:
    """캐시 전체 초기화."""
    st.cache_data.clear()


# ══════════════════════════════════════════════════════════════════════
#  헬퍼
# ══════════════════════════════════════════════════════════════════════

def _b(status: str) -> str:
    """상태 배지 HTML."""
    _map = {
        "active":     ("bdg-ok",   "✅ 활성"),
        "inactive":   ("bdg-warn", "⏸ 비활성"),
        "deprecated": ("bdg-err",  "🗑 사용중단"),
        "pending":    ("bdg-gray", "⏳ 대기"),
        "indexed":    ("bdg-grn",  "✅ 인덱싱됨"),
        "failed":     ("bdg-err",  "❌ 실패"),
    }
    cls, lbl = _map.get(status, ("bdg-gray", status))
    return f'<span class="bdg {cls}">{lbl}</span>'


def _kpi(ico: str, val: str, lbl: str, sub: str = "", grn: bool = False) -> str:
    extra = "grn" if grn else ""
    return (
        f'<div class="kpi {extra}"><span class="ico">{ico}</span>'
        f'<div class="val">{val}</div><div class="lbl">{lbl}</div>'
        + (f'<div class="sub">{sub}</div>' if sub else "") + "</div>"
    )


def _sec(ico: str, title: str) -> str:
    return f'<div class="sec">{ico}&nbsp;{title}</div>'


def _fmt(n: int) -> str:
    return f"{n:,}"


def _sim_c(p: float) -> str:
    return "#16A34A" if p >= 70 else "#D97706" if p >= 40 else "#DC2626"


# ══════════════════════════════════════════════════════════════════════
#  탭 1: 대시보드
# ══════════════════════════════════════════════════════════════════════

def _tab_dashboard(cms: "CMSService", vec) -> None:
    cs = _cms_stats(cms)
    vs = _vec_stats(vec)

    # ── CMS 현황 ─────────────────────────────────────────────────
    st.markdown(_sec("📋", "CMS 문서 현황"), unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_kpi("📄", _fmt(cs.total_documents), "총 문서",       f"활성 {cs.active_documents}"),  unsafe_allow_html=True)
    c2.markdown(_kpi("📦", _fmt(cs.total_chunks),    "총 청크"),                                        unsafe_allow_html=True)
    c3.markdown(_kpi("✅", _fmt(cs.indexed_chunks),  "인덱싱 완료",   f"대기 {cs.pending_chunks}"),     unsafe_allow_html=True)
    c4.markdown(_kpi("🔄", _fmt(cs.total_versions),  "버전 수"),                                        unsafe_allow_html=True)
    c5.markdown(_kpi("💾", f"{cs.db_size_mb} MB",   "CMS DB 크기"),                                    unsafe_allow_html=True)

    # CMS 비어있고 FAISS 데이터 있으면 동기화 안내
    if cs.total_documents == 0 and vs and vs.is_loaded and vs.total_sources > 0:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="sync-banner">'
            f'<h3>🔄 FAISS → CMS 동기화 필요</h3>'
            f'<p>FAISS DB에 <b>{vs.total_sources}개</b> 소스 파일, '
            f'<b>{_fmt(vs.total_vectors)}개</b> 벡터가 있지만 CMS에 등록된 문서가 없습니다.<br>'
            f'아래 버튼을 클릭하면 기존 FAISS 소스를 CMS에 자동으로 등록합니다.</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button("🔄 FAISS → CMS 전체 동기화", type="primary", key="dash_sync"):
            srcs = _sources(vec)
            with st.spinner(f"{len(srcs)}개 소스 동기화 중..."):
                r = cms.sync_from_faiss(srcs)
            if r["success"]:
                st.success(r["message"]); _cc(); st.rerun()
            else:
                st.error(r["message"])

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FAISS 벡터 DB 현황 ────────────────────────────────────────
    st.markdown(_sec("🗄️", "FAISS 벡터 DB 현황"), unsafe_allow_html=True)

    if vs is None or not vs.is_loaded:
        st.warning(
            "⚠️ FAISS DB 로드 실패.\n\n"
            "`python build_db.py`를 실행하거나 [🔧 관리 도구] 탭에서 재구축하세요."
        )
    else:
        v1, v2, v3, v4 = st.columns(4)
        v1.markdown(_kpi("🔢", _fmt(vs.total_vectors),  "총 벡터",      grn=True), unsafe_allow_html=True)
        v2.markdown(_kpi("📂", str(vs.total_sources),   "소스 파일 수", grn=True), unsafe_allow_html=True)
        v3.markdown(_kpi("💽", f"{vs.db_size_mb} MB",  "DB 크기",      grn=True), unsafe_allow_html=True)
        v4.markdown(_kpi("🗃️", str(vs.backup_count),   "보관 백업",    grn=True), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="faiss-box">'
            f'<b>경로</b> : <code>{vs.db_path}</code> &nbsp;&nbsp; '
            f'<b>인덱스</b> : <code>{vs.index_type}</code> &nbsp;&nbsp; '
            f'<b>최근 수정</b> : <code>{vs.last_modified}</code>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if vs.source_summary:
            import pandas as pd
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(_sec("📊", "소스 파일별 청크 분포 (상위 15)"), unsafe_allow_html=True)
            srt = sorted(vs.source_summary.items(), key=lambda x: x[1], reverse=True)[:15]
            df_s = pd.DataFrame(srt, columns=["파일명", "청크 수"])
            st.bar_chart(df_s.set_index("파일명"), color="#059669", height=200)
            if len(vs.source_summary) > 15:
                st.caption(f"* 상위 15개 표시 (전체 {len(vs.source_summary)}개 파일)")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 인덱싱 현황 + 감사 로그 ─────────────────────────────────────
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.markdown(_sec("📈", "CMS 인덱싱 현황"), unsafe_allow_html=True)
        pct = (cs.indexed_chunks / cs.total_chunks) if cs.total_chunks > 0 else 0.0
        st.progress(pct, text=f"CMS 등록 청크 기준 {pct*100:.1f}% 인덱싱 완료")
        m1, m2, m3 = st.columns(3)
        m1.metric("인덱싱 완료", cs.indexed_chunks)
        m2.metric("대기 중",     cs.pending_chunks)
        m3.metric("사용중단",    cs.deprecated_documents)

    with col_r:
        st.markdown(_sec("📋", "최근 활동 로그"), unsafe_allow_html=True)
        _icon = {"upload":"📤","version_up":"🔄","deprecated":"🗑","chunk_edit":"✏️",
                 "chunk_delete":"🗑","faiss_index":"⚡","faiss_sync":"🔄",
                 "rollback":"⏪","markdown_save":"📝","chunk_save":"✂️","backup_create":"💾"}
        logs = cms.get_audit_logs(limit=12)
        if logs:
            for log in logs:
                ico = _icon.get(log.action, "·")
                st.markdown(
                    f'<div class="log-row">'
                    f'{ico} <b style="font-size:11px">{log.action}</b>'
                    f'<span style="color:#9CA3AF;margin-left:6px">{log.created_at[5:16]}</span><br>'
                    f'<span style="color:#9CA3AF;padding-left:10px">{log.detail[:50]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("아직 활동 기록이 없습니다.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(_sec("🖥️", "시스템 모니터링"), unsafe_allow_html=True)
    _render_system_monitor()

    st.divider()
    if st.button("🔄 새로고침", key="dash_refresh"):
        _cc(); st.rerun()


# ══════════════════════════════════════════════════════════════════════
#  탭 2: 문서 목록
# ══════════════════════════════════════════════════════════════════════

def _tab_doclist(cms: "CMSService", vec) -> None:
    cs = _cms_stats(cms)

    # ── CMS 비어있으면 동기화 배너 ────────────────────────────────
    if cs.total_documents == 0:
        vs = _vec_stats(vec)
        if vs and vs.is_loaded and vs.total_sources > 0:
            st.markdown(
                f'<div class="sync-banner">'
                f'<h3>📋 문서 목록이 비어 있습니다</h3>'
                f'<p>FAISS DB에 <b>{vs.total_sources}개</b> 소스 파일이 있습니다.<br>'
                f'동기화하면 기존 파일들이 문서 목록에 자동으로 등록됩니다.</p>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button("🔄 FAISS → CMS 동기화", type="primary", key="list_sync"):
                srcs = _sources(vec)
                with st.spinner(f"{len(srcs)}개 소스 동기화 중..."):
                    r = cms.sync_from_faiss(srcs)
                if r["success"]:
                    st.success(r["message"]); _cc(); st.rerun()
                else:
                    st.error(r["message"])
            return
        else:
            st.info("등록된 문서가 없습니다. [📤 파일 업로드] 탭에서 PDF를 추가하세요.")
            return

    # ── 필터 바 ────────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns([2, 2, 3, 1])
    with f1:
        status_f = st.selectbox(
            "상태", ["all", "active", "inactive", "deprecated"],
            format_func=lambda s: {"all":"🔍 전체","active":"✅ 활성",
                                   "inactive":"⏸ 비활성","deprecated":"🗑 사용중단"}.get(s, s),
            key="dl_status", label_visibility="collapsed",
        )
    with f2:
        depts  = [""] + cms.get_departments()
        dept_f = st.selectbox("부서", depts,
                              format_func=lambda d: "🏢 전체 부서" if d == "" else d,
                              key="dl_dept", label_visibility="collapsed")
    with f3:
        search_f = st.text_input("검색", placeholder="🔍 제목 · 태그 · 설명...",
                                 key="dl_search", label_visibility="collapsed")
    with f4:
        if st.button("🔄", key="dl_refresh", help="새로고침"):
            _cc(); st.rerun()

    # 필터 변경 → 페이지 리셋
    fk = f"{status_f}|{dept_f}|{search_f}"
    if st.session_state.get("_dl_fk") != fk:
        st.session_state["_dl_fk"] = fk
        st.session_state["dl_page"] = 1

    PAGE = 15
    page = st.session_state.get("dl_page", 1)
    docs, total = _docs(cms, status_f, dept_f, search_f, page, PAGE)
    tp = max(1, -(-total // PAGE))

    st.caption(f"총 **{total}건** | {page}/{tp} 페이지")

    if not docs:
        st.info("해당 조건의 문서가 없습니다.")
        return

    for doc in docs:
        is_open = st.session_state.get(f"open_{doc.document_id}", False)
        bl = "#1D4ED8" if is_open else "#2563EB"
        tag_html = " ".join(f'<span class="bdg bdg-info">{t}</span>' for t in doc.tags[:3])

        st.markdown(
            f'<div class="doc" style="border-left-color:{bl}">'
            f'<div class="dt">{doc.title}'
            f'<span style="font-size:11px;font-weight:400;color:#9CA3AF"> v{doc.version}</span>'
            f'&nbsp;{_b(doc.status)}&nbsp;{_b("indexed" if doc.indexed else "pending")}'
            f'</div>'
            f'<div class="dm">'
            f'<span>📂 {doc.department or "부서 미지정"}</span>'
            f'<span>📦 {doc.chunk_count:,}청크</span>'
            f'<span>📅 {doc.updated_at[:10]}</span>'
            f'{tag_html}</div></div>',
            unsafe_allow_html=True,
        )

        ba1, ba2, ba3, ba4, ba5, _ = st.columns([1, 1, 1, 1, 1, 3])
        with ba1:
            lbl = "📄 닫기" if is_open else "📄 상세"
            if st.button(lbl, key=f"btn_open_{doc.document_id}", use_container_width=True):
                st.session_state[f"open_{doc.document_id}"] = not is_open; st.rerun()
        with ba2:
            if doc.status == "active":
                if st.button("⏸", key=f"deact_{doc.document_id}", use_container_width=True, help="비활성화"):
                    cms.set_document_status(doc.document_id, "inactive"); _cc(); st.rerun()
            else:
                if st.button("✅", key=f"act_{doc.document_id}", use_container_width=True, help="활성화"):
                    cms.set_document_status(doc.document_id, "active"); _cc(); st.rerun()
        with ba3:
            if not doc.indexed and doc.chunk_count > 0:
                if st.button("⚡ 인덱싱", key=f"idx_{doc.document_id}",
                             type="primary", use_container_width=True):
                    with st.spinner("인덱싱 중..."):
                        r = cms.build_faiss_from_document(doc.document_id)
                    (st.success if r["success"] else st.error)(r["message"])
                    _cc(); st.rerun()
            else:
                st.button("✅", key=f"idxd_{doc.document_id}", disabled=True,
                          use_container_width=True, help="인덱싱 완료")
        with ba4:
            hist = cms.get_version_history(doc.document_id)
            vk = f"ver_{doc.document_id}"
            if len(hist) > 1:
                vl = "🔼" if st.session_state.get(vk) else f"🔄 v{len(hist)}"
                if st.button(vl, key=f"btn_ver_{doc.document_id}", use_container_width=True,
                             help="버전 이력"):
                    st.session_state[vk] = not st.session_state.get(vk, False); st.rerun()
        with ba5:
            if st.button("🗑", key=f"dep_{doc.document_id}", use_container_width=True,
                         help="사용중단"):
                cms.set_document_status(doc.document_id, "deprecated"); _cc(); st.rerun()

        # 버전 이력
        if st.session_state.get(f"ver_{doc.document_id}"):
            hist = cms.get_version_history(doc.document_id)
            for h in hist:
                hc1, hc2, hc3, hc4 = st.columns([1, 2, 2, 1])
                hc1.markdown(f"**v{h.version}**")
                hc2.caption(h.updated_at[:10])
                hc3.markdown(_b(h.status), unsafe_allow_html=True)
                with hc4:
                    if h.status != "active":
                        if st.button("⏪", key=f"rb_{h.document_id}", help="롤백"):
                            if cms.rollback_to_version(h.document_id):
                                st.success("롤백 완료"); _cc(); st.rerun()

        # 인라인 상세
        if is_open:
            _detail_inline(cms, doc.document_id)

    # 페이지네이션
    if tp > 1:
        st.divider()
        pg1, pg2, pg3 = st.columns([1, 4, 1])
        with pg1:
            if page > 1 and st.button("◀ 이전", key="dl_prev"):
                st.session_state["dl_page"] -= 1; _cc(); st.rerun()
        with pg2:
            st.markdown(f"<div style='text-align:center;font-size:12px;color:#6B7280;padding:8px'>"
                        f"{page} / {tp} 페이지</div>", unsafe_allow_html=True)
        with pg3:
            if page < tp and st.button("다음 ▶", key="dl_next"):
                st.session_state["dl_page"] += 1; _cc(); st.rerun()


def _detail_inline(cms: "CMSService", doc_id: str) -> None:
    """문서 카드 아래 인라인 상세 패널."""
    doc = cms.get_document(doc_id)
    if doc is None:
        return
    st.markdown('<div class="det-panel">', unsafe_allow_html=True)
    t1, t2, t3 = st.tabs(["📝 Markdown", "📦 청크", "📋 로그"])
    with t1:
        md = cms.load_markdown(doc_id) or ""
        if st.checkbox("✏️ 편집", key=f"mde_{doc_id}"):
            new = st.text_area("", value=md, height=280, key=f"mda_{doc_id}", label_visibility="collapsed")
            s1, s2 = st.columns(2)
            with s1:
                if st.button("💾 저장", key=f"mds_{doc_id}", type="primary"):
                    cms.save_markdown(doc_id, new); st.success("저장 완료"); _cc(); st.rerun()
            with s2:
                if st.button("🔀 청크 재분할", key=f"mdc_{doc_id}"):
                    chunks = markdown_to_chunks(new, document_id=doc_id)
                    saved  = cms.save_chunks(doc_id, chunks)
                    _cc(); st.success(f"{saved}개 청크"); st.rerun()
        else:
            if md:
                st.markdown(md[:1800] + ("..." if len(md) > 1800 else ""))
            else:
                st.info("Markdown 없음. 파일 업로드 시 자동 생성됩니다.")
    with t2:
        _chunks_panel(cms, doc_id, pf=f"il_{doc_id}")
    with t3:
        logs = cms.get_audit_logs(document_id=doc_id, limit=10)
        if logs:
            import pandas as pd
            st.dataframe(pd.DataFrame([{"시각":l.created_at[5:16],"액션":l.action,"내용":l.detail[:50]} for l in logs]),
                         use_container_width=True, hide_index=True, height=160)
        else:
            st.info("로그 없음")
    st.markdown('</div>', unsafe_allow_html=True)


def _chunks_panel(cms: "CMSService", doc_id: str, pf: str = "det") -> None:
    """청크 목록 + 수정/삭제 공통 패널."""
    chunks = cms.get_chunks(doc_id)
    if not chunks:
        st.info("청크가 없습니다. Markdown 탭에서 '청크 재분할'을 실행하세요.")
        return

    kw_col, st_col = st.columns([3, 1])
    with kw_col:
        kw = st.text_input("", placeholder="🔍 청크 내용 검색...",
                           key=f"{pf}_kw", label_visibility="collapsed")
    with st_col:
        ic = sum(1 for c in chunks if c.embedding_status == "indexed")
        pc = sum(1 for c in chunks if c.embedding_status == "pending")
        st.markdown(
            f'<div style="text-align:right;font-size:11px;color:#6B7280;padding-top:6px">'
            f'✅ {ic} &nbsp; ⏳ {pc}</div>',
            unsafe_allow_html=True,
        )

    display = [c for c in chunks if kw.lower() in c.content.lower()] if kw else chunks
    st.caption(f"총 {len(chunks)}개 | 표시 {len(display)}개")
    if len(display) > 40:
        st.info("처음 40개만 표시합니다."); display = display[:40]

    for chunk in display:
        pl = f"p.{chunk.page}" if chunk.page > 0 else ""
        al = f" {chunk.article}" if chunk.article else ""
        hdr = f"#{chunk.chunk_index+1}{' — '+pl if pl else ''}{al} · {chunk.char_count:,}자 [{chunk.embedding_status}]"

        with st.expander(hdr, expanded=False):
            st.markdown(_b(chunk.embedding_status), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            ek = f"{pf}_ce_{chunk.chunk_id}"
            if st.session_state.get(ek):
                nc = st.text_area("", value=chunk.content, height=150,
                                  key=f"{pf}_ct_{chunk.chunk_id}", label_visibility="collapsed")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("💾 저장", key=f"{pf}_cs_{chunk.chunk_id}", type="primary"):
                        cms.update_chunk(chunk.chunk_id, nc)
                        st.session_state[ek] = False; _cc(); st.rerun()
                with col2:
                    if st.button("취소", key=f"{pf}_cc_{chunk.chunk_id}"):
                        st.session_state[ek] = False; st.rerun()
            else:
                st.markdown(f'<div class="ck-box">{chunk.content}</div>', unsafe_allow_html=True)
                ca1, ca2 = st.columns(2)
                with ca1:
                    if st.button("✏️ 수정", key=f"{pf}_ceb_{chunk.chunk_id}"):
                        st.session_state[ek] = True; st.rerun()
                with ca2:
                    dk = f"{pf}_cd_{chunk.chunk_id}"
                    if st.session_state.get(dk):
                        if st.button("⚠️ 삭제 확인", key=f"{pf}_cdo_{chunk.chunk_id}", type="primary"):
                            cms.delete_chunk(chunk.chunk_id)
                            st.session_state[dk] = False; _cc(); st.rerun()
                    else:
                        if st.button("🗑 삭제", key=f"{pf}_cdb_{chunk.chunk_id}"):
                            st.session_state[dk] = True; st.rerun()


# ══════════════════════════════════════════════════════════════════════
#  탭 3: 문서 상세
# ══════════════════════════════════════════════════════════════════════

def _tab_detail(cms: "CMSService") -> None:
    st.markdown(_sec("📄", "문서 상세 뷰어"), unsafe_allow_html=True)

    docs_all, total = cms.list_documents(page_size=300)
    if not docs_all:
        st.info("등록된 문서가 없습니다. [📤 파일 업로드] 탭에서 추가하거나 [📋 문서 목록] 탭에서 FAISS 동기화를 실행하세요.")
        return

    st.caption(f"총 {total}개 문서")
    opts   = {d.document_id: f"{d.title}  v{d.version}  [{d.status}]" for d in docs_all}
    sel_id = st.selectbox("문서 선택", list(opts.keys()),
                          format_func=lambda k: opts.get(k, k),
                          key="det_sel", label_visibility="collapsed")

    doc = cms.get_document(sel_id)
    if doc is None:
        st.error("문서를 찾을 수 없습니다."); return

    tag_str = " ".join(f'<span class="bdg bdg-info">{t}</span>' for t in doc.tags)
    st.markdown(
        f'<div class="doc" style="border-left-color:#1D4ED8;margin-bottom:14px">'
        f'<div class="dt" style="font-size:16px">{doc.title}'
        f'<span style="font-size:12px;font-weight:400;color:#9CA3AF"> v{doc.version}</span>'
        f'&nbsp;{_b(doc.status)}&nbsp;{_b("indexed" if doc.indexed else "pending")}'
        f'</div>'
        f'<div class="dm">'
        f'<span>📂 {doc.department or "부서 미지정"}</span>'
        f'<span>📦 {doc.chunk_count:,}청크</span>'
        f'<span>📅 등록 {doc.created_at[:10]}</span>'
        f'<span>📝 수정 {doc.updated_at[:10]}</span>'
        f'{tag_str}</div>'
        + (f'<div style="margin-top:5px;font-size:12px;color:#6B7280">{doc.description}</div>' if doc.description else "")
        + '</div>',
        unsafe_allow_html=True,
    )

    act1, act2 = st.columns([1, 1])
    with act1:
        if not doc.indexed and doc.chunk_count > 0:
            if st.button("⚡ 벡터 인덱싱", key="det_idx", type="primary"):
                with st.spinner("인덱싱 중..."):
                    r = cms.build_faiss_from_document(sel_id)
                (st.success if r["success"] else st.error)(r["message"])
                _cc(); st.rerun()
    with act2:
        if st.button("🔀 청크 재분할", key="det_rc"):
            md = cms.load_markdown(sel_id)
            if md:
                chunks = markdown_to_chunks(md, document_id=sel_id)
                saved  = cms.save_chunks(sel_id, chunks)
                _cc(); st.success(f"{saved}개 청크 재분할"); st.rerun()
            else:
                st.error("Markdown이 없습니다.")

    sm, sc, sl = st.tabs(["📝 Markdown", "📦 청크 목록", "📋 감사 로그"])
    with sm:
        mc = cms.load_markdown(sel_id) or ""
        em = st.toggle("✏️ 편집 모드", key="det_em")
        if em:
            nm = st.text_area("", value=mc, height=460, key="det_ma", label_visibility="collapsed")
            s1, s2 = st.columns(2)
            with s1:
                if st.button("💾 저장", type="primary", key="det_ms"):
                    cms.save_markdown(sel_id, nm); st.success("저장 완료"); _cc(); st.rerun()
            with s2:
                if st.button("🔀 청크 재분할", key="det_mc"):
                    chunks = markdown_to_chunks(nm, document_id=sel_id)
                    saved  = cms.save_chunks(sel_id, chunks)
                    _cc(); st.success(f"{saved}개 청크"); st.rerun()
        else:
            if mc:
                st.markdown(mc[:4000] + ("...(이하 생략)" if len(mc) > 4000 else ""))
            else:
                st.info("Markdown이 없습니다.")
    with sc:
        _chunks_panel(cms, sel_id, pf="det")
    with sl:
        logs = cms.get_audit_logs(document_id=sel_id, limit=30)
        if logs:
            import pandas as pd
            st.dataframe(
                pd.DataFrame([{"시각":l.created_at,"액션":l.action,"내용":l.detail[:70],"처리자":l.performed_by} for l in logs]),
                use_container_width=True, hide_index=True, height=260,
            )
        else:
            st.info("감사 로그 없음")


# ══════════════════════════════════════════════════════════════════════
#  탭 4: 파일 업로드
# ══════════════════════════════════════════════════════════════════════

def _tab_upload(cms: "CMSService") -> None:
    st.markdown(_sec("📤", "문서 업로드"), unsafe_allow_html=True)

    if is_building():
        st.warning("⚙️ 벡터 DB 재구축 중입니다. 완료 후 업로드하세요."); return

    with st.form("upload_form", clear_on_submit=True):
        uploaded = st.file_uploader("PDF 파일", type=["pdf"],
                                    help="텍스트 기반 PDF만 지원 (스캔본 불가)")
        u1, u2 = st.columns(2)
        with u1:
            title    = st.text_input("문서 제목 *", placeholder="예: 원무 업무 규정집 2024")
            dept     = st.text_input("부서",        placeholder="예: 원무팀, 간호부")
        with u2:
            tags_raw = st.text_input("태그 (쉼표 구분)", placeholder="원무, 규정, 2024")
            desc     = st.text_area("설명", height=68)

        uc1, uc2 = st.columns(2)
        with uc1:
            cs = st.select_slider("청크 크기(자)", [400,500,600,700,800,900,1000,1200], value=800)
            ov = st.select_slider("청크 중첩(자)", [0,50,100,150,200,250], value=150)
        with uc2:
            force_ver  = st.checkbox("🔄 동일 파일도 새 버전으로 등록")
            auto_chunk = st.checkbox("자동 청킹", value=True)
            auto_index = st.checkbox("자동 벡터 인덱싱", value=False)

        submitted = st.form_submit_button("🚀 업로드", type="primary", use_container_width=True)

    if submitted and uploaded:
        if not title.strip():
            st.error("제목을 입력해주세요."); return

        file_bytes = uploaded.getvalue()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        if len(file_bytes) > 200 * 1024 * 1024:
            st.error("파일 크기 초과 (최대 200MB)"); return

        with st.status("처리 중...", expanded=True) as sw:
            st.write("📋 문서 등록 중...")
            result = cms.upload_document(
                file_bytes=file_bytes, file_name=uploaded.name,
                title=title.strip(), department=dept.strip(),
                tags=tags, description=desc.strip(),
                force_new_version=force_ver,
            )
            if result.get("duplicate") and not force_ver:
                st.warning(result["message"]); sw.update(label="⚠️ 중복 감지", state="error"); return

            doc_id = result["document_id"]
            st.write(f"  ✅ doc_id `{doc_id}` (v{result.get('version',1)})")

            st.write("📝 Markdown 변환 중...")
            md_text = pdf_to_markdown(file_bytes, uploaded.name)
            cms.save_markdown(doc_id, md_text)
            st.write(f"  ✅ {len(md_text):,}자")

            if auto_chunk:
                st.write("✂️ 청크 분할 중...")
                chunks = markdown_to_chunks(md_text, cs, ov, doc_id)
                saved  = cms.save_chunks(doc_id, chunks)
                st.write(f"  ✅ {saved}개 청크")
                if auto_index and saved > 0:
                    st.write("⚡ 벡터 인덱싱 중...")
                    ir = cms.build_faiss_from_document(doc_id)
                    st.write(f"  {'✅' if ir['success'] else '❌'} {ir.get('message','')}")

            sw.update(label="✅ 완료!", state="complete")

        st.success(f"**{result['message']}**")
        if result.get("is_new_version"):
            st.info("이전 버전이 '사용 중단' 상태로 변경되었습니다.")

        with st.expander("📝 Markdown 미리보기", expanded=False):
            st.markdown(md_text[:2000] + ("..." if len(md_text) > 2000 else ""))
        _cc()

    with st.expander("📌 업로드 가이드"):
        st.markdown("""
| 항목 | 내용 |
|------|------|
| 지원 형식 | 텍스트 기반 PDF (스캔본 불가) |
| 최대 크기 | 200MB |
| 중복 감지 | MD5 해시 기반 (완전 동일 파일) |
| 버전 관리 | 같은 제목 재업로드 → 신규 버전, 기존 deprecated |
| Soft Delete | 물리 삭제 없음 — 모든 버전 이력 보존 |
        """)


# ══════════════════════════════════════════════════════════════════════
#  탭 5: 검색 테스트
# ══════════════════════════════════════════════════════════════════════

def _tab_search(cms: "CMSService", vec) -> None:
    st.markdown(_sec("🔍", "검색 테스트"), unsafe_allow_html=True)
    st.caption("FAISS 벡터 검색으로 쿼리와 가장 유사한 청크를 찾습니다.")

    if vec is None:
        st.warning("⚠️ 벡터 DB 서비스 사용 불가."); return

    vs = _vec_stats(vec)
    if vs is None or not vs.is_loaded:
        st.error("FAISS DB가 없습니다. [🔧 관리 도구] 탭에서 재구축하세요."); return

    s1, s2, s3 = st.columns([5, 1, 1])
    with s1:
        query = st.text_input("", placeholder="예: 연차휴가 신청 방법, 야간 수당 지급 기준...",
                              key="sq", label_visibility="collapsed")
    with s2:
        top_k = st.number_input("결과 수", 1, 20, 5, key="sk")
    with s3:
        go = st.button("🔍 검색", type="primary", key="sgo",
                       use_container_width=True, disabled=not query)

    if not go or not query:
        st.info("검색어를 입력하고 검색 버튼을 클릭하세요."); return

    with st.spinner(f"'{query}' 검색 중..."):
        try:
            raw = vec.search_chunks(query, top_k=top_k)
        except Exception as e:
            st.error(f"검색 실패: {e}"); return

    if not raw:
        st.warning("검색 결과가 없습니다. FAISS DB를 먼저 구축하세요."); return

    docs_all, _ = cms.list_documents(page_size=500)
    f2d = {d.file_name: d for d in docs_all}

    st.markdown(
        f'<div style="font-size:13px;color:#374151;margin-bottom:10px">'
        f'<b>{len(raw)}개 결과</b> — <em>"{query}"</em>'
        f'<span style="color:#9CA3AF;margin-left:8px">(FAISS {_fmt(vs.total_vectors)}벡터 중 검색)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    for rank, (chunk, score) in enumerate(raw, start=1):
        sim = max(0.0, (1.0 - score / 10.0)) * 100
        sc_ = _sim_c(sim)
        pl  = f"p.{chunk.page}" if chunk.page > 0 else ""
        cd  = f2d.get(chunk.source)

        title_exp = (
            f"**{rank}위** &nbsp; {chunk.source[:40]}"
            + (f" {pl}" if pl else "")
            + f" &nbsp;|&nbsp; 유사도 **{sim:.1f}%**"
        )

        with st.expander(title_exp, expanded=(rank <= 2)):
            m1, m2, m3 = st.columns([1, 3, 2])
            with m1:
                st.markdown(
                    f'<div style="font-size:24px;font-weight:800;color:{sc_};text-align:center;line-height:1.1">'
                    f'{sim:.0f}%</div>'
                    f'<div style="font-size:10px;color:#9CA3AF;text-align:center">유사도</div>'
                    f'<div style="font-size:10px;color:#D1D5DB;text-align:center">L2: {score:.4f}</div>',
                    unsafe_allow_html=True,
                )
            with m2:
                st.markdown(f"**출처**: `{chunk.source}`")
                if pl:
                    st.markdown(f"**페이지**: {pl}")
                st.markdown(f"**글자 수**: {chunk.char_count:,}자")
            with m3:
                if cd:
                    st.markdown(
                        f"**CMS 문서**: {cd.title}  \n"
                        f"**버전**: v{cd.version} &nbsp; {_b(cd.status)}",
                        unsafe_allow_html=True,
                    )
                    if cd.department:
                        st.caption(f"📂 {cd.department}")
                else:
                    st.caption("(CMS 미등록)")

            st.divider()
            content = getattr(chunk, "text_full", "") or getattr(chunk, "text_preview", "")
            if content:
                st.markdown(f'<div class="ck-box">{content}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
#  탭 6: 관리 도구
# ══════════════════════════════════════════════════════════════════════

def _tab_tools(cms: "CMSService", vec) -> None:
    col_l, col_r = st.columns(2)

    # ── 인덱싱 관리 ───────────────────────────────────────────────
    with col_l:
        st.markdown(_sec("⚡", "벡터 인덱싱 관리"), unsafe_allow_html=True)
        docs_active, _ = cms.list_documents(status_filter="active", page_size=300)
        pending_docs   = [d for d in docs_active if not d.indexed and d.chunk_count > 0]

        if pending_docs:
            st.warning(f"⚠️ 인덱싱 대기: **{len(pending_docs)}개** 문서")
            for pd_ in pending_docs[:5]:
                st.markdown(f"  · {pd_.title} ({pd_.chunk_count:,}청크)")
            if len(pending_docs) > 5:
                st.caption(f"  + {len(pending_docs)-5}개 더...")
            if st.button("⚡ 전체 일괄 인덱싱", type="primary", key="batch_idx"):
                prog = st.progress(0, text="인덱싱 중...")
                ok = 0
                for i, d in enumerate(pending_docs):
                    r = cms.build_faiss_from_document(d.document_id)
                    if r["success"]: ok += 1
                    prog.progress((i+1)/len(pending_docs), text=d.title[:20])
                prog.progress(1.0, text="완료!")
                st.success(f"완료: {ok}/{len(pending_docs)}개")
                _cc(); st.rerun()
        else:
            st.success("✅ 모든 활성 문서 인덱싱 완료")

        st.divider()
        st.markdown("**FAISS → CMS 동기화**")
        st.caption("기존 FAISS 소스 파일을 CMS 문서 레코드로 일괄 등록합니다.")
        if vec and st.button("🔄 FAISS 전체 동기화", key="tool_sync"):
            srcs = _sources(vec)
            with st.spinner(f"{len(srcs)}개 소스 처리 중..."):
                r = cms.sync_from_faiss(srcs)
            st.success(r["message"]); _cc(); st.rerun()

        st.divider()
        st.markdown("**특정 문서 재인덱싱**")
        docs_all, _ = cms.list_documents(page_size=300)
        if docs_all:
            ri = st.selectbox("문서 선택", [d.document_id for d in docs_all],
                              format_func=lambda k: next(
                                  (f"{d.title} v{d.version}" for d in docs_all if d.document_id == k), k),
                              key="ri_sel", label_visibility="collapsed")
            ri1, ri2 = st.columns(2)
            with ri1:
                if st.button("⚡ 재인덱싱", key="ri_btn"):
                    with st.spinner("재인덱싱 중..."):
                        r = cms.build_faiss_from_document(ri)
                    (st.success if r["success"] else st.error)(r["message"])
                    _cc(); st.rerun()
            with ri2:
                if st.button("🗑 벡터 DB 제거", key="ri_rm"):
                    with st.spinner("제거 중..."):
                        r = cms.remove_from_faiss(ri)
                    (st.success if r["success"] else st.error)(r["message"])
                    _cc(); st.rerun()

        st.divider()
        with st.expander("⚠️ 전체 재구축 (고급)"):
            st.error("모든 PDF를 다시 읽어 DB를 완전 재구축합니다. 5~30분 소요.")
            if st.checkbox("위험성을 이해하고 진행합니다", key="rb_ck"):
                if vec and st.button("🔄 전체 재구축", type="primary", key="rb_all"):
                    with st.spinner("재구축 중..."):
                        r = vec.rebuild_all()
                    (st.success if r.success else st.error)(r.message)
                    _cc(); st.rerun()

        st.divider()
        st.markdown("**☁️ 구글 드라이브 폴더 선택 재구축**")
        _render_gdrive_rebuild(cms, vec)

    # ── 백업 관리 ─────────────────────────────────────────────────
    with col_r:
        st.markdown(_sec("💾", "백업 관리"), unsafe_allow_html=True)

        # 백업 생성 시점 안내
        st.info(
            "**백업이 자동 생성되는 시점**\n\n"
            "· 파일 단위 삭제 (`delete_source`) 실행 시\n"
            "· 전체 재구축 (`rebuild_all`) 실행 시\n\n"
            "그 외에는 아래 **수동 백업** 버튼으로 언제든지 생성할 수 있습니다."
        )

        if st.button("💾 지금 수동 백업 생성", key="manual_bk", type="primary"):
            with st.spinner("백업 생성 중..."):
                r = cms.create_backup_manual()
            (st.success if r["success"] else st.error)(r["message"])
            _cc(); st.rerun()

        st.divider()

        if vec is None:
            st.info("벡터 DB 서비스 사용 불가")
        else:
            try:
                backups = vec.get_backup_list()
                if backups:
                    st.markdown(f"**{len(backups)}개 백업 보관** (최근 5개 자동 유지)")
                    for bk in backups:
                        with st.expander(
                            f"📦 {bk['name']}  |  {bk['size_mb']} MB  |  {bk['created_at'][:16]}",
                            expanded=False,
                        ):
                            st.code(bk["path"])
                            rk = f"rst_{bk['name']}"
                            if st.session_state.get(f"cfm_{rk}"):
                                st.error("정말 이 버전으로 복원하시겠습니까?")
                                ck1, ck2 = st.columns(2)
                                with ck1:
                                    if st.button("✅ 복원 확인", key=f"ok_{rk}", type="primary"):
                                        r = vec.restore_backup(bk["name"])
                                        (st.success if r.success else st.error)(r.message)
                                        st.session_state[f"cfm_{rk}"] = False; _cc(); st.rerun()
                                with ck2:
                                    if st.button("취소", key=f"cl_{rk}"):
                                        st.session_state[f"cfm_{rk}"] = False; st.rerun()
                            else:
                                if st.button("⏪ 이 버전으로 복원", key=rk, use_container_width=True):
                                    st.session_state[f"cfm_{rk}"] = True; st.rerun()
                else:
                    st.info("백업이 없습니다.")
            except Exception as e:
                st.error(f"백업 목록 로드 실패: {e}")

    # ── 전체 감사 로그 ─────────────────────────────────────────────
    st.divider()
    st.markdown(_sec("📋", "전체 감사 로그"), unsafe_allow_html=True)
    ll = st.select_slider("최근 로그", [20, 50, 100, 200], value=50, key="ll")
    logs = cms.get_audit_logs(limit=ll)
    if logs:
        import pandas as pd
        st.dataframe(
            pd.DataFrame([{"시각":l.created_at,"액션":l.action,"내용":l.detail[:70],
                           "문서ID":l.document_id[:10]+"...","처리자":l.performed_by} for l in logs]),
            use_container_width=True, hide_index=True, height=300,
        )
    else:
        st.info("감사 로그가 없습니다.")




# ══════════════════════════════════════════════════════════════════════
#  시스템 모니터링
# ══════════════════════════════════════════════════════════════════════

def _render_system_monitor() -> None:
    if "mon_req" not in st.session_state:
        st.session_state["mon_req"] = 0
        st.session_state["mon_err"] = 0
        st.session_state["mon_ms"]  = []

    req    = st.session_state["mon_req"]
    err    = st.session_state["mon_err"]
    err_r  = f"{err/max(req,1)*100:.1f}%" if req > 0 else "0%"
    ms_lst = st.session_state["mon_ms"][-20:]
    avg_ms = f"{sum(ms_lst)/len(ms_lst):.0f}ms" if ms_lst else "N/A"

    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=0.1)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        _ok  = True
    except ImportError:
        cpu = mem = disk = None
        _ok = False

    def _mc(ico, val_str, lbl, color="#2563EB"):
        return (
            f'<div class="kpi"><span class="ico">{ico}</span>'
            f'<div class="val" style="color:{color};font-size:19px">{val_str}</div>'
            f'<div class="lbl">{lbl}</div></div>'
        )

    def _pct_color(v):
        return "#DC2626" if v >= 90 else "#D97706" if v >= 75 else "#16A34A"

    sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
    if _ok:
        sc1.markdown(_mc("🖥️", f"{cpu:.1f}%",     "CPU 사용률",  _pct_color(cpu)),  unsafe_allow_html=True)
        sc2.markdown(_mc("🧠", f"{mem.percent:.1f}%","메모리",    _pct_color(mem.percent)), unsafe_allow_html=True)
        sc3.markdown(_mc("💿", f"{disk.percent:.1f}%","디스크",   _pct_color(disk.percent)), unsafe_allow_html=True)
    else:
        sc1.caption("psutil 없음"); sc2.caption("설치: pip install psutil"); sc3.empty()
    sc4.markdown(_mc("🔍", f"{req:,}",   "검색 요청"),   unsafe_allow_html=True)
    sc5.markdown(_mc("⚡", avg_ms,       "평균 응답"),    unsafe_allow_html=True)
    sc6.markdown(_mc("❌", err_r, "오류율", "#DC2626" if err > 0 else "#16A34A"), unsafe_allow_html=True)

    if not _ok:
        st.caption("시스템 지표는 `pip install psutil` 후 이용 가능합니다.")


# ══════════════════════════════════════════════════════════════════════
#  구글 드라이브 폴더 → 재구축
# ══════════════════════════════════════════════════════════════════════

def _render_gdrive_rebuild(cms, vec) -> None:
    DRIVE_URL = "https://drive.google.com/drive/folders/13hG8qM8iQsovKBI4a_3X6SjkO0UUU8sL"
    st.caption("로컬에 동기화된 구글 드라이브 폴더 경로를 지정하면 해당 PDF를 벡터 DB에 추가합니다.")
    st.markdown(
        f'<a href="{DRIVE_URL}" target="_blank" style="font-size:12px;color:#2563EB">'
        f'📁 기본 구글 드라이브 폴더 열기</a>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    cp, cs_ = st.columns([4, 1])
    with cp:
        folder_path = st.text_input(
            "폴더 경로",
            placeholder="예: D:\\MH\\guidbot\\docs",
            key="gd_path", label_visibility="collapsed",
        )
    with cs_:
        scan_btn = st.button("🔍 스캔", key="gd_scan", use_container_width=True)

    if scan_btn and folder_path:
        from pathlib import Path as _P
        p = _P(folder_path.strip())
        if not p.exists():
            st.error(f"경로가 없습니다: `{folder_path}`")
        else:
            pdfs = sorted(p.glob("**/*.pdf"))
            if not pdfs:
                st.warning("PDF 파일이 없습니다.")
            else:
                st.session_state["gd_files"] = [str(f) for f in pdfs]
                st.success(f"✅ PDF {len(pdfs)}개 발견")

    gd_files = st.session_state.get("gd_files", [])
    if not gd_files:
        return

    from pathlib import Path as _P
    labels = {f: _P(f).name for f in gd_files}
    selected = st.multiselect(
        "파일 선택 (전체 선택 후 제외할 파일 삭제 가능)",
        options=gd_files,
        default=gd_files,
        format_func=lambda f: labels[f],
        key="gd_sel",
        label_visibility="collapsed",
    )

    if not selected:
        return

    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        gd_cs = st.select_slider("청크 크기", [400,500,600,700,800,1000,1200], value=800, key="gd_cs")
    with gc2:
        gd_ov = st.select_slider("청크 중첩", [0,50,100,150,200,250], value=150, key="gd_ov")
    with gc3:
        gd_mode = st.radio("처리 방식", ["증분 추가", "파일 교체"], key="gd_mode", horizontal=True)

    msg_mode = "기존 DB에 추가" if gd_mode == "증분 추가" else "선택 파일 교체"
    st.warning(f"**{len(selected)}개 파일** 처리 예정 — 방식: {msg_mode}")

    if st.button(f"☁️ {len(selected)}개 파일 처리 실행", type="primary", key="gd_run"):
        prog = st.progress(0, text="준비 중...")
        ok_list, fail_list = [], []

        for i, fpath in enumerate(selected):
            fname = _P(fpath).name
            prog.progress((i + 0.5) / len(selected), text=f"처리: {fname[:28]}")
            try:
                fb = _P(fpath).read_bytes()
                if gd_mode == "파일 교체" and vec:
                    try:
                        vec.delete_source(fname)
                    except Exception:
                        pass
                res = cms.upload_document(
                    file_bytes=fb, file_name=fname,
                    title=fname.replace(".pdf", "").replace("_", " "),
                    department="구글드라이브",
                    force_new_version=(gd_mode == "파일 교체"),
                )
                doc_id = res.get("document_id", "")
                if doc_id:
                    md = pdf_to_markdown(fb, fname)
                    cms.save_markdown(doc_id, md)
                    cks = markdown_to_chunks(md, gd_cs, gd_ov, doc_id)
                    cms.save_chunks(doc_id, cks)
                    ir = cms.build_faiss_from_document(doc_id)
                    if ir.get("success"):
                        ok_list.append(fname)
                    else:
                        fail_list.append((fname, ir.get("message", "")))
                else:
                    fail_list.append((fname, res.get("message", "")))
            except Exception as ex:
                fail_list.append((fname, str(ex)[:60]))
            prog.progress((i + 1) / len(selected), text=f"완료: {fname[:28]}")

        prog.progress(1.0, text="전체 완료!")
        st.success(f"✅ 성공: {len(ok_list)}개")
        if fail_list:
            st.error(f"❌ 실패: {len(fail_list)}개")
            for fn, msg in fail_list:
                st.caption(f"  · {fn}: {msg}")
        _cc()
        st.session_state.pop("gd_files", None)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════
#  탭 7: 로그 관리
# ══════════════════════════════════════════════════════════════════════

def _tab_logs(cms) -> None:
    st.markdown(_sec("📋", "로그 관리"), unsafe_allow_html=True)

    ALL_ACTIONS = [
        "전체", "upload", "version_up", "deprecated", "chunk_edit",
        "chunk_delete", "faiss_index", "faiss_sync", "faiss_remove",
        "rollback", "markdown_save", "chunk_save", "backup_create",
        "status_active", "status_inactive", "status_deprecated",
    ]

    lf1, lf2, lf3, lf4 = st.columns([2, 2, 3, 1])
    with lf1:
        act_f = st.selectbox("액션", ALL_ACTIONS, key="lf_act", label_visibility="collapsed")
    with lf2:
        lim_f = st.select_slider("최근 로그 수", [50, 100, 200, 500, 1000], value=100, key="lf_lim")
    with lf3:
        kw_f = st.text_input("검색", placeholder="🔍 내용·문서ID·액션 검색...",
                             key="lf_kw", label_visibility="collapsed")
    with lf4:
        if st.button("🔄", key="lf_ref", help="새로고침"):
            _cc(); st.rerun()

    logs = cms.get_audit_logs(limit=lim_f)
    if act_f != "전체":
        logs = [l for l in logs if l.action == act_f]
    if kw_f:
        kl = kw_f.lower()
        logs = [l for l in logs if kl in l.detail.lower() or kl in l.document_id.lower() or kl in l.action.lower()]

    st.caption(f"총 **{len(logs)}건** 표시")

    if not logs:
        st.info("해당 조건의 로그가 없습니다."); return

    import pandas as pd
    df = pd.DataFrame([{
        "시각":   l.created_at,
        "액션":   l.action,
        "내용":   l.detail[:80],
        "문서ID": l.document_id[:12] + "...",
        "처리자": l.performed_by,
    } for l in logs])

    st.dataframe(df, use_container_width=True, hide_index=True, height=380)

    # ── 액션 분포 차트 ──────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    act_cnt = df["액션"].value_counts().reset_index()
    act_cnt.columns = ["액션", "건수"]
    st.markdown(_sec("📊", "액션별 발생 건수"), unsafe_allow_html=True)
    st.bar_chart(act_cnt.set_index("액션"), color="#2563EB", height=180)

    # ── CSV 내보내기 + 오래된 로그 삭제 ───────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    col_exp, col_del = st.columns([2, 3])
    with col_exp:
        csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            "⬇️ CSV 내보내기", data=csv,
            file_name=f"cms_log_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv", key="log_csv", use_container_width=True,
        )
    with col_del:
        with st.expander("🗑 오래된 로그 삭제"):
            st.warning("지정 일수 이전 로그를 영구 삭제합니다.")
            keep = st.number_input("최근 N일 유지", min_value=7, max_value=365, value=90, key="log_keep")
            if st.checkbox("삭제를 이해하고 진행합니다", key="log_del_ck"):
                if st.button("🗑 삭제 실행", type="primary", key="log_del_btn"):
                    deleted = _purge_logs(cms, keep)
                    st.success(f"✅ {deleted}건 삭제"); _cc(); st.rerun()


def _purge_logs(cms, keep_days: int) -> int:
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with cms._lock:
            cur = cms._db().execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
            cms._db().commit()
            return cur.rowcount
    except Exception:
        return 0

# ══════════════════════════════════════════════════════════════════════
#  사이드바
# ══════════════════════════════════════════════════════════════════════

def _sidebar(cms: "CMSService", vec) -> None:
    with st.sidebar:
        st.markdown(
            """<div style="text-align:center;padding:14px 0 8px">
            <div style="font-size:28px;margin-bottom:4px">🗄️</div>
            <div style="font-weight:700;color:#fff;font-size:14px">RAG CMS</div>
            <div style="color:rgba(255,255,255,.38);font-size:11px;margin-top:2px">좋은문화병원 가이드봇</div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("**📊 현황**")
        try:
            cs = _cms_stats(cms)
            rows = (
                f"| 활성 문서 | **{cs.active_documents}** |\n"
                f"| 총 청크 | **{_fmt(cs.total_chunks)}** |\n"
                f"| 인덱싱 완료 | **{_fmt(cs.indexed_chunks)}** |\n"
                f"| 대기 청크 | **{cs.pending_chunks}** |"
            )
            if vec:
                vs = _vec_stats(vec)
                if vs and vs.is_loaded:
                    rows += (
                        f"\n| FAISS 벡터 | **{_fmt(vs.total_vectors)}** |"
                        f"\n| 소스 파일 | **{vs.total_sources}** |"
                        f"\n| FAISS 크기 | **{vs.db_size_mb} MB** |"
                    )
            st.markdown(f"| 항목 | 값 |\n|------|-----|\n{rows}")
        except Exception:
            st.warning("통계 로드 실패")
        st.divider()
        if is_building():
            st.warning("⚙️ **재구축 중...**")
        with st.expander("⚠️ 운영 주의사항"):
            st.markdown("""
- Soft Delete (물리 삭제 없음)
- 버전업 시 기존 버전 자동 보존
- 스캔 PDF 미지원
- 청크 수정 후 재인덱싱 필요
            """)
        st.divider()
        st.markdown(
            "**🔗 바로가기**\n\n"
            "[💬 챗봇 (8502)](http://localhost:8502)  \n"
            "[🏥 대시보드 (8501)](http://localhost:8501)  \n"
            "[💰 재무 (8503)](http://localhost:8503)  \n"
            "[📁 구글 드라이브](https://drive.google.com/drive/folders/13hG8qM8iQsovKBI4a_3X6SjkO0UUU8sL)"
        )
        st.divider()
        if st.button("🔄 새로고침", use_container_width=True):
            _cc(); st.rerun()
        st.markdown(
            '<div style="text-align:center;font-size:10px;color:rgba(255,255,255,.18);margin-top:12px">'
            'RAG CMS v2.2 | 좋은문화병원 시스템팀</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    _apply_styles()

    st.markdown(
        """<div class="cms-hdr">
          <div style="font-size:30px;filter:drop-shadow(0 1px 3px rgba(0,0,0,.2))">🗄️</div>
          <div>
            <h1>RAG CMS — 지식 관리 시스템</h1>
            <p>좋은문화병원 가이드봇 &nbsp;|&nbsp; 문서 업로드 · 버전 관리 · 청크 CRUD · 벡터 인덱싱 · 검색 테스트</p>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    if not _CMS_OK:
        st.error(f"❌ CMS 서비스 로드 실패\n\n오류: `{_CMS_ERR}`")
        st.info("`services/cms_service.py`를 `services/` 폴더에 복사 후 재실행하세요.")
        return

    try:
        cms = _get_cms()
        vec = _get_vec()
    except Exception as e:
        st.error(f"서비스 초기화 실패: {e}"); return

    _sidebar(cms, vec)

    tabs = st.tabs([
        "📊 대시보드",
        "📋 문서 목록",
        "📄 문서 상세",
        "📤 파일 업로드",
        "🔍 검색 테스트",
        "🔧 관리 도구",
        "📋 로그 관리",
    ])

    with tabs[0]: _tab_dashboard(cms, vec)
    with tabs[1]: _tab_doclist(cms, vec)
    with tabs[2]: _tab_detail(cms)
    with tabs[3]: _tab_upload(cms)
    with tabs[4]: _tab_search(cms, vec)
    with tabs[5]: _tab_tools(cms, vec)
    with tabs[6]: _tab_logs(cms)


if __name__ == "__main__":
    main()