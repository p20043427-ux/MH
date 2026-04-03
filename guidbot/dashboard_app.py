"""
dashboard_app.py  ─  좋은문화병원 병동 현황 대시보드 v3.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v3.0 변경]
  · 관리자 로그인 → 📊 모니터링 탭 표시
  · 사용자 활동 로그 / AI 응답 시간 / 버튼 클릭 순위 확인 가능
  · 사이드바에 관리자 패널 추가

[v2.0 운영 개선]
  · 포트 정정: 대시보드 8501 / 챗봇 8502
  · 헬스체크 엔드포인트 추가 (?health=1)
  · Windows 프로세스 우선순위 ABOVE_NORMAL 설정
  · 사이드바 시스템 모니터링 (RAM/CPU, psutil 설치 시)
"""

from __future__ import annotations

import sys
import time
import json
from pathlib import Path

import streamlit as st

from config.settings import settings
from ui.theme import UITheme as T
from ui.hospital_dashboard import render_hospital_dashboard
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
    page_title="병동 현황 대시보드 | 좋은문화병원",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(T.get_global_css(), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# 헬스체크 엔드포인트 (?health=1)
# ══════════════════════════════════════════════════════════════════════
_health_param = st.query_params.get("health", "")
if _health_param == "1":
    _health: dict = {"status": "ok", "ts": time.time(), "oracle": False}
    try:
        from db.oracle_client import test_connection
        _ok, _msg = test_connection()
        _health["oracle"] = _ok
        _health["oracle_msg"] = _msg
    except Exception as _e:
        _health["oracle_msg"] = str(_e)
    try:
        import psutil
        _proc = psutil.Process()
        _mem  = _proc.memory_info()
        _health["memory_rss_mb"]          = round(_mem.rss / 1024 / 1024, 1)
        _sys_mem = psutil.virtual_memory()
        _health["system_memory_pct"]      = _sys_mem.percent
        _health["system_memory_avail_gb"] = round(_sys_mem.available / 1024**3, 1)
    except ImportError:
        _health["psutil"] = "not installed"
    st.json(_health)
    st.stop()


# ══════════════════════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# PDF → Markdown → 벡터DB 업로드 함수
# ══════════════════════════════════════════════════════════════════════
def _do_pdf_upload(
    uploaded_files,
    chunk_size: int = 800,
    overlap: int = 150,
) -> None:
    """
    업로드된 PDF 파일들을 Markdown으로 변환 후 벡터DB에 증분 추가.

    [처리 순서]
    1. 임시 디렉토리에 PDF 저장
    2. pdf_to_markdown()으로 Markdown 변환
    3. MarkdownHeaderTextSplitter로 청킹
    4. VectorStoreManager.append()로 기존 DB에 증분 추가
    5. 결과 및 오류 Streamlit에 표시

    [관리자 전용]
    RAG_READONLY 정책: 벡터DB 추가는 별도 파일 경로에 저장.
    Oracle 쓰기 없음.
    """
    import tempfile, shutil
    from pathlib import Path

    _prog = st.progress(0, text="준비 중...")
    _log  = st.empty()
    _total = len(uploaded_files)
    _success, _fail = 0, []

    all_docs = []

    for _i, _f in enumerate(uploaded_files):
        _prog.progress(int(_i / _total * 70), text=f"변환 중: {_f.name}")
        _log.info(f"처리 중: {_f.name}")

        try:
            with tempfile.TemporaryDirectory() as _tmp:
                _tmp_path = Path(_tmp)
                _pdf_path = _tmp_path / _f.name

                # PDF 임시 저장
                _pdf_bytes = _f.getbuffer().tobytes()
                with open(_pdf_path, "wb") as _fp:
                    _fp.write(_pdf_bytes)
                # ── 세션에 bytes 저장 → 파일 없어도 다운로드 활성화 ──
                if "_uploaded_pdf_bytes" not in st.session_state:
                    st.session_state["_uploaded_pdf_bytes"] = {}
                st.session_state["_uploaded_pdf_bytes"][_f.name] = _pdf_bytes
                # local_work_dir에도 복사 (향후 재시작 시 지속)
                try:
                    from config.settings import settings as _cfg_p
                    import shutil as _sh
                    _work_copy = _cfg_p.local_work_dir / _f.name
                    _cfg_p.local_work_dir.mkdir(parents=True, exist_ok=True)
                    _sh.copy2(str(_pdf_path), str(_work_copy))
                except Exception as _cp_e:
                    logger.debug(f"PDF 복사 실패: {_cp_e}")

                # Markdown 변환 + 청킹
                from core.pdf_to_markdown import pdf_to_markdown
                # langchain v0.2+ 패키지 분리 → langchain_text_splitters
                from langchain_text_splitters import (
                    MarkdownHeaderTextSplitter,
                    RecursiveCharacterTextSplitter,
                )
                from langchain_core.documents import Document as _Doc

                _md_text = pdf_to_markdown(_pdf_path, save_md=False)

                # Markdown 헤더 기반 청킹
                _headers = [("#", "h1"), ("##", "h2"), ("###", "h3")]
                _md_splitter = MarkdownHeaderTextSplitter(
                    headers_to_split_on=_headers, strip_headers=False
                )
                _md_chunks = _md_splitter.split_text(_md_text)

                # 청크 크기 초과 시 추가 분할
                _char_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=overlap,
                    separators=["\n\n", "\n", " ", ""],
                )
                _chunks = _char_splitter.split_documents(_md_chunks)

                # 메타데이터 추가
                for _c in _chunks:
                    _c.metadata["source"]   = _f.name
                    _c.metadata["type"]     = "uploaded_pdf"
                    _c.metadata["uploaded"] = time.strftime("%Y-%m-%d %H:%M")

                all_docs.extend(_chunks)
                _success += 1
                logger.info(f"PDF 변환 완료: {_f.name} → {len(_chunks)}청크")

        except Exception as _e:
            _fail.append((_f.name, str(_e)))
            logger.error(f"PDF 변환 실패: {_f.name} — {_e}")

    # 벡터DB에 추가
    if all_docs:
        _prog.progress(80, text="벡터DB 추가 중...")
        try:
            from core.vector_store import VectorStoreManager
            from config.settings import settings as _cfg

            # VectorStoreManager는 내부에서 get_embeddings_auto()로 임베딩 자동 처리
            _vsm = VectorStoreManager(
                db_path=_cfg.rag_db_path,
                model_name=_cfg.embedding_model,
                cache_dir=str(_cfg.local_cache_path),
            )
            _ok  = _vsm.append(all_docs)

            if _ok:
                _prog.progress(100, text="완료!")
                # ── 캐시 무효화: 채팅이 새 DB 재로드하도록 버전 증가
                import streamlit as _st_inv
                _st_inv.session_state["dash_vdb_version"] = (
                    _st_inv.session_state.get("dash_vdb_version", 0) + 1
                )
                _st_inv.session_state.pop("_vector_db_cached", None)  # 즉시 제거
                _st_inv.session_state.pop("_vdb_cached_ver", None)
                # RAGPipeline 싱글턴도 리셋 (새 DB 반영)
                try:
                    from core.rag_pipeline import reset_pipeline
                    reset_pipeline()
                except Exception:
                    pass
                st.success(
                    f"✅ {_success}개 파일 ({len(all_docs):,}청크) 벡터DB 추가 완료\n\n"
                    + ("\n".join(f"• {n}: {e}" for n, e in _fail) if _fail else "")
                )
                logger.info(f"벡터DB 증분 업데이트: {len(all_docs)}청크 추가")
            else:
                st.error("벡터DB 추가 실패 — 로그 확인")
        except Exception as _ve:
            st.error(f"벡터DB 오류: {_ve}")
            logger.error(f"벡터DB 업로드 실패: {_ve}", exc_info=True)
    else:
        _prog.progress(100, text="변환 실패")
        st.error("변환된 문서가 없습니다. 오류: " + str(_fail))

    _log.empty()
    if _fail and all_docs:
        st.warning("일부 파일 실패:\n" + "\n".join(f"• {n}: {e}" for n, e in _fail))



