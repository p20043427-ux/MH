"""
main.py  ─  좋은문화병원 가이드봇 v9.3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v9.2 — 병원 현황판 라우팅 추가]

■ 현황판 페이지 라우팅 신규
  · 사이드바 병동/원무/외래 대시보드 버튼 클릭
    → session_state["active_page"] = "hospital_dashboard"
    → session_state["dashboard_tab"] = "ward" | "finance" | "opd"
    → main() 재진입 시 render_hospital_dashboard() 전체 화면

[v9.1 유지]
  · SQL 대시보드 / 문서 관리 라우팅 (관리자 전용)
  · 데이터 분석 모드 (search_mode == "data_analysis")
  · 벤치마크 + 로그 탭
  · 피드백 시스템
"""

from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Optional

import streamlit as st
from langchain_community.vectorstores import FAISS

from config.settings import settings
from core.llm import get_llm_client
from core.search_engine import SearchResult, iter_search_steps
from core.vector_store import VectorStoreManager
from ui.components import (
    home_screen,
    source_trust_card,
    source_section_header,
    error_banner,
    tip_banner,
    page_header,
)
from ui.sidebar import render_sidebar, DBHealth
from ui.theme import UITheme as T

# v9.0: 데이터 분석 대시보드 UI
from ui.data_dashboard import render_data_analysis_tab
from utils.exceptions import GuidbotError, LLMQuotaError
from utils.feedback_store import (
    get_feedback_stats,
    load_all_feedback,
    save_feedback,
    export_as_training_data,
    get_negative_feedback_questions,
)
from utils.logger import get_logger, ContextLogger
from utils.monitor import get_metrics

logger = get_logger(__name__, log_dir=settings.log_dir)

_MAX_HISTORY = 15 


_TIPS: list[str] = [
    "원내 와이파이 · moonhwa_free · 별도 설정 불필요합니다",
    "병원 내 전 구역 금연입니다. 흡연은 지정 구역에서만 가능합니다.",
    "당직 수당 계산 기준이 궁금하시면 '당직 수당'이라고 입력해 보세요",
    "연차 신청 전 취업규칙을 확인해 보세요 — 챗봇에게 물어보세요!",
]

_BENCHMARK_QUERIES: list[str] = [
    "연차휴가 산정 기준이 어떻게 되나요?",
    "당직 근무 수당 계산 방법을 알려주세요",
    "출산 전후 휴가 기간은 얼마나 되나요?",
    "취업규칙 위반 시 징계 절차는 어떻게 되나요?",
    "병원 내 금연 구역은 어디인가요?",
]

_RENDER_INTERVAL_SEC = 0.05


