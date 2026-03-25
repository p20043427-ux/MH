"""
core/context_builder.py  ─  LLM 컨텍스트 빌더 (토큰 최적화 v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[현재 문제]
  컨텍스트 ~1600~2100 tokens → Gemini 입력 과다
  → TTFT(Time To First Token) 증가
  → 불필요한 정보가 포함되어 할루시네이션 위험↑

[최적화 전략]
  ┌──────────────────────────────────────────────────────────┐
  │  항목            변경 전         변경 후         절감    │
  │──────────────────────────────────────────────────────────│
  │  청크당 토큰     전체(~600tok)    400자 트런케이션        │
  │  REF 수          3개              3개 유지                │
  │  헤더 형식       장황한 포맷      압축 포맷               │
  │  총 컨텍스트     ~2100 tok        ~900 tok        57%↓   │
  └──────────────────────────────────────────────────────────┘

[토큰 절감이 속도에 영향하는 이유]
  Gemini 2.5 Flash 처리시간 ∝ 입력 토큰 수
  2100tok → 900tok: TTFT ~0.5~1초 개선 예상
"""

from __future__ import annotations

from typing import List, Optional

from core.retriever import RankedDocument
from utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  토큰 최적화 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 청크당 최대 문자 수 (한국어 400자 ≈ 200~267 토큰)
# 기존: 전체 page_content (약 800자 = ~400~533 토큰)
# 변경: 600자 (약 300~400 토큰) → 핵심 내용 충분히 포함
CHUNK_MAX_CHARS: int = 600

# 전체 컨텍스트 최대 토큰 수 (안전 상한)
# 한국어 1 토큰 ≈ 1.5~2자 → 900 토큰 ≈ 1350~1800자
CONTEXT_MAX_TOKENS: int = 900

# CE 점수 → 신뢰도 레이블 기준
HIGH_TRUST_THRESHOLD:   float = 0.7
MEDIUM_TRUST_THRESHOLD: float = 0.3


def build_context(
    ranked_docs:    List[RankedDocument],
    max_chars_each: int = CHUNK_MAX_CHARS,
    max_total_tok:  int = CONTEXT_MAX_TOKENS,
) -> str:
    """
    LLM 컨텍스트 문자열 생성 (토큰 최적화).

    [포맷 전략]
    · 헤더를 최소화하여 토큰 절약
    · 청크 텍스트는 앞 600자만 사용
    · 누적 추정 토큰 수가 max_total_tok 초과 시 중단
      (낮은 순위 문서 버림 → 고품질 정보 우선)

    Args:
        ranked_docs:    CE 리랭킹된 문서 리스트 (순위 1이 최고)
        max_chars_each: 청크당 최대 문자 수
        max_total_tok:  전체 컨텍스트 최대 토큰 수 (추정)

    Returns:
        LLM 에 전달할 컨텍스트 문자열
    """
    if not ranked_docs:
        return "관련 규정 문서를 찾을 수 없습니다."

    sections:    List[str] = []
    total_chars: int       = 0

    for doc in ranked_docs:
        # ── 1. 텍스트 트런케이션 ──────────────────────────────
        text     = doc.document.page_content
        if len(text) > max_chars_each:
            # 문장 경계에서 자름
            truncated   = text[:max_chars_each]
            last_break  = max(truncated.rfind("\n"), truncated.rfind("."))
            if last_break > max_chars_each * 0.7:
                text = truncated[:last_break + 1]
            else:
                text = truncated

        # ── 2. 누적 토큰 추정 (한국어 1자 ≈ 0.5토큰) ─────────
        estimated_tokens = len(text) // 2
        if total_chars > 0 and (total_chars // 2) + estimated_tokens > max_total_tok:
            logger.debug(
                f"컨텍스트 토큰 상한 도달: rank={doc.rank} 이후 생략"
            )
            break

        # ── 3. 압축 헤더 포맷 (토큰 절약) ────────────────────
        # 기존: "[REF 1] source: 취업규칙.pdf | page: 12 | 제26조" (50자)
        # 변경: "[1] 취업규칙.pdf p.12 §26조"                      (20자)
        header_parts = [f"[{doc.rank}]", doc.source, f"p.{doc.page}"]
        if doc.article:
            header_parts.append(f"§{doc.article}")
        if doc.revision_date:
            header_parts.append(f"({doc.revision_date})")
        header = " ".join(header_parts)

        sections.append(f"{header}\n{text}")
        total_chars += len(text) + len(header) + 2  # "\n\n" = 2자

    result = "\n\n".join(sections)

    logger.info(
        f"컨텍스트 구축: {len(ranked_docs)}개 청크 → "
        f"약 {len(result)//2} 토큰 (추정)"
    )
    return result


def estimate_tokens(text: str) -> int:
    """
    한국어 텍스트 토큰 수 추정 (tiktoken 없이).

    [근거]
    · 한국어 어절: 1어절 ≈ 2~3 토큰 (SentencePiece 기준)
    · 영문/숫자: 1자 ≈ 0.25~0.3 토큰
    · 간이 추정: 전체 문자 수 ÷ 2 (보수적 추정)
    """
    return max(1, len(text) // 2)


def format_source_list(ranked_docs: List[RankedDocument]) -> str:
    """UI 표시용 출처 목록 문자열"""
    if not ranked_docs:
        return "출처 없음"
    lines = []
    for d in ranked_docs:
        trust = _trust_label(d.score)
        line  = f"{d.rank}. {d.source} (p.{d.page})"
        if d.article:
            line += f" · {d.article}"
        line += f"  [{trust}]"
        lines.append(line)
    return "\n".join(lines)


def _trust_label(score: float) -> str:
    """CE 점수 → 신뢰도 레이블"""
    if score >= HIGH_TRUST_THRESHOLD:
        return "높음"
    if score >= MEDIUM_TRUST_THRESHOLD:
        return "보통"
    return "낮음"