# ══════════════════════════════════════════════════════════════════════
# 벡터DB 문서 목록 뷰어
# ══════════════════════════════════════════════════════════════════════
def _render_vdb_doc_list() -> None:
    """
    벡터DB에 색인된 문서 목록 표시.
    ranked_docs의 metadata["source"]를 집계해 파일별 청크 수, 최신 업로드일 표시.
    """
    st.markdown("### 📚 벡터DB 등록 문서 목록")
    try:
        from core.vector_store import VectorStoreManager
        from config.settings import settings as _cfg
        _vsm = VectorStoreManager(
            db_path=_cfg.rag_db_path,
            model_name=_cfg.embedding_model,
            cache_dir=str(_cfg.local_cache_path),
        )
        _vdb = _vsm.load()
        if _vdb is None:
            st.warning("벡터DB가 없습니다. build_db.py를 먼저 실행하세요.")
            return

        # FAISS docstore에서 모든 문서 메타데이터 수집
        _docs = list(_vdb.docstore._dict.values())
        total_chunks = len(_docs)

        # 파일별 집계
        from collections import defaultdict
        _file_stats: dict = defaultdict(lambda: {"chunks": 0, "uploaded": "", "type": ""})
        for _d in _docs:
            _meta = getattr(_d, "metadata", {})
            _src  = _meta.get("source", "알 수 없음")
            _file_stats[_src]["chunks"] += 1
            _up   = _meta.get("uploaded", "")
            if _up and _up > _file_stats[_src]["uploaded"]:
                _file_stats[_src]["uploaded"] = _up
            if not _file_stats[_src]["type"]:
                _file_stats[_src]["type"] = _meta.get("type", "규정집")

        # KPI
        c1, c2, c3 = st.columns(3)
        c1.metric("총 문서 수", f"{len(_file_stats)}개")
        c2.metric("총 청크 수", f"{total_chunks:,}개")
        c3.metric("벡터 수",    f"{_vdb.index.ntotal:,}개")
        st.divider()

        # 필터
        _search = st.text_input("🔍 파일명 검색", placeholder="파일명 일부 입력...", key="vdb_search")
        _sort   = st.selectbox("정렬", ["파일명 오름차순", "청크 수 내림차순", "업로드일 내림차순"],
                               key="vdb_sort", label_visibility="collapsed")

        _rows = sorted(
            [(src, stat) for src, stat in _file_stats.items()
             if not _search or _search.lower() in src.lower()],
            key=lambda x: (
                x[0].lower() if "파일명" in _sort else
                -x[1]["chunks"] if "청크" in _sort else
                -(x[1]["uploaded"] or "")
            )
        )

        if not _rows:
            st.info("검색 결과가 없습니다.")
            return

        # 테이블 헤더
        _TH = "padding:6px 10px;font-size:11px;font-weight:700;color:#64748B;border-bottom:2px solid #E2E8F0;background:#F8FAFC;"
        _tbl = (
            '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            '<thead><tr>'
            f'<th style="{_TH}text-align:left;">#</th>'
            f'<th style="{_TH}text-align:left;">파일명</th>'
            f'<th style="{_TH}text-align:center;">유형</th>'
            f'<th style="{_TH}text-align:right;">청크</th>'
            f'<th style="{_TH}text-align:center;">업로드일</th>'
            '</tr></thead><tbody>'
        )
        _TYPE_COLOR = {
            "uploaded_pdf": "#7C3AED",
            "db_manual":    "#0891B2",
            "규정집":        "#1E40AF",
        }
        for _i, (_src, _stat) in enumerate(_rows):
            _bg = "#F8FAFC" if _i % 2 == 0 else "#FFFFFF"
            _td = f"padding:5px 10px;background:{_bg};border-bottom:1px solid #F1F5F9;"
            _tc = _TYPE_COLOR.get(_stat["type"], "#64748B")
            _type_lbl = {"uploaded_pdf":"업로드","db_manual":"DB명세","규정집":"규정집"}.get(_stat["type"], _stat["type"])
            _tbl += (
                f"<tr>"
                f'<td style="{_td}color:#94A3B8;">{_i+1}</td>'
                f'<td style="{_td}font-weight:600;color:#0F172A;word-break:break-all;">{_src}</td>'
                f'<td style="{_td}text-align:center;">'
                f'<span style="background:{_tc}22;color:{_tc};border-radius:4px;'
                f'padding:1px 6px;font-size:10px;font-weight:700;">{_type_lbl}</span></td>'
                f'<td style="{_td}text-align:right;font-family:Consolas,monospace;color:#1E40AF;">{_stat["chunks"]}</td>'
                f'<td style="{_td}text-align:center;color:#64748B;">{_stat["uploaded"] or "─"}</td>'
                f"</tr>"
            )
        _tbl += "</tbody></table>"
        st.markdown(f'<div style="overflow-x:auto;">{_tbl}</div>', unsafe_allow_html=True)

        # CSV 다운로드
        import io, csv
        _buf = io.StringIO()
        _w = csv.writer(_buf)
        _w.writerow(["파일명","유형","청크수","업로드일"])
        for _src, _stat in _rows:
            _w.writerow([_src, _stat["type"], _stat["chunks"], _stat["uploaded"]])
        st.download_button("📥 목록 CSV 다운로드", _buf.getvalue().encode("utf-8-sig"),
                           "vdb_docs.csv", "text/csv", key="vdb_csv_dl")

    except Exception as _e:
        st.error(f"벡터DB 목록 조회 실패: {_e}")

