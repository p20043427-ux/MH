"""
core/context_builder.py  ─  LLM 컨텍스트 조립 모듈 v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v2.0 변경사항]
  ✅ build_cot_context() 추가
     - 이전 위치: core/search_engine.py 의 _build_cot_context() (private)
     - 변경: core/context_builder.py 의 build_cot_context() (public)
     - 이유: CoT 컨텍스트 빌드는 "검색 실행" 책임이 아닌
             "컨텍스트 조립" 책임 → 올바른 모듈에 배치

[역할]
  RAG 검색 결과 (RankedDocument 리스트)를
  LLM 에 전달할 프롬프트 컨텍스트 문자열로 조립합니다.

  · build_context()      : 표준 컨텍스트 (ranked_docs → 문자열)
  · build_cot_context()  : Chain of Thought 컨텍스트 (deep 모드용)
  · format_source_list() : 참고 문서 목록 포맷 (출처 표시용)
"""

from __future__ import annotations

from typing import List, Optional

from core.retriever import RankedDocument


def build_context(ranked_docs: List[RankedDocument]) -> str:
    """
    검색된 문서 목록을 LLM 프롬프트용 컨텍스트 문자열로 조립합니다.

    [조립 형식]
    [문서 1] 출처: {파일명} p.{페이지} | 조항: {조항명}
    {청크 내용}

    [문서 2] ...

    Args:
        ranked_docs: CE 리랭킹 후 정렬된 RankedDocument 리스트

    Returns:
        LLM 에 주입할 컨텍스트 문자열 (문서 없으면 빈 메시지)
    """
    if not ranked_docs:
        return "관련 규정 문서를 찾을 수 없습니다."

    parts = []
    for i, doc in enumerate(ranked_docs, 1):
        source = doc.source or "출처 불명"
        page   = doc.page or ""
        article = doc.article or ""

        # 헤더: 출처 정보
        header_parts = [f"[문서 {i}] 출처: {source}"]
        if page:
            header_parts.append(f"p.{page}")
        header = " | ".join(header_parts)
        if article:
            header += f" | 조항: {article}"

        parts.append(f"{header}\n{doc.chunk_text}")

    return "\n\n".join(parts)


def build_cot_context(
    original_query: str,
    expanded_queries: List[str],
    ranked_docs: List[RankedDocument],
) -> str:
    """
    Multi-Query 심층 검색용 Chain of Thought 컨텍스트를 생성합니다.

    [v2.0 신규 — 이전 위치: search_engine._build_cot_context()]

    [Chain of Thought 목적]
    · 병원 업무 규정은 예외/단서 조항이 많음
    · LLM 에게 "단계적 추론"을 지시하여 예외 케이스 놓침 방지
    · 특히 deep 모드(다중 쿼리 확장)에서 효과적

    [컨텍스트 구조]
    ┌─────────────────────────────────────────────────┐
    │ [답변 지시문]                                   │
    │ 아래 참고 문서를 단계적으로 분석하여 답변하세요. │
    │ 예외 조항과 단서 조항에 특히 주의하세요.         │
    │                                                 │
    │ [원본 질문] {original_query}                    │
    │ [검색 관점] {expanded_queries 요약}              │
    │                                                 │
    │ [참고 문서 1] ...                               │
    │ [참고 문서 2] ...                               │
    └─────────────────────────────────────────────────┘

    Args:
        original_query:   사용자의 원본 질문
        expanded_queries: 쿼리 확장으로 생성된 검색 관점 목록
        ranked_docs:      CE 리랭킹 후 선별된 문서 목록

    Returns:
        CoT 지시문이 포함된 LLM 컨텍스트 문자열
    """
    # 표준 문서 컨텍스트 먼저 조립
    base_context = build_context(ranked_docs)

    # CoT 지시문 헤더
    cot_header = (
        "[답변 방식]\n"
        "아래 참고 문서들을 단계적으로 분석하여 답변하세요.\n"
        "1. 각 문서에서 질문과 관련된 조항을 찾으세요.\n"
        "2. 예외 조항, 단서 조항('다만', '단', '제외')을 반드시 확인하세요.\n"
        "3. 여러 문서 내용이 상충하면 최신 또는 더 구체적인 조항을 우선하세요.\n"
        "4. 확인되지 않은 내용은 추측하지 말고 '규정집 확인 필요'라고 명시하세요.\n"
    )

    # 검색 관점 요약 (사용자에게 투명성 제공)
    perspectives = "\n".join(
        f"  관점 {i + 1}: {q}" for i, q in enumerate(expanded_queries)
    )
    query_section = (
        f"[원본 질문]\n{original_query}\n\n"
        f"[분석에 사용된 검색 관점]\n{perspectives}\n"
    )

    return f"{cot_header}\n{query_section}\n\n[참고 문서]\n{base_context}"


def format_source_list(ranked_docs: List[RankedDocument]) -> str:
    """
    참고 문서 목록을 출처 표시용 문자열로 포맷합니다.

    [출력 형식]
    📄 참고 문서
    1. {파일명} p.{페이지} — {조항명}
    2. ...

    Args:
        ranked_docs: RankedDocument 리스트

    Returns:
        포맷된 출처 목록 문자열 (없으면 빈 문자열)
    """
    if not ranked_docs:
        return ""

    lines = ["📄 참고 문서"]
    for i, doc in enumerate(ranked_docs, 1):
        source = doc.source or "출처 불명"
        page   = doc.page or ""
        article = doc.article or ""

        line = f"{i}. {source}"
        if page:
            line += f" p.{page}"
        if article:
            line += f" — {article}"
        lines.append(line)

    return "\n".join(lines)