st.set_page_config(
    page_title=settings.app_title,
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(T.get_global_css(), unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _load_resources():
    """벡터 DB 병렬 초기화 + BM25 백그라운드 워밍업 (v9.2)."""
    logger.info("리소스 초기화 시작 (v9.2 — 병렬 로딩)")
    try:
        from utils.startup_optimizer import (
            parallel_load_resources,
            start_background_warmup,
        )

        result = parallel_load_resources()
        if result.vector_db is not None:
            start_background_warmup(result.vector_db)
        return result.vector_db
    except Exception as exc:
        logger.warning(f"병렬 로딩 실패 → 기존 방식 폴백: {exc}")
        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        vector_db = manager.load()
        if vector_db is None:
            logger.warning("벡터 DB 없음")
            return None
        logger.info("벡터 DB 로드 완료")
        return vector_db


def _check_health(vector_db) -> DBHealth:
    """벡터 DB + 파일 시스템 상태 확인."""
    from datetime import datetime

    file_count, recent_files = 0, []
    try:
        pdf_files = sorted(
            settings.local_work_dir.glob("*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        file_count = len(pdf_files)
        recent_files = [
            (p.name, datetime.fromtimestamp(p.stat().st_mtime).strftime("%m/%d"))
            for p in pdf_files[:5]
        ]
    except Exception:
        pass

    if vector_db is None:
        return DBHealth(
            is_healthy=False,
            message="DB 오프라인",
            doc_count=0,
            file_count=file_count,
            recent_files=recent_files,
        )
    try:
        return DBHealth(
            is_healthy=True,
            message="정상 가동 중",
            doc_count=vector_db.index.ntotal,
            file_count=file_count,
            recent_files=recent_files,
        )
    except Exception:
        return DBHealth(
            is_healthy=True,
            message="정상 가동 중",
            doc_count=0,
            file_count=file_count,
            recent_files=recent_files,
        )


def _render_feedback_buttons(
    msg_idx: int,
    question: str,
    answer: str,
    mode: str,
    sources: list[dict],
) -> None:
    """답변 아래 피드백 버튼 렌더링."""
    state_key = f"feedback_{msg_idx}"
    current = st.session_state.get(state_key, None)

    source_strs: list[str] = []
    for s in sources:
        src = s.get("source", "")
        page = s.get("page", "")
        if src:
            source_strs.append(f"{src} p.{page}" if page else src)

    session_id = ""
    try:
        session_id = st.runtime.scriptrunner.get_script_run_ctx().session_id[:8]
    except Exception:
        pass

    if current is not None:
        if current == "positive":
            st.markdown(
                '<div style="font-size:12px;color:#16A34A;margin-top:0.3rem;">'
                + "👍 피드백 감사합니다. 더 나은 서비스에 반영됩니다.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:12px;color:#DC2626;margin-top:0.3rem;">'
                + "👎 불편을 드려 죄송합니다. 개선에 반영됩니다.</div>",
                unsafe_allow_html=True,
            )
        return

    st.markdown(
        """<div style="font-size:11px;color:rgba(75,85,99,0.8);
            margin-top:0.55rem;margin-bottom:0.2rem;
            letter-spacing:0.02em;">이 답변이 도움이 되었나요?</div>""",
        unsafe_allow_html=True,
    )

    col_pos, col_neg, _ = st.columns([1, 1, 8])

    with col_pos:
        if st.button(
            "👍 도움됨",
            key=f"fb_pos_{msg_idx}",
            help="이 답변이 정확하고 도움이 되었습니다",
        ):
            save_feedback(
                question=question,
                answer=answer,
                feedback="positive",
                mode=mode,
                sources=source_strs,
                session_id=session_id,
            )
            st.session_state[state_key] = "positive"
            if settings.monitoring_enabled:
                try:
                    get_metrics().record_feedback("positive")
                except Exception:
                    pass
            st.rerun()

    with col_neg:
        if st.button(
            "👎 부정확",
            key=f"fb_neg_{msg_idx}",
            help="이 답변에 오류가 있거나 도움이 되지 않았습니다",
        ):
            save_feedback(
                question=question,
                answer=answer,
                feedback="negative",
                mode=mode,
                sources=source_strs,
                session_id=session_id,
            )
            st.session_state[state_key] = "negative"
            if settings.monitoring_enabled:
                try:
                    get_metrics().record_feedback("negative")
                except Exception:
                    pass
            st.rerun()


_MODE_META: dict[str, dict] = {
    "fast": {"icon": "⚡", "label": "빠른 검색", "color": "#0369A1", "bg": "#EFF6FF"},
    "standard": {
        "icon": "⚖️",
        "label": "표준 검색",
        "color": "#065F46",
        "bg": "#ECFDF5",
    },
    "deep": {"icon": "🧠", "label": "심층 검색", "color": "#92400E", "bg": "#FFFBEB"},
}


def _render_mode_badge(mode: str, pipeline_label: str = "") -> None:
    meta = _MODE_META.get(mode, _MODE_META["standard"])
    sub = f"  ·  {pipeline_label}" if pipeline_label else ""
    st.markdown(
        f"""<div style="display:inline-flex;align-items:center;gap:0.35rem;
            background:{meta["bg"]};border:1px solid {meta["color"]}30;
            border-radius:5px;padding:0.2rem 0.55rem;
            font-size:11px;font-weight:600;color:{meta["color"]};
            margin-top:0.4rem;margin-bottom:0.55rem;">
            {meta["icon"]} {meta["label"]}{sub}
        </div>""",
        unsafe_allow_html=True,
    )


def _stream_answer(
    prompt: str,
    vector_db: FAISS,
    request_id: str = "",
    search_mode: str = "standard",
) -> tuple[str, list, Optional[SearchResult]]:
    """검색 → LLM 스트리밍 → Source Card 표시."""
    log = ContextLogger(logger, req=request_id[:8]) if request_id else logger
    search_result: Optional[SearchResult] = None

    with st.status("검색 중...", expanded=False) as status:
        try:
            for step_msg, result in iter_search_steps(
                query=prompt,
                vector_db=vector_db,
                mode=search_mode,
            ):
                status.write(f"📍 {step_msg}")
                if result is not None:
                    search_result = result

            if search_result is None or not search_result.ranked_docs:
                status.update(label="관련 문서 없음", state="complete", expanded=False)
            else:
                status.update(
                    label=f"검색 완료 — {search_result.hit_count}건 · {search_result.timing_summary}",
                    state="complete",
                    expanded=False,
                )
            log.info(
                f"검색 완료: mode={search_mode} | "
                f"hits={search_result.hit_count if search_result else 0}"
            )
            if settings.monitoring_enabled and search_result:
                try:
                    get_metrics().record_search(
                        search_result.t_total_ms / 1000, query=prompt
                    )
                except Exception:
                    pass

        except Exception as exc:
            status.update(label="검색 오류", state="error")
            st.error(f"검색 중 오류 발생: {exc}")
            log.error(f"검색 오류: {exc}", exc_info=True)
            if settings.monitoring_enabled:
                try:
                    get_metrics().record_error()
                except Exception:
                    pass
            return "", [], None

    context = (
        search_result.context
        if search_result and search_result.ranked_docs
        else "관련 규정 문서를 찾지 못했습니다."
    )

    _render_mode_badge(
        search_mode,
        pipeline_label=search_result.pipeline_label if search_result else "",
    )

    if search_result and search_result.rewritten_query:
        st.markdown(
            f'<div style="font-size:12px;color:#6B7280;margin-bottom:0.4rem;">'
            + f"쿼리 정제: <em>{search_result.rewritten_query}</em></div>",
            unsafe_allow_html=True,
        )

    msg_box = st.empty()
    full_text = ""
    last_render = time.time()
    stream_start = time.time()

    try:
        try:
            stream = get_llm_client().generate_stream(
                prompt, context, request_id=request_id
            )
        except TypeError:
            stream = get_llm_client().generate_stream(prompt, context)

        for token in stream:
            full_text += token
            now = time.time()
            if now - last_render >= _RENDER_INTERVAL_SEC or "\n" in token:
                msg_box.markdown(full_text + "▌")
                last_render = now

    except LLMQuotaError:
        msg_box.error("API 할당량 초과. 잠시 후 다시 시도해주세요.")
        if settings.monitoring_enabled:
            try:
                get_metrics().record_error()
            except Exception:
                pass
        return "", [], search_result

    except Exception as exc:
        msg_box.error(f"답변 생성 실패: {exc}")
        log.error(f"LLM 오류: {exc}", exc_info=True)
        return "", [], search_result

    stream_elapsed = time.time() - stream_start
    msg_box.markdown(full_text)
    log.info(f"답변 완료: {len(full_text):,}자 / 스트림 {stream_elapsed:.1f}초")

    if settings.monitoring_enabled:
        try:
            get_metrics().record_stream(stream_elapsed, token_count=len(full_text))
        except Exception:
            pass

    sources_data: list[dict] = []
    if search_result and search_result.ranked_docs:
        source_section_header(len(search_result.ranked_docs))
        for doc in search_result.ranked_docs:
            candidate_path = settings.local_work_dir / doc.source
            doc_path = candidate_path if candidate_path.exists() else None
            source_trust_card(
                rank=doc.rank,
                source=doc.source,
                page=doc.page,
                score=doc.score,
                article=doc.article,
                revision_date=getattr(doc, "revision_date", ""),
                chunk_text=doc.document.page_content,
                doc_path=doc_path,
                card_ns=f"new_{request_id[:6]}",
            )
            sources_data.append(
                {
                    "rank": doc.rank,
                    "source": doc.source,
                    "page": doc.page,
                    "score": doc.score,
                    "article": doc.article,
                    "revision_date": getattr(doc, "revision_date", ""),
                    "chunk_text": doc.document.page_content,
                    "doc_path_str": str(doc_path) if doc_path else None,
                }
            )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": full_text,
            "sources": sources_data,
            "mode": search_mode,
            "pipeline_label": search_result.pipeline_label if search_result else "",
            "question": prompt,
        }
    )

    return full_text, sources_data, search_result


def _render_chat_tab(vector_db, db_health: DBHealth) -> None:
    """대화 탭 — 메인 챗봇 인터페이스."""
    if not db_health.is_healthy:
        error_banner(
            title="데이터베이스 연결 실패",
            description="build_db.py 를 실행하거나 관리자에게 문의해 주세요.",
        )

    st.divider()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if not st.session_state.messages:
        home_screen()
    else:
        tip_banner(random.choice(_TIPS))

    for msg_idx, msg in enumerate(st.session_state.messages[-_MAX_HISTORY:]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

            if msg["role"] == "assistant":
                sources = msg.get("sources", [])
                mode = msg.get("mode", "standard")
                p_label = msg.get("pipeline_label", "")
                question = msg.get("question", "")

                _render_mode_badge(mode, pipeline_label=p_label)

                if sources:
                    source_section_header(len(sources))
                    for s in sources:
                        _dp = Path(s["doc_path_str"]) if s.get("doc_path_str") else None
                        source_trust_card(
                            rank=s["rank"],
                            source=s["source"],
                            page=s["page"],
                            score=s["score"],
                            article=s.get("article", ""),
                            revision_date=s.get("revision_date", ""),
                            chunk_text=s.get("chunk_text", ""),
                            doc_path=_dp,
                            card_ns=str(msg_idx),
                        )

                _render_feedback_buttons(
                    msg_idx=msg_idx,
                    question=question,
                    answer=msg["content"],
                    mode=mode,
                    sources=sources,
                )

    search_mode = st.session_state.get("search_mode", "standard")
    prefill = st.session_state.pop("prefill_prompt", None)
    prompt = prefill or st.chat_input(
        "규정이나 지침에 대해 질문하세요...", key="chat_input"
    )

    if prompt:
        request_id = str(uuid.uuid4())
        log = ContextLogger(logger, req=request_id[:8])
        log.info(f"신규 질문: '{prompt[:50]}' [모드: {search_mode}]")

        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        if not db_health.is_healthy:
            with st.chat_message("assistant"):
                st.warning("규정 데이터베이스가 구축되지 않아 답변할 수 없습니다.")
        else:
            with st.chat_message("assistant"):
                full_text, sources, search_result = _stream_answer(
                    prompt=prompt,
                    vector_db=vector_db,
                    request_id=request_id,
                    search_mode=search_mode,
                )
                if full_text:
                    last_idx = len(st.session_state.messages) - 1
                    _render_feedback_buttons(
                        msg_idx=last_idx,
                        question=prompt,
                        answer=full_text,
                        mode=search_mode,
                        sources=sources,
                    )


def _render_benchmark_tab(vector_db) -> None:
    """벤치마크 탭 (관리자 전용)."""
    st.markdown(
        f'<h2 style="font-size:20px;font-weight:700;color:{T.TEXT};'
        f'margin:0.5rem 0 0.3rem;">시스템 벤치마크</h2>'
        f'<p style="font-size:14px;color:{T.TEXT_MUTED};margin:0 0 1rem;">'
        f"검색 모드별 성능 비교 · 답변 품질 통계</p>",
        unsafe_allow_html=True,
    )

    stats: dict = {}
    try:
        stats = get_metrics().get_stats()
    except Exception:
        pass

    raw_err = stats.get("error_rate", 0.0)
    err_pct = raw_err if raw_err > 1.0 else raw_err * 100

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 질문 수", f"{stats.get('query_count', 0):,}회")
    c2.metric("평균 검색 시간", f"{stats.get('avg_search_ms', 0):.0f} ms")
    c3.metric("평균 응답 시간", f"{stats.get('avg_stream_ms', 0):.0f} ms")
    c4.metric("오류율", f"{err_pct:.1f}%", delta_color="inverse")

    st.divider()

    fb_stats = get_feedback_stats()
    total = fb_stats.get("total", 0)
    pos_rate = fb_stats.get("positive_rate", 0.0) * 100

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("총 피드백", f"{total:,}건")
    f2.metric("도움됨", f"{fb_stats.get('positive', 0):,}건")
    f3.metric("부정확", f"{fb_stats.get('negative', 0):,}건")
    f4.metric("긍정률", f"{pos_rate:.1f}%")

    if total > 0:
        import pandas as pd

        by_mode = fb_stats.get("by_mode", {})
        mode_label_map = {"fast": "⚡ 빠른", "standard": "⚖️ 표준", "deep": "🧠 심층"}
        rows = [
            {
                "검색 모드": mode_label_map.get(mid, mid),
                "총": meta["total"],
                "👍": meta["positive"],
                "👎": meta["negative"],
                "긍정률": f"{meta['positive_rate'] * 100:.0f}%",
            }
            for mid, meta in by_mode.items()
            if meta["total"] > 0
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    import pandas as pd

    bm_data = st.session_state.get("benchmark_results", {})
    if bm_data:
        rows = []
        label_map = {"fast": "⚡ 빠른", "standard": "⚖️ 표준", "deep": "🧠 심층"}
        for mode_id, results in bm_data.items():
            for r in results:
                rows.append(
                    {
                        "모드": label_map.get(mode_id, mode_id),
                        "검색(초)": round(r["search_sec"], 2),
                        "응답(초)": round(r["stream_sec"], 2),
                        "총합(초)": round(r["total_sec"], 2),
                    }
                )
        df_bm = pd.DataFrame(rows)
        df_avg = df_bm.groupby("모드").mean(numeric_only=True).reset_index()
        st.bar_chart(df_avg.set_index("모드")[["검색(초)", "응답(초)"]])
    else:
        df_guide = pd.DataFrame(
            {
                "모드": ["⚡ 빠른", "⚖️ 표준", "🧠 심층"],
                "검색(초)": [0.05, 0.5, 1.5],
                "응답(초)": [0.95, 2.0, 3.5],
            }
        ).set_index("모드")
        st.bar_chart(df_guide)
        st.caption(
            "위 수치는 예상 기준값입니다. 테스트 실행 후 실측값으로 업데이트됩니다."
        )

    st.divider()
    col_l, col_r = st.columns([2, 1])
    with col_l:
        test_query = st.selectbox(
            "테스트 쿼리",
            options=["(직접 입력)"] + _BENCHMARK_QUERIES,
            key="bm_query_select",
        )
        if test_query == "(직접 입력)":
            test_query = st.text_input(
                "직접 입력",
                placeholder="테스트 질문을 입력하세요",
                key="bm_query_custom",
                label_visibility="collapsed",
            )
    with col_r:
        test_modes = st.multiselect(
            "테스트 모드",
            options=["⚡ 빠른 검색", "⚖️ 표준 검색", "🧠 심층 검색"],
            default=["⚡ 빠른 검색", "⚖️ 표준 검색"],
            key="bm_modes",
        )

    run_btn = st.button(
        "▶ 테스트 실행",
        type="primary",
        key="bm_run",
        disabled=(not test_query or not test_modes or vector_db is None),
    )

    if run_btn and test_query and test_modes:
        mode_map_rev = {
            "⚡ 빠른 검색": "fast",
            "⚖️ 표준 검색": "standard",
            "🧠 심층 검색": "deep",
        }
        if "benchmark_results" not in st.session_state:
            st.session_state["benchmark_results"] = {}
        prog = st.progress(0, text="테스트 실행 중...")
        results = []

        for i, mode_label in enumerate(test_modes):
            mode_id = mode_map_rev[mode_label]
            prog.progress(i / len(test_modes), text=f"[{mode_label}] 측정 중...")
            try:
                from core.search_engine import _run_fast, _run_standard, _run_deep

                if mode_id == "fast":
                    sr = _run_fast(test_query, vector_db)
                elif mode_id == "standard":
                    sr = _run_standard(test_query, vector_db)
                else:
                    sr = _run_deep(test_query, vector_db)

                t_llm = time.time()
                resp = ""
                try:
                    for tok in get_llm_client().generate_stream(
                        test_query, sr.context[:800]
                    ):
                        resp += tok
                except Exception:
                    pass
                stream_sec = time.time() - t_llm

                row = {
                    "mode_id": mode_id,
                    "mode_label": mode_label,
                    "search_sec": sr.t_total_ms / 1000,
                    "stream_sec": stream_sec,
                    "total_sec": (sr.t_total_ms / 1000) + stream_sec,
                    "hit_count": sr.hit_count,
                    "avg_score": sr.avg_score,
                }
                st.session_state["benchmark_results"].setdefault(mode_id, []).append(
                    row
                )
                results.append(row)
            except Exception as exc:
                logger.error(f"벤치마크 오류 [{mode_id}]: {exc}", exc_info=True)
                results.append(
                    {
                        "mode_id": mode_id,
                        "mode_label": mode_label,
                        "search_sec": 0.0,
                        "stream_sec": 0.0,
                        "total_sec": 0.0,
                        "hit_count": 0,
                        "avg_score": 0.0,
                    }
                )

        prog.progress(1.0, text="완료!")
        prog.empty()
        st.success(f"{len(test_modes)}개 모드 테스트 완료")
        res_cols = st.columns(len(results))
        for col, r in zip(res_cols, results):
            with col:
                st.markdown(
                    f"""<div style="background:#F8FAFC;border:1px solid #E5E7EB;
                        border-radius:10px;padding:0.85rem 1rem;text-align:center;">
                        <div style="font-size:12px;font-weight:700;color:{T.TEXT};margin-bottom:0.4rem;">
                            {r["mode_label"]}</div>
                        <div style="font-size:24px;font-weight:800;color:{T.PRIMARY};">
                            {r["total_sec"]:.1f}s</div>
                        <div style="font-size:11px;color:{T.TEXT_MUTED};margin-top:0.2rem;">
                            검색 {r["search_sec"]:.2f}s + 응답 {r["stream_sec"]:.2f}s</div>
                        <div style="font-size:11px;color:{T.TEXT_SECONDARY};margin-top:0.35rem;">
                            결과 {r["hit_count"]}건 · 신뢰도 {r["avg_score"] * 100:.0f}%</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        st.rerun()

    if st.session_state.get("benchmark_results"):
        if st.button("벤치마크 기록 초기화", key="bm_clear"):
            st.session_state["benchmark_results"] = {}
            st.rerun()


def _render_log_tab() -> None:
    """로그 탭 — 관리자 전용."""
    st.markdown(
        f'<h2 style="font-size:20px;font-weight:700;color:{T.TEXT};'
        f'margin:0.5rem 0 0.3rem;">시스템 로그 & 피드백 데이터</h2>',
        unsafe_allow_html=True,
    )

    log_tab, fb_tab = st.tabs(["시스템 로그", "피드백 데이터"])

    with log_tab:
        try:
            last_qs = get_metrics().get_stats().get("last_queries", [])
            if last_qs:
                import pandas as pd

                st.dataframe(
                    pd.DataFrame({"#": range(1, len(last_qs) + 1), "질문": last_qs}),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("이번 세션에서 아직 질문이 없습니다.")
        except Exception as exc:
            st.warning(f"이력 로드 실패: {exc}")

        st.divider()
        try:
            log_dir = settings.log_dir
            if log_dir and Path(log_dir).exists():
                log_files = sorted(
                    Path(log_dir).glob("*.log"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if log_files:
                    col_a, col_b = st.columns([3, 1])
                    with col_a:
                        sel = st.selectbox(
                            "로그 파일",
                            options=[f.name for f in log_files[:10]],
                            key="log_file_select",
                        )
                    with col_b:
                        n_lines = st.number_input(
                            "표시 줄",
                            min_value=20,
                            max_value=500,
                            value=100,
                            step=20,
                            key="log_lines",
                        )
                    if st.button("불러오기", key="show_log"):
                        content = (Path(log_dir) / sel).read_text(
                            encoding="utf-8", errors="replace"
                        )
                        st.code(
                            "\n".join(content.splitlines()[-int(n_lines) :]),
                            language="text",
                        )
                else:
                    st.info("로그 파일이 없습니다.")
            else:
                st.info("로그 디렉토리가 설정되지 않았습니다.")
        except Exception as exc:
            st.warning(f"로그 파일 조회 실패: {exc}")

    with fb_tab:
        all_fb = load_all_feedback()
        if not all_fb:
            st.info("아직 피드백 데이터가 없습니다.")
        else:
            import pandas as pd

            col_fa, col_fb = st.columns(2)
            with col_fa:
                fb_filter = st.selectbox(
                    "피드백 필터",
                    options=["전체", "도움됨만", "부정확만"],
                    key="fb_filter",
                )
            with col_fb:
                mode_filter = st.selectbox(
                    "검색 모드 필터",
                    options=["전체", "fast", "standard", "deep"],
                    key="fb_mode_filter",
                )

            filtered = all_fb
            if fb_filter == "도움됨만":
                filtered = [r for r in filtered if r.get("feedback") == "positive"]
            elif fb_filter == "부정확만":
                filtered = [r for r in filtered if r.get("feedback") == "negative"]
            if mode_filter != "전체":
                filtered = [r for r in filtered if r.get("mode") == mode_filter]

            if filtered:
                df_fb = pd.DataFrame(
                    [
                        {
                            "시각": r.get("timestamp", "")[:19].replace("T", " "),
                            "피드백": "도움됨"
                            if r.get("feedback") == "positive"
                            else "부정확",
                            "모드": r.get("mode", ""),
                            "질문": r.get("question", "")[:60],
                        }
                        for r in filtered
                    ]
                )
                st.dataframe(df_fb, use_container_width=True, hide_index=True)

            pos_count = len([r for r in all_fb if r.get("feedback") == "positive"])
            if st.button(
                "training_data.json 내보내기",
                type="secondary",
                key="export_training_data",
                disabled=(pos_count == 0),
            ):
                try:
                    export_path = export_as_training_data()
                    st.success(f"저장 완료: {export_path}")
                except Exception as exc:
                    st.error(f"내보내기 실패: {exc}")


# ──────────────────────────────────────────────────────────────────────
#  앱 진입점 v9.3
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """
    main.py 진입점 (v9.4 — RAG 전용).

    [처리 순서]
    1. page_header() — 병원명/설명 헤더 표시
    2. _load_resources() — 벡터 DB 로드 (FAISS + 임베딩 모델)
    3. render_sidebar() — 사이드바 렌더 및 role 반환
    4. _active_page 분기 — SQL대시보드 / 문서관리 / 검색
    """
    # ── 검색 앱 헤더 표시 ─────────────────────────────────────────────
    # "좋은문화병원 가이드봇" 제목 + 설명 출력
    # 대시보드 앱(dashboard_app.py)에는 이 헤더가 없음
    page_header()

    # ── AI 리소스 로드 ────────────────────────────────────────────────
    # FAISS 벡터 DB + 임베딩 모델을 로드.
    # @st.cache_resource 로 캐싱되므로 최초 1회만 실제 로드됨.
    # 병동 대시보드(dashboard_app.py)는 이 무거운 리소스를 로드하지 않음.
    _ph = st.empty()  # 로딩 표시용 빈 자리
    vector_db = _load_resources()
    _ph.empty()  # 로딩 완료 후 자리 비움

    # ── 사이드바 + role 확인 ──────────────────────────────────────────
    # render_sidebar()는 사이드바를 그리고 현재 사용자 role을 반환
    # role: "user" (일반 직원) | "admin" (관리자)
    db_health = _check_health(vector_db)
    current_role = render_sidebar(db_health)

    # ── 페이지 라우팅 ─────────────────────────────────────────────────
    # session_state["active_page"] 값에 따라 화면 결정
    # 기본값 "main" → 일반 RAG 채팅 화면
    _active_page = st.session_state.get("active_page", "main")

    # ── SQL 대시보드 (관리자 전용) ────────────────────────────────────
    # 사이드바에서 SQL 대시보드 버튼 클릭 시 진입
    if _active_page == "sql_dashboard":
        if current_role != "admin":
            # 일반 유저가 직접 URL 조작으로 접근 시도하는 경우 차단
            st.error("SQL 대시보드는 관리자만 접근 가능합니다.")
            st.session_state["active_page"] = "main"
        else:
            try:
                from ui.sql_dashboard import render_sql_dashboard

                render_sql_dashboard(
                    admin_user=st.session_state.get("admin_id", "admin")
                )
            except Exception as _e:
                st.error(f"SQL 대시보드 로드 실패: {_e}")
                logger.error(f"sql_dashboard 오류: {_e}", exc_info=True)
        return  # 이 화면이 렌더됐으므로 아래 코드는 실행하지 않음

    # ── 문서 관리 (관리자 전용) ──────────────────────────────────────
    # 사이드바에서 문서 관리 버튼 클릭 시 진입
    if _active_page == "doc_manager":
        if current_role != "admin":
            st.error("문서 관리는 관리자만 접근 가능합니다.")
            st.session_state["active_page"] = "main"
        else:
            try:
                from ui.doc_manager_ui import render_doc_manager_ui

                render_doc_manager_ui(
                    admin_user=st.session_state.get("admin_id", "admin")
                )
            except Exception as _e:
                st.error(f"문서 관리 로드 실패: {_e}")
                logger.error(f"doc_manager 오류: {_e}", exc_info=True)
        return

    # ── 검색 모드 확인 ───────────────────────────────────────────────
    # search_mode: "fast" | "standard" | "deep" | "data_analysis"
    # 사이드바 버튼에서 변경됨 (sidebar.py 의 _SEARCH_MODES 목록)
    current_mode: str = st.session_state.get("search_mode", "standard")
    _IS_DATA_ANALYSIS = current_mode == "data_analysis"

    # ── 탭 구성 및 화면 렌더 ─────────────────────────────────────────
    # 관리자: 대화 + 벤치마크 + 로그 탭
    # 일반 유저: 대화만 (또는 데이터 분석)
    if current_role == "admin":
        if _IS_DATA_ANALYSIS:
            tabs = st.tabs(["📊 데이터 분석", "📊 벤치마크", "📋 로그"])
            with tabs[0]:
                render_data_analysis_tab()
            with tabs[1]:
                _render_benchmark_tab(vector_db)
            with tabs[2]:
                _render_log_tab()
        else:
            tabs = st.tabs(["💬 대화", "📊 벤치마크", "📋 로그"])
            with tabs[0]:
                _render_chat_tab(vector_db, db_health)
            with tabs[1]:
                _render_benchmark_tab(vector_db)
            with tabs[2]:
                _render_log_tab()
    else:
        # 일반 유저는 탭 없이 바로 화면 표시
        if _IS_DATA_ANALYSIS:
            render_data_analysis_tab()
        else:
            _render_chat_tab(vector_db, db_health)


if __name__ == "__main__":
    main()