def _render_mini_sidebar() -> str:
    """
    대시보드 사이드바.
    - Oracle 상태 표시
    - AI 챗봇 이동 링크
    - 시스템 모니터링 (psutil)
    - 관리자 로그인 패널
    반환: 현재 role ("user" | "admin")
    """
    with st.sidebar:
        # ── 병원 로고 ──────────────────────────────────────────────────
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

        # ── Oracle 연결 상태 ───────────────────────────────────────────
        if "dash_oracle_ok" not in st.session_state:
            _ok = False
            try:
                from db.oracle_client import test_connection
                _ok, _ = test_connection()
            except Exception:
                pass
            st.session_state["dash_oracle_ok"] = _ok

        _oracle_ok = st.session_state.get("dash_oracle_ok", False)
        _oc_bg  = "rgba(22,163,74,0.15)"  if _oracle_ok else "rgba(245,158,11,0.15)"
        _oc_bd  = "rgba(22,163,74,0.3)"   if _oracle_ok else "rgba(245,158,11,0.3)"
        _oc_dot = "#16A34A"  if _oracle_ok else "#F59E0B"
        _oc_lbl = "Oracle 연결 정상" if _oracle_ok else "Oracle 미연결"
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

        # ── 마지막 갱신 시각 ───────────────────────────────────────────
        _last_ts = st.session_state.get("dash_last_ts", time.strftime("%Y-%m-%d %H:%M"))
        st.markdown(
            f'<div style="font-size:11px;color:rgba(255,255,255,0.45);'
            f'margin-bottom:16px;">마지막 갱신: {_last_ts}</div>',
            unsafe_allow_html=True,
        )

        # ── AI 챗봇 이동 링크 ─────────────────────────────────────────
        st.markdown(
            '<div style="margin-bottom:16px;">'
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

        # ── 부서별 Google Docs 문서 링크 ────────────────────────────
        st.markdown(
            '<div style="font-size:10px;font-weight:700;color:rgba(255,255,255,0.50);'
            'text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;'
            '">📄 부서 문서</div>',
            unsafe_allow_html=True,
        )
        _DEPT_DOCS = [
            ("🏥", "병동",    "https://docs.google.com/document/d/병동문서ID"),
            ("🚨", "응급실",  "https://docs.google.com/document/d/응급실문서ID"),
            ("💊", "중환자실","https://drive.google.com/file/d/1f0R6rpr5Te45W8wlIuGZAcx2m-nT7FGV/view?usp=sharing"),
            ("👶", "분만실",  "https://docs.google.com/document/d/분만실문서ID"),
            ("🍼", "NICU",    "https://docs.google.com/document/d/NICU문서ID"),
            ("🔪", "수술실",  "https://docs.google.com/document/d/수술실문서ID"),
            ("👩‍⚕️", "간호부", "https://docs.google.com/document/d/간호부문서ID"),
        ]
        _doc_rows = ""
        for _ico, _nm, _url in _DEPT_DOCS:
            _doc_rows += (
                f'<a href="{_url}" target="_blank" style="'
                "display:flex;align-items:center;gap:8px;"
                "background:rgba(255,255,255,0.05);"
                "border:1px solid rgba(255,255,255,0.10);"
                "border-radius:7px;padding:7px 10px;"
                'text-decoration:none;margin-bottom:4px;">'
                f'<span style="font-size:13px;">{_ico}</span>'
                '<div style="flex:1;">'
                f'<span style="font-size:12px;font-weight:600;'
                f'color:rgba(255,255,255,0.85);">{_nm}</span>'
                '</div>'
                '<span style="font-size:10px;'
                'color:rgba(255,255,255,0.30);">↗</span>'
                '</a>'
            )
        st.markdown(
            f'<div style="display:flex;flex-direction:column;'
            f'gap:0;margin-bottom:14px;">{_doc_rows}</div>',
            unsafe_allow_html=True,
        )

        # ── 시스템 모니터링 (psutil) ──────────────────────────────────
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

        # ── 관리자 로그인 패널 ────────────────────────────────────────
        _role: str = st.session_state.get("dash_role", "user")

        with st.expander("🔐 관리자", expanded=(_role == "admin")):
            if _role == "admin":
                # 로그인 상태
                st.markdown(
                    '<div style="font-size:11px;font-weight:700;'
                    'color:#4ADE80;margin-bottom:8px;">✓ 관리자 인증 완료</div>',
                    unsafe_allow_html=True,
                )
                if st.button("로그아웃", key="dash_admin_logout",
                             use_container_width=True):
                    st.session_state["dash_role"] = "user"
                    logger.info("대시보드 관리자 로그아웃")
                    st.rerun()
            else:
                # 로그인 폼
                # 사이드바 input 색상 강제: 다크 배경에서 흰 텍스트 → 검정
                st.markdown("""
                    <style>
                    section[data-testid='stSidebar'] input[type='password'],
                    section[data-testid='stSidebar'] input[type='text'] {
                        color: #0F172A !important;
                        background-color: #FFFFFF !important;
                        border: 1.5px solid #CBD5E1 !important;
                    }
                    section[data-testid='stSidebar'] input::placeholder {
                        color: #94A3B8 !important;
                    }
                    </style>""", unsafe_allow_html=True)
                _pw = st.text_input(
                    "패스워드", type="password",
                    key="dash_admin_pw",
                    placeholder="관리자 패스워드 입력",
                    label_visibility="collapsed",
                )
                if _pw:
                    try:
                        # main.py 와 동일한 settings.check_admin 사용
                        if settings.check_admin(_pw):
                            st.session_state["dash_role"] = "admin"
                            logger.info("대시보드 관리자 인증 성공")
                            st.rerun()
                        else:
                            st.markdown(
                                '<div style="font-size:11px;color:#EF4444;'
                                'font-weight:600;margin-top:4px;">'
                                '패스워드가 올바르지 않습니다</div>',
                                unsafe_allow_html=True,
                            )
                            logger.warning("대시보드 관리자 인증 실패")
                    except Exception as _auth_e:
                        st.error(f"인증 오류: {_auth_e}")


        st.divider()

        # ── PDF → 벡터DB 업로드 (관리자만) ─────────────────────
        if _role == "admin":
            with st.expander("📄 규정집 PDF 업로드", expanded=False):
                # ── 사이드바 다크 배경 색상 강제 적용 CSS ─────────
                st.markdown("""
                    <style>
                    section[data-testid='stSidebar'] p,
                    section[data-testid='stSidebar'] label,
                    section[data-testid='stSidebar'] span,
                    section[data-testid='stSidebar'] div[data-testid='stMarkdownContainer'] {
                        color: #E2E8F0 !important;
                    }
                    section[data-testid='stSidebar'] .stSlider > label {
                        color: #CBD5E1 !important;
                        font-size: 12px !important;
                        font-weight: 600 !important;
                    }
                    </style>""", unsafe_allow_html=True)

                # Google Drive 공유 폴더 바로가기
                st.markdown(
                    '<a href="https://drive.google.com/drive/folders/13hG8qM8iQsovKBI4a_3X6SjkO0UUU8sL"'
                    ' target="_blank" style="'
                    'display:flex;align-items:center;gap:6px;'
                    'background:rgba(66,133,244,0.15);border:1px solid rgba(66,133,244,0.35);'
                    'border-radius:7px;padding:6px 10px;text-decoration:none;margin-bottom:6px;">'
                    '<span style="font-size:14px;">📁</span>'
                    '<div>'
                    '<div style="font-size:11px;font-weight:600;color:rgba(255,255,255,0.88);">벡터DB 공유 폴더</div>'
                    '<div style="font-size:10px;color:rgba(255,255,255,0.45);">Google Drive ↗</div>'
                    '</div></a>',
                    unsafe_allow_html=True,
                )
                st.caption("PDF → Markdown 변환 후 벡터DB에 추가합니다.")
                _up_files = st.file_uploader(
                    "📂 PDF 파일 선택 (복수 가능)",
                    type=["pdf"],
                    accept_multiple_files=True,
                    key="dash_pdf_upload",
                )
                _chunk_size = st.select_slider(
                    "📐 청크 크기",
                    options=[400, 500, 600, 700, 800, 900, 1000, 1200],
                    value=st.session_state.get("dash_chunk_size", 800),
                    key="dash_chunk_size",
                )
                _overlap = st.select_slider(
                    "🔗 청크 중복",
                    options=[0, 50, 100, 150, 200, 250, 300],
                    value=st.session_state.get("dash_overlap", 150),
                    key="dash_overlap",
                )
                if _up_files:
                    st.success(f"{len(_up_files)}개 파일 선택됨")
                    if st.button(
                        f"🚀 벡터DB 추가 ({len(_up_files)}개)",
                        key="dash_upload_btn",
                        use_container_width=True,
                        type="primary",
                    ):
                        _do_pdf_upload(
                            _up_files,
                            chunk_size=_chunk_size,
                            overlap=_overlap,
                        )
                else:
                    st.info("PDF 파일을 선택하면 추가 버튼이 나타납니다.")

        # ── 버전 ──────────────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:10px;color:rgba(255,255,255,0.25);'
            'text-align:center;padding-top:12px;">'
            "병동 대시보드 v3.0<br>좋은문화병원 통계과"
            "</div>",
            unsafe_allow_html=True,
        )

    return st.session_state.get("dash_role", "user")


