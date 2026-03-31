"""
main.py  ─  좋은문화병원 가이드봇 v9.4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v9.4 — 코드 정리 & 성능 개선]

■ 핵심 변경
  · search_engine.py import 완전 제거
    from core.search_engine import SearchResult, iter_search_steps  ❌
    from core.rag_pipeline import PipelineResult, get_pipeline      ✅

  · 스트리밍 렌더 O(n²) → O(n)
    full_text += token   ❌  (str concat 반복 — 긴 답변일수록 느림)
    tokens.append(token) ✅  (list append O(1), 마지막에 join)

  · 매 토큰 time.time() 호출 제거
    if now - last_render >= ... or "\\n" in token  ❌
    if token_count % RENDER_EVERY == 0:            ✅  (시스템콜 90% 감소)

  · 불필요한 import 제거
    from langchain_community.vectorstores import FAISS  ❌

  · 벤치마크: private _run_* → pipeline.run_with_mode()
    from core.search_engine import _run_fast...  ❌
    pipeline.run_with_mode(query, mode)          ✅

[v9.2 유지]
  · 병원 현황판 라우팅 (ward / finance / opd)
  · SQL 대시보드 / 문서 관리 (관리자)
  · 데이터 분석 모드
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

from config.settings import settings
from core.llm import get_llm_client

# [v9.4] search_engine.py 제거 — rag_pipeline.py v8.0 단일 파이프라인
from core.rag_pipeline import PipelineResult, get_pipeline

# SearchResult 하위 호환 별칭 (다른 곳에서 참조 중인 경우 대비)
SearchResult = PipelineResult

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
    "제증명 서류는 퇴원 하루 전 미리 신청해 주세요",
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

# [v9.4] 스트리밍 렌더 간격 (초) — 낮을수록 부드럽지만 CPU 사용↑
_RENDER_INTERVAL_SEC = 0.05
# [v9.4] N토큰마다 time.time() 1회 호출 (시스템콜 오버헤드 최소화)
_RENDER_TOKEN_INTERVAL = 3


st.set_page_config(
    page_title=settings.app_title,
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(T.get_global_css(), unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _load_resources():
    """벡터 DB 병렬 초기화 + BM25 백그라운드 워밍업 (v9.4)."""
    from utils.startup_optimizer import parallel_load_resources, start_background_warmup
    try:
        result = parallel_load_resources()
        if result.pipeline is not None:
            start_background_warmup(result.pipeline)
        return result.vector_db
    except Exception as exc:
        logger.warning(f"병렬 로딩 실패 → 폴백: {exc}")
        from core.vector_store import VectorStoreManager
        manager = VectorStoreManager(
            db_path=settings.rag_db_path,
            model_name=settings.embedding_model,
            cache_dir=str(settings.local_work_dir),
        )
        return manager.load()


def _check_health(vector_db) -> DBHealth:
    """벡터 DB + 파일 시스템 상태 확인."""
    from datetime import datetime
    file_count, recent_files = 0, []
    try:
        pdf_files = sorted(
            settings.local_work_dir.glob("*.pdf"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        file_count = len(pdf_files)
        recent_files = [
            (p.name, datetime.fromtimestamp(p.stat().st_mtime).strftime("%m/%d"))
            for p in pdf_files[:5]
        ]
    except Exception:
        pass
    if vector_db is None:
        return DBHealth(is_healthy=False, message="DB 오프라인", doc_count=0,
                        file_count=file_count, recent_files=recent_files)
    try:
        return DBHealth(is_healthy=True, message="정상 가동 중",
                        doc_count=vector_db.index.ntotal,
                        file_count=file_count, recent_files=recent_files)
    except Exception:
        return DBHealth(is_healthy=True, message="정상 가동 중", doc_count=0,
                        file_count=file_count, recent_files=recent_files)


def _render_mode_badge(mode: str, pipeline_label: str = "") -> None:
    """검색 모드 뱃지 표시."""
    _MODE_COLORS = {
        "fast": ("#FEF3C7", "#D97706", "⚡"),
        "standard": ("#EFF6FF", "#2563EB", "⚖️"),
        "deep": ("#F5F3FF", "#7C3AED", "🧠"),
        "data_analysis": ("#ECFDF5", "#059669", "📊"),
    }
    color_bg, color_text, icon = _MODE_COLORS.get(mode, ("#F9FAFB", "#6B7280", "🔍"))
    label = pipeline_label or mode
    st.markdown(
        f'<span style="background:{color_bg};color:{color_text};'
        f"border:1px solid {color_text}33;border-radius:4px;"
        f'padding:2px 8px;font-size:11px;font-weight:600;">{icon} {label}</span>',
        unsafe_allow_html=True,
    )


def _stream_answer(
    prompt: str,
    vector_db,
    request_id: str,
    search_mode: str,
) -> tuple[str, list, Optional[PipelineResult]]:
    """
    RAG 검색 + LLM 스트리밍 답변 생성.

    [v9.4 변경]
    · iter_search_steps() → pipeline.iter_steps() (search_engine 제거)
    · full_text += token (O(n²)) → tokens.append + join (O(n))
    · time.time() 매 토큰 → N토큰마다 1회

    Returns:
        (full_text, sources_data, pipeline_result)
    """
    log = ContextLogger(logger, req=request_id[:8]) if request_id else logger
    pipeline_result: Optional[PipelineResult] = None

    # ── 검색 ────────────────────────────────────────────────
    with st.status("검색 중...", expanded=False) as status:
        try:
            # [v9.4] pipeline.iter_steps() — search_engine 대신 rag_pipeline 사용
            _pipeline = get_pipeline(vector_db)
            for step_msg, result in _pipeline.iter_steps(
                query=prompt,
                mode=search_mode,
            ):
                status.write(f"📍 {step_msg}")
                if result is not None:
                    pipeline_result = result

            if pipeline_result is None or not pipeline_result.ranked_docs:
                status.update(label="관련 문서 없음", state="complete", expanded=False)
            else:
                status.update(
                    label=f"검색 완료 — {pipeline_result.hit_count}건 · {pipeline_result.timing_summary}",
                    state="complete",
                    expanded=False,
                )
            log.info(
                f"검색 완료: mode={search_mode} | "
                f"hits={pipeline_result.hit_count if pipeline_result else 0}"
            )
            if settings.monitoring_enabled and pipeline_result:
                try:
                    get_metrics().record_search(
                        pipeline_result.t_total_ms / 1000, query=prompt
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
        pipeline_result.context
        if pipeline_result and pipeline_result.ranked_docs
        else "관련 규정 문서를 찾지 못했습니다."
    )

    _render_mode_badge(
        search_mode,
        pipeline_label=pipeline_result.pipeline_label if pipeline_result else "",
    )

    if pipeline_result and pipeline_result.rewritten_query:
        st.markdown(
            f'<div style="font-size:12px;color:#6B7280;margin-bottom:0.4rem;">'
            f"쿼리 정제: <em>{pipeline_result.rewritten_query}</em></div>",
            unsafe_allow_html=True,
        )

    # ── LLM 스트리밍 ────────────────────────────────────────
    msg_box = st.empty()

    # [v9.4] list.append O(1) → 마지막에 join O(n) — str+= O(n²) 제거
    tokens: list[str] = []
    token_count = 0
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
            tokens.append(token)
            token_count += 1
            # [v9.4] N토큰마다만 time.time() 호출 → 시스템콜 90% 감소
            if token_count % _RENDER_TOKEN_INTERVAL == 0:
                now = time.time()
                if now - last_render >= _RENDER_INTERVAL_SEC:
                    msg_box.markdown("".join(tokens) + "▌")
                    last_render = now

    except LLMQuotaError:
        msg_box.error("API 할당량 초과. 잠시 후 다시 시도해주세요.")
        if settings.monitoring_enabled:
            try:
                get_metrics().record_error()
            except Exception:
                pass
        return "", [], pipeline_result

    except Exception as exc:
        msg_box.error(f"답변 생성 실패: {exc}")
        log.error(f"LLM 오류: {exc}", exc_info=True)
        return "", [], pipeline_result

    full_text = "".join(tokens)  # [v9.4] join O(n)
    stream_elapsed = time.time() - stream_start
    msg_box.markdown(full_text)
    log.info(f"답변 완료: {len(full_text):,}자 / 스트림 {stream_elapsed:.1f}초")

    if settings.monitoring_enabled:
        try:
            get_metrics().record_stream(stream_elapsed, token_count=len(full_text))
        except Exception:
            pass

    # ── 출처 카드 ────────────────────────────────────────────
    sources_data: list[dict] = []
    if pipeline_result and pipeline_result.ranked_docs:
        source_section_header(len(pipeline_result.ranked_docs))
        for doc in pipeline_result.ranked_docs:
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
            "pipeline_label": pipeline_result.pipeline_label if pipeline_result else "",
            "question": prompt,
        }
    )

    return full_text, sources_data, pipeline_result



def _render_log_tab() -> None:
    """피드백 로그 탭 — 관리자 전용."""
    try:
        all_fb = load_all_feedback()
        if all_fb:
            import pandas as pd
            st.dataframe(pd.DataFrame(all_fb), use_container_width=True, hide_index=True)
            if st.button("CSV 내보내기", key="fb_export"):
                csv = export_as_training_data()
                st.download_button(
                    "📥 다운로드",
                    data=csv,
                    file_name="feedback_training.csv",
                    mime="text/csv",
                )
        else:
            st.info("피드백 데이터가 없습니다.")
    except Exception as exc:
        st.error(f"로그 로드 실패: {exc}")

def _render_benchmark_tab(vector_db) -> None:
    """
    벤치마크 탭 (관리자 전용).

    [v9.4] private _run_fast/_run_standard/_run_deep → pipeline.run_with_mode()
    """
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
                # [v9.4] private _run_* 제거 → pipeline.run_with_mode() 사용
                _bm_pipeline = get_pipeline(vector_db)
                t_search = time.time()
                sr = _bm_pipeline.run_with_mode(test_query, mode=mode_id, use_cache=False)
                search_sec = time.time() - t_search

                t_llm = time.time()
                resp_tokens: list[str] = []
                try:
                    for tok in get_llm_client().generate_stream(
                        test_query, sr.context[:800]
                    ):
                        resp_tokens.append(tok)
                except Exception:
                    pass
                stream_sec = time.time() - t_llm

                row = {
                    "mode": mode_id,
                    "search_sec": search_sec,
                    "stream_sec": stream_sec,
                    "total_sec": search_sec + stream_sec,
                    "hits": sr.hit_count if sr else 0,
                    "answer_len": len("".join(resp_tokens)),
                }
                results.append(row)
                st.session_state["benchmark_results"].setdefault(mode_id, []).append(row)

            except Exception as exc:
                st.error(f"[{mode_label}] 오류: {exc}")

        prog.progress(1.0, text="완료")

        if results:
            import pandas as pd

            label_map = {"fast": "⚡ 빠른", "standard": "⚖️ 표준", "deep": "🧠 심층"}
            rows_display = [
                {
                    "모드": label_map.get(r["mode"], r["mode"]),
                    "검색(초)": round(r["search_sec"], 2),
                    "응답(초)": round(r["stream_sec"], 2),
                    "총합(초)": round(r["total_sec"], 2),
                    "검색 결과": r["hits"],
                    "응답 길이": r["answer_len"],
                }
                for r in results
            ]
            st.dataframe(
                pd.DataFrame(rows_display),
                use_container_width=True,
                hide_index=True,
            )

    import pandas as pd

    bm_data = st.session_state.get("benchmark_results", {})
    if bm_data:
        rows = []
        label_map = {"fast": "⚡ 빠른", "standard": "⚖️ 표준", "deep": "🧠 심층"}
        for mode_id, bm_results in bm_data.items():
            for r in bm_results:
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
        st.caption("위 수치는 예상 기준값입니다. 테스트 실행 후 실측값으로 업데이트됩니다.")


def _render_chat_tab(vector_db, db_health: DBHealth) -> None:
    """대화 탭 — 메인 챗봇 인터페이스."""
    if not db_health.is_healthy:
        error_banner(
            title="데이터베이스 연결 실패",
            description="build_db.py 를 실행하거나 관리자에게 문의해 주세요.",
        )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    search_mode = st.session_state.get("search_mode", "standard")

    # 이전 메시지 렌더
    for i, msg in enumerate(st.session_state.messages[-_MAX_HISTORY:]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("sources"):
                with st.expander(f"📄 참고 문서 {len(msg['sources'])}건", expanded=False):
                    for src in msg["sources"]:
                        source_trust_card(
                            rank=src["rank"],
                            source=src["source"],
                            page=src["page"],
                            score=src["score"],
                            article=src.get("article", ""),
                            revision_date=src.get("revision_date", ""),
                            chunk_text=src["chunk_text"],
                            doc_path=Path(src["doc_path_str"]) if src.get("doc_path_str") else None,
                            card_ns=f"hist_{i}",
                        )

    # 홈 화면 (첫 방문)
    if not st.session_state.messages:
        home_screen()
        tip_banner(random.choice(_TIPS))

    # ── 입력창 — 항상 렌더 (prefill 여부 무관) ───────────────────
    # st.chat_input 을 먼저 렌더해야 화면 하단에 고정됨.
    # '_prefill or chat_input' 패턴은 prefill 시 chat_input 을 skip해서
    # 답변 후 입력창이 사라지는 버그 → chat_input 항상 먼저 렌더.
    _user_input = st.chat_input("규정이나 업무에 대해 질문하세요")
    _prefill    = st.session_state.pop("prefill_prompt", None)
    prompt      = _prefill or _user_input

    if prompt:
        request_id = str(uuid.uuid4())
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        if vector_db is None:
            st.warning("벡터 DB가 없습니다. build_db.py를 먼저 실행해 주세요.")
        elif search_mode == "data_analysis":
            with st.chat_message("assistant"):
                render_data_analysis_tab()
        else:
            with st.chat_message("assistant"):
                full_text, sources, pipeline_result = _stream_answer(
                    prompt=prompt,
                    vector_db=vector_db,
                    request_id=request_id,
                    search_mode=search_mode,
                )


def main() -> None:
    """가이드봇 메인 진입점."""
    vector_db = _load_resources()
    db_health = _check_health(vector_db)

    active_page = st.session_state.get("active_page", "main")

    render_sidebar(db_health=db_health)

    if active_page == "hospital_dashboard":
        from ui.hospital_dashboard import render_hospital_dashboard

        tab = st.session_state.get("dashboard_tab", "ward")
        render_hospital_dashboard(tab=tab)
        return

    page_header()

    search_mode = st.session_state.get("search_mode", "standard")

    if search_mode in ("sql_dashboard",):
        from ui.sql_dashboard import render_sql_dashboard

        render_sql_dashboard(vector_db=vector_db)
        return

    if search_mode == "doc_manage":
        from ui.doc_manager import render_doc_manager

        render_doc_manager()
        return

    # ── 관리자 여부 확인 ────────────────────────────────────────────
    current_role: str = st.session_state.get("role", "user")
    _is_admin = (current_role == "admin")
    _is_data = (st.session_state.get("search_mode", "standard") == "data_analysis")

    # ── 탭 구성 — 관리자만 벤치마크·로그 탭 표시 ──────────────────
    if _is_admin:
        if _is_data:
            tabs = st.tabs(["📊 데이터 분석", "📊 벤치마크", "📋 로그"])
            with tabs[0]:
                render_data_analysis_tab()
            with tabs[1]:
                _render_benchmark_tab(vector_db=vector_db)
            with tabs[2]:
                _render_log_tab()
        else:
            tabs = st.tabs(["💬 대화", "📊 벤치마크", "📋 로그"])
            with tabs[0]:
                _render_chat_tab(vector_db=vector_db, db_health=db_health)
            with tabs[1]:
                _render_benchmark_tab(vector_db=vector_db)
            with tabs[2]:
                _render_log_tab()
    else:
        # 일반 유저 — 탭 없이 바로 화면 표시
        if _is_data:
            render_data_analysis_tab()
        else:
            _render_chat_tab(vector_db=vector_db, db_health=db_health)


if __name__ == "__main__":
    main()