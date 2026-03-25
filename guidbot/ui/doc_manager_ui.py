"""
ui/doc_manager_ui.py ─ 문서 관리 Streamlit UI v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[기능]
  · 탭1: 문서 업로드 (드래그&드롭, 카테고리/태그 설정)
  · 탭2: 등록 문서 목록 (검색/필터/다운로드/비활성화)
  · 탭3: 벡터 인덱스 관리 (미반영 현황, 재구축 버튼)

[보안]
  · 관리자 인증 통과한 경우에만 렌더링
  · 파일 확장자/크기 제한 적용
  · 업로더 식별자 기록
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

from config.settings import settings
from utils.logger import get_logger
from db.doc_manager import (
    DocCategory,
    DocManager,
    DocMeta,
    get_doc_manager,
)

logger = get_logger(__name__, log_dir=settings.log_dir)

# ── 허용 파일 형식 ─────────────────────────────────────────────
_ALLOWED_EXTENSIONS: Dict[str, str] = {
    ".pdf": "PDF 문서",
    ".docx": "Word 문서",
    ".xlsx": "Excel 명세서",
    ".md": "Markdown 문서",
    ".sql": "SQL 쿼리 파일",
    ".txt": "텍스트 파일",
}
_MAX_FILE_MB = 50  # 최대 업로드 크기

# ── 카테고리 표시 레이블 ───────────────────────────────────────
_CAT_LABELS = {
    DocCategory.REGULATION: "📋 업무 규정집",
    DocCategory.DB_SPEC: "🗄️ DB 테이블 명세서",
    DocCategory.QUERY_LIB: "🔍 SQL 쿼리 예제집",
    DocCategory.SYSTEM_GUIDE: "📖 시스템 안내서",
    DocCategory.OTHER: "📁 기타",
}


def render_doc_manager_ui(admin_user: str = "admin") -> None:
    """
    문서 관리 UI 진입점.

    관리자 화면의 문서 관리 탭에서 호출합니다.

    Args:
        admin_user: 현재 로그인한 관리자 식별자
    """
    mgr = get_doc_manager()
    stats = mgr.get_stats()

    # ── 현황 요약 배너 ─────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📄 총 문서", f"{stats['total']}건")
    col2.metric(
        "⏳ 인덱스 대기",
        f"{stats['unindexed']}건",
        delta="재구축 필요" if stats["unindexed"] > 0 else None,
        delta_color="inverse",
    )
    col3.metric("💾 저장 용량", f"{stats['total_size_mb']} MB")
    by_cat = stats.get("by_category", {})
    col4.metric("📂 카테고리", f"{len(by_cat)}종")

    st.divider()

    tab_upload, tab_list, tab_index = st.tabs(
        [
            "📤 문서 업로드",
            "📂 문서 목록",
            "⚙️ 인덱스 관리",
        ]
    )

    with tab_upload:
        _render_upload_tab(mgr, admin_user)

    with tab_list:
        _render_list_tab(mgr)

    with tab_index:
        _render_index_tab(mgr)


# ── 탭1: 업로드 ────────────────────────────────────────────────


def _render_upload_tab(mgr: DocManager, admin_user: str) -> None:
    """문서 업로드 탭."""
    st.subheader("문서 업로드")
    st.caption("업무 규정집, 테이블 명세서, SQL 쿼리 예제를 등록합니다.")

    with st.form("doc_upload_form", clear_on_submit=True):
        # 파일 선택
        uploaded_file = st.file_uploader(
            "파일 선택",
            type=list(e.lstrip(".") for e in _ALLOWED_EXTENSIONS),
            help=f"허용 형식: {', '.join(_ALLOWED_EXTENSIONS.keys())} / 최대 {_MAX_FILE_MB}MB",
        )

        col_l, col_r = st.columns(2)
        with col_l:
            title = st.text_input(
                "📌 문서 제목 *",
                placeholder="예) 응급실 업무규정 2024, EMIHPTMI 테이블 명세서",
            )
            category = st.selectbox(
                "📂 카테고리 *",
                options=list(_CAT_LABELS.keys()),
                format_func=lambda c: _CAT_LABELS[c],
            )

        with col_r:
            description = st.text_area(
                "📝 문서 설명",
                placeholder="이 문서의 내용과 용도를 간략히 설명하세요.",
                height=100,
            )
            tags_input = st.text_input(
                "🏷️ 태그 (쉼표 구분)",
                placeholder="응급실, EMIHPTMI, 응급환자, 중증도",
                help="쿼리/검색 시 태그로 관련 문서를 빠르게 찾을 수 있습니다.",
            )

        # 카테고리별 안내
        _hints = {
            DocCategory.REGULATION: "💡 규정집 PDF/DOCX → RAG 검색 인덱스에 추가됩니다.",
            DocCategory.DB_SPEC: "💡 테이블 명세서 → SQL 생성 정확도 향상에 활용됩니다.",
            DocCategory.QUERY_LIB: "💡 SQL 예제 파일 → 유사 질문 시 예제 자동 추천됩니다.",
            DocCategory.SYSTEM_GUIDE: "💡 안내서 → 직원 질문 답변에 활용됩니다.",
            DocCategory.OTHER: "",
        }
        if hint := _hints.get(category, ""):
            st.info(hint)

        submitted = st.form_submit_button(
            "📤 업로드 등록",
            type="primary",
            use_container_width=True,
        )

        if submitted:
            if not uploaded_file:
                st.error("❌ 파일을 선택해주세요.")
                return
            if not title.strip():
                st.error("❌ 문서 제목을 입력해주세요.")
                return

            file_data = uploaded_file.read()
            file_mb = len(file_data) / 1024 / 1024

            if file_mb > _MAX_FILE_MB:
                st.error(f"❌ 파일 크기 초과: {file_mb:.1f}MB (최대 {_MAX_FILE_MB}MB)")
                return

            ext = Path(uploaded_file.name).suffix.lower()
            if ext not in _ALLOWED_EXTENSIONS:
                st.error(f"❌ 허용되지 않는 파일 형식: {ext}")
                return

            tags = [t.strip() for t in tags_input.split(",") if t.strip()]

            with st.spinner("업로드 중..."):
                result = mgr.upload(
                    file_data=file_data,
                    file_name=uploaded_file.name,
                    title=title.strip(),
                    category=category,
                    tags=tags,
                    description=description.strip(),
                    uploaded_by=admin_user,
                )

            if result.duplicate:
                st.warning(f"⚠️ {result.message}")
                if st.button("강제 재등록", key="force_reupload"):
                    result2 = mgr.upload(
                        file_data=file_data,
                        file_name=uploaded_file.name,
                        title=title.strip(),
                        category=category,
                        tags=tags,
                        description=description.strip(),
                        uploaded_by=admin_user,
                        force_update=True,
                    )
                    if result2.success:
                        st.success(f"✅ {result2.message}")
                        st.rerun()
            elif result.success:
                st.success(f"✅ {result.message}")
                if mgr.needs_reindex(category):
                    st.info(
                        f"📌 '⚙️ 인덱스 관리' 탭에서 벡터 인덱스를 재구축하면 "
                        f"검색에 즉시 반영됩니다."
                    )
                st.rerun()
            else:
                st.error(f"❌ {result.message}")


# ── 탭2: 문서 목록 ─────────────────────────────────────────────


def _render_list_tab(mgr: DocManager) -> None:
    """문서 목록 탭."""
    st.subheader("등록 문서 목록")

    # 필터
    col_f1, col_f2, col_f3 = st.columns([2, 2, 3])
    with col_f1:
        filter_cat = st.selectbox(
            "카테고리 필터",
            options=["전체"] + list(_CAT_LABELS.keys()),
            format_func=lambda c: "전체" if c == "전체" else _CAT_LABELS[c],
        )
    with col_f2:
        show_inactive = st.checkbox("비활성 포함", value=False)
    with col_f3:
        search_kw = st.text_input("제목/설명 검색", placeholder="검색어 입력...")

    cat_param = "" if filter_cat == "전체" else filter_cat
    docs = mgr.list_docs(
        category=cat_param,
        active_only=not show_inactive,
        search=search_kw,
    )

    if not docs:
        st.info("등록된 문서가 없거나 검색 조건에 해당하는 문서가 없습니다.")
        return

    st.caption(f"총 {len(docs)}건")

    # 문서 목록 테이블
    for doc in docs:
        _render_doc_row(mgr, doc)


def _render_doc_row(mgr: DocManager, doc: DocMeta) -> None:
    """단일 문서 행 렌더링."""
    cat_label = _CAT_LABELS.get(doc.category, doc.category)
    indexed_badge = "✅ 인덱스됨" if doc.vector_indexed else "⏳ 대기"
    active_badge = "" if doc.is_active else " 🔴 비활성"
    tags_str = " ".join(f"`{t}`" for t in doc.tags) if doc.tags else "-"

    with st.expander(
        f"{cat_label}  **{doc.title}**  v{doc.version}"
        f"  {indexed_badge}{active_badge}  "
        f"│ {doc.file_name}  ({doc.file_size / 1024:.0f}KB)"
    ):
        col_i, col_a = st.columns([3, 1])
        with col_i:
            st.markdown(f"**설명**: {doc.description or '(없음)'}")
            st.markdown(f"**태그**: {tags_str}")
            st.markdown(
                f"**등록**: {doc.created_at[:19]}  │  "
                f"**업로더**: {doc.uploaded_by}  │  "
                f"**ID**: `{doc.doc_id}`"
            )
            if not doc.vector_indexed:
                st.warning(
                    "⚠️ 아직 벡터 인덱스에 반영되지 않았습니다. '인덱스 관리' 탭에서 재구축하세요."
                )

        with col_a:
            # 다운로드 버튼
            file_path = mgr.get_file_path(doc.doc_id)
            if file_path and file_path.exists():
                with open(file_path, "rb") as f:
                    st.download_button(
                        "⬇️ 다운로드",
                        data=f.read(),
                        file_name=doc.file_name,
                        key=f"dl_{doc.doc_id}",
                        use_container_width=True,
                    )

            # 비활성화 버튼
            if doc.is_active:
                if st.button(
                    "🗑️ 비활성화", key=f"deact_{doc.doc_id}", use_container_width=True
                ):
                    if mgr.deactivate(doc.doc_id, reason="관리자 UI"):
                        st.success("비활성화되었습니다.")
                        st.rerun()


# ── 탭3: 인덱스 관리 ───────────────────────────────────────────


def _render_index_tab(mgr: DocManager) -> None:
    """벡터 인덱스 관리 탭."""
    st.subheader("벡터 인덱스 관리")
    st.caption(
        "문서를 업로드한 후 RAG 검색에 반영하려면 벡터 인덱스를 재구축해야 합니다."
    )

    pending = mgr.get_pending_index_docs()

    if pending:
        st.warning(f"⚠️ {len(pending)}개 문서가 인덱스 재구축을 기다리고 있습니다.")
        for doc in pending:
            st.markdown(
                f"- {_CAT_LABELS.get(doc.category, '?')} **{doc.title}** v{doc.version}"
            )
    else:
        st.success("✅ 모든 문서가 최신 인덱스에 반영되어 있습니다.")

    st.divider()

    col_b1, col_b2, col_b3 = st.columns(3)

    with col_b1:
        if st.button(
            "📋 규정집 인덱스 재구축",
            use_container_width=True,
            disabled=not mgr.needs_reindex(DocCategory.REGULATION),
        ):
            _trigger_reindex(DocCategory.REGULATION, mgr)

    with col_b2:
        if st.button(
            "🗄️ 스키마 인덱스 재구축",
            use_container_width=True,
            disabled=not mgr.needs_reindex(DocCategory.DB_SPEC),
        ):
            _trigger_reindex(DocCategory.DB_SPEC, mgr)

    with col_b3:
        if st.button(
            "🔍 쿼리 예제 인덱스 재구축",
            use_container_width=True,
            disabled=not mgr.needs_reindex(DocCategory.QUERY_LIB),
        ):
            _trigger_reindex(DocCategory.QUERY_LIB, mgr)

    st.divider()
    if st.button(
        "🔄 전체 인덱스 재구축 (전체 문서)", type="secondary", use_container_width=True
    ):
        _trigger_reindex("all", mgr)


def _trigger_reindex(category: str, mgr: DocManager) -> None:
    """
    벡터 인덱스 재구축 트리거.

    [build_db.py 통합 방식]
    build_db.py 는 main() 함수만 노출하며 argparse 기반입니다.
    직접 import 하여 호출하거나, subprocess 로 실행합니다.
    카테고리별로 적절한 벡터 DB 를 재구축합니다:
      - regulation/system_guide/other → faiss_db (기본 RAG DB)
      - db_spec  → schema_db (스키마 벡터 DB)
      - query_library → query_db (쿼리 예제 DB)
    """
    from db.doc_manager import DocCategory

    _category_map = {
        DocCategory.REGULATION: "rag",
        DocCategory.SYSTEM_GUIDE: "rag",
        DocCategory.OTHER: "rag",
        DocCategory.DB_SPEC: "schema",
        DocCategory.QUERY_LIB: "query",
        "all": "all",
    }
    rebuild_target = _category_map.get(category, "rag")

    with st.spinner(f"인덱스 재구축 중... ({rebuild_target})"):
        try:
            doc_ids = [
                d.doc_id
                for d in mgr.get_pending_index_docs(
                    "" if category == "all" else category
                )
            ]

            if rebuild_target in ("rag", "all"):
                _rebuild_rag_index()
            if rebuild_target in ("schema", "all"):
                _rebuild_schema_index()
            if rebuild_target in ("query", "all"):
                _rebuild_query_index()

            mgr.mark_indexed(doc_ids)
            st.success(f"✅ 인덱스 재구축 완료 ({len(doc_ids)}개 문서 반영)")
            st.rerun()
        except Exception as exc:
            st.error(f"❌ 재구축 실패: {exc}")
            logger.error(f"인덱스 재구축 실패: {exc}", exc_info=True)


def _rebuild_rag_index() -> None:
    """규정집/일반 문서 FAISS 인덱스 재구축."""
    import argparse
    import sys

    # build_db.main() 을 직접 호출 (argparse Namespace 직접 생성)
    try:
        from build_db import main as build_main, parse_args

        ns = argparse.Namespace(
            source="docs",
            force=False,
            verbose=False,
        )
        # parse_args 시그니처에 맞게 필요 속성 추가
        for attr in ["source", "force", "verbose"]:
            if not hasattr(ns, attr):
                setattr(ns, attr, None)
        build_main(ns)
    except TypeError:
        # argparse Namespace 불일치 시 — build_db 의 기본값으로 실행
        import subprocess, sys

        subprocess.run(
            [sys.executable, "build_db.py"],
            check=True,
            cwd=str(Path(__file__).parent.parent),
        )


def _rebuild_schema_index() -> None:
    """DB 스키마 벡터 인덱스 재구축 (schema_oracle_loader)."""
    try:
        from db.schema_oracle_loader import build_schema_db_from_oracle

        build_schema_db_from_oracle(force=True)
        return
    except ImportError:
        pass
    # 폴백: schema_oracle_loader 모듈이 없으면 subprocess
    import subprocess, sys

    subprocess.run(
        [sys.executable, "-m", "db.schema_oracle_loader", "--force"],
        check=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _rebuild_query_index() -> None:
    """SQL 쿼리 예제 벡터 인덱스 재구축."""
    try:
        # query_db 구축 모듈이 있으면 사용
        from db.query_db_builder import build_query_db

        build_query_db(force=True)
    except ImportError:
        # 아직 구현 안 됨 — RAG DB 재구축으로 대체
        logger.warning("query_db_builder 미구현 — RAG 인덱스 재구축으로 대체")
        _rebuild_rag_index()