# ══════════════════════════════════════════════════════════════════════
# 메인 함수
# ══════════════════════════════════════════════════════════════════════
def main() -> None:
    """
    dashboard_app.py 진입점 v3.0

    [화면 구성]
    · 일반 유저: 병동 대시보드 탭 1개
    · 관리자:   병동 대시보드 + 📊 모니터링 탭 2개

    [모니터링 탭 내용]
    · 사용자 액션 집계 (빠른 분석 클릭 순위, 병동 필터 사용)
    · AI 채팅 질문 수 / LLM 오류율 / 평균 응답시간
    · DB 쿼리 실패 목록
    · 최근 이벤트 로그 테이블 (필터·다운로드 가능)
    """
    logger.info("dashboard_app v3.0 시작 — 병동 대시보드 (포트 8501)")

    # 사이드바 렌더 → 현재 role 반환
    current_role = _render_mini_sidebar()

    # 갱신 시각 초기화
    if "dash_last_ts" not in st.session_state:
        st.session_state["dash_last_ts"] = time.strftime("%Y-%m-%d %H:%M")

    # ── 탭 구성 ─────────────────────────────────────────────────────
    if current_role == "admin":
        # 관리자: 병동 대시보드 + 모니터링
        tab_dash, tab_mon, tab_docs = st.tabs(["🏥 병동 대시보드", "📊 모니터링", "📚 벡터DB 문서"])

        with tab_dash:
            try:
                render_hospital_dashboard(tab="ward")
            except Exception as e:
                st.error(
                    f"대시보드 로드 오류\n\n{e}\n\n"
                    f"Oracle 연결 상태를 확인하거나 관리자에게 문의하세요."
                )
                logger.error(f"dashboard_app 렌더 오류: {e}", exc_info=True)

        with tab_mon:
            # ── 사용자 활동 모니터링 뷰어 ──────────────────────────
            try:
                from ui.dashboard_log_viewer import render_dashboard_monitor
                render_dashboard_monitor()
            except ImportError:
                st.error(
                    "모니터링 모듈을 찾을 수 없습니다.\n\n"
                    "`ui/dashboard_log_viewer.py` 가 배포됐는지 확인하세요."
                )
            except Exception as _me:
                st.error(f"모니터링 뷰어 오류: {_me}")
                logger.error(f"모니터링 뷰어 오류: {_me}", exc_info=True)

        with tab_docs:
            _render_vdb_doc_list()

    else:
        # 일반 유저: 탭 없이 바로 병동 대시보드
        try:
            render_hospital_dashboard(tab="ward")
        except Exception as e:
            st.error(
                f"대시보드 로드 오류\n\n{e}\n\n"
                f"Oracle 연결 상태를 확인하거나 관리자에게 문의하세요."
            )
            logger.error(f"dashboard_app 렌더 오류: {e}", exc_info=True)


if __name__ == "__main__":
    main()