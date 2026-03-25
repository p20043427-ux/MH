"""
core/query_rewriter.py  ─  경량 QueryRewriter (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[설계 목표]
  LLM 호출 없이 규칙 기반으로 쿼리를 정제.
  추가 지연 0.001초 이내 (순수 Python 연산).

[QueryRewriter 가 필요한 이유]
  사용자 입력:  "연차 어떻게 써요?"
  실제 문서어:  "연차휴가 신청 절차"

  → FAISS 는 embedding 유사도로 검색
  → 구어체 ≠ 문서 표현 → 검색 품질 저하
  → 쿼리를 문서 언어에 가깝게 정규화하면 정확도↑

[3단계 파이프라인]
  1. 구어체 → 문서어 사전 치환 (dict lookup, O(1))
  2. 불필요 어미·조사 제거 (regex, O(n))
  3. 키워드 확장: 병원 전문 용어 추가 (dict lookup, O(1))

[LLM 기반 QueryRewriter 와 비교]
  규칙 기반: +0.001초, 정확도 보통, 구현 즉시 가능
  LLM 기반:  +1~2초,   정확도 높음, API 비용 추가
  → 현재 목표(5초 이내)에서는 규칙 기반이 최선
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Optional

from utils.logger import get_logger
from config.settings import settings

logger = get_logger(__name__, log_dir=settings.log_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  병원 전문 용어 사전 (구어체 → 문서어)
#  필요 시 확장: TERM_MAP 에 key-value 추가만 하면 됨
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TERM_MAP: dict[str, str] = {
    # ── 휴가·근태 ──────────────────────────────────────────
    "연차":         "연차휴가",
    "연차 쓰기":    "연차휴가 신청",
    "반차":         "반일 연차휴가",
    "병가":         "병가 휴가",
    "출산":         "출산휴가",
    "육아":         "육아휴직",
    "조퇴":         "조퇴 처리 절차",
    "지각":         "지각 처리 기준",

    # ── 급여·수당 ──────────────────────────────────────────
    "월급":         "급여",
    "봉급":         "급여",
    "야근비":       "연장근로 수당",
    "야근 수당":    "연장근로 수당",
    "야간 수당":    "야간근로 수당",
    "당직비":       "당직 수당",
    "명절비":       "명절 귀향 여비",
    "식대":         "식사 보조비",
    "교통비":       "교통 보조비",
    "성과급":       "성과 상여금",

    # ── 서류·증명 ──────────────────────────────────────────
    "재직증명":     "재직증명서",
    "경력증명":     "경력증명서",
    "급여명세":     "급여명세서",
    "원천징수":     "원천징수 확인서",
    "건강보험":     "건강보험 증명서",

    # ── 교육·훈련 ──────────────────────────────────────────
    "신입 교육":    "신규 직원 오리엔테이션",
    "법정 교육":    "법정의무교육",
    "감염 교육":    "감염관리 교육",

    # ── 환경·안전 ──────────────────────────────────────────
    "낙상":         "낙상 사고 보고",
    "욕창":         "욕창 예방 지침",
    "의료폐기물":   "의료 폐기물 분리수거",
    "소독":         "소독 및 멸균 지침",
    "감염":         "감염관리 지침",

    # ── 인사·복무 ──────────────────────────────────────────
    "징계":         "징계 처분 기준",
    "해고":         "해고 및 퇴직 절차",
    "퇴직금":       "퇴직급여 지급 기준",
    "전보":         "인사 발령 절차",
    "승진":         "승진 기준",
    "평가":         "인사 고과 평가",
}

# ── 키워드 확장 사전 (검색 쿼리에 관련 용어 추가) ─────
# 예: "연차" 검색 시 "연차휴가 신청 절차" 도 함께 검색 공간에 반영
EXPAND_MAP: dict[str, List[str]] = {
    "연차휴가":     ["휴가 신청 절차", "연차 부여 기준"],
    "급여":         ["임금 지급일", "급여 계산"],
    "당직":         ["당직 수당", "당직 근무 기준"],
    "감염관리":     ["감염 예방", "격리 지침"],
    "의료사고":     ["사고 보고 절차", "환자 안전"],
    "징계":         ["징계 처분", "복무 규정 위반"],
    "퇴직":         ["퇴직 절차", "퇴직급여"],
}

# ── 제거할 구어체 어미·조사 패턴 ──────────────────────
_NOISE_PATTERNS: List[str] = [
    r"(어떻게|어떻게 되|어떻게 하면|어떻게 해야)\s*(되나요|해요|하나요|됩니까|됩니까\?|되나요\?)?",
    r"(가르쳐|알려)\s*(주세요|줘|줄래요)?",
    r"(궁금|문의|질문)\s*(합니다|해요|드립니다|드려요)?",
    r"(에 대해|에 대해서|에 관해|에 관하여)\s*(알고 싶어요|알고싶어요)?",
    r"\s*(좀|좀요|요|이요|이에요|예요|인가요|인가요\?)\s*$",
    r"(어떤|무슨|뭔|뭐)\s+(절차|기준|방법|방식|내용)",
    r"\?$",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  QueryRewriter 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QueryRewriter:
    """
    규칙 기반 쿼리 정규화기.

    [사용 예시]
        rewriter = QueryRewriter()
        result = rewriter.rewrite("연차 어떻게 써요?")
        # → RewriteResult(
        #       original="연차 어떻게 써요?",
        #       rewritten="연차휴가 신청",
        #       expanded="연차휴가 신청 휴가 신청 절차 연차 부여 기준",
        #       rewrites_applied=["TERM_MAP", "NOISE_REMOVE", "EXPAND"]
        #   )
    """

    def rewrite(self, query: str) -> "RewriteResult":
        """
        3단계 쿼리 정규화.

        Returns:
            RewriteResult (원본, 정규화, 확장, 적용 규칙 목록)
        """
        original     = query.strip()
        current      = original
        applied: List[str] = []

        # ── Step 1: 구어체 → 문서어 치환 ───────────────────────
        current, replaced = self._apply_term_map(current)
        if replaced:
            applied.append(f"TERM_MAP:{replaced}")

        # ── Step 2: 노이즈 어미·조사 제거 ──────────────────────
        cleaned = _NOISE_RE.sub(" ", current).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)   # 다중 공백 정리
        if cleaned != current:
            current = cleaned
            applied.append("NOISE_REMOVE")

        # ── Step 3: 키워드 확장 ─────────────────────────────────
        expanded, exp_terms = self._apply_expand(current)
        if exp_terms:
            applied.append(f"EXPAND:{exp_terms}")

        rewritten = current if current else original

        if applied:
            logger.debug(
                f"QueryRewriter: '{original}' → '{rewritten}' "
                f"(rules: {applied})"
            )

        return RewriteResult(
            original         = original,
            rewritten        = rewritten,
            expanded         = expanded,
            rewrites_applied = applied,
        )

    def _apply_term_map(self, text: str) -> tuple[str, Optional[str]]:
        """구어체 → 문서어 사전 치환 (긴 것부터 매칭)"""
        result   = text
        replaced = None
        # 긴 표현부터 매칭 (탐욕적 매칭 방지)
        for src, dst in sorted(TERM_MAP.items(), key=lambda x: -len(x[0])):
            if src in result:
                result   = result.replace(src, dst)
                replaced = f"{src}→{dst}"
                break   # 첫 번째 매칭만 적용 (안전)
        return result, replaced

    def _apply_expand(self, text: str) -> tuple[str, Optional[str]]:
        """검색 공간 확장: 관련 용어 추가"""
        for keyword, expansions in EXPAND_MAP.items():
            if keyword in text:
                extra   = " ".join(expansions)
                expanded = f"{text} {extra}"
                return expanded, keyword
        return text, None


# ── 결과 데이터클래스 ─────────────────────────────────

from dataclasses import dataclass, field as dc_field


@dataclass
class RewriteResult:
    original:         str
    rewritten:        str                # FAISS 검색에 사용할 쿼리
    expanded:         str                # 확장 쿼리 (추가 검색 시 활용)
    rewrites_applied: List[str] = dc_field(default_factory=list)

    @property
    def was_rewritten(self) -> bool:
        return bool(self.rewrites_applied)

    @property
    def search_query(self) -> str:
        """FAISS 검색에 실제로 사용할 쿼리"""
        return self.rewritten


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  싱글톤 접근자
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@lru_cache(maxsize=1)
def get_query_rewriter() -> QueryRewriter:
    """앱 기동 중 단일 인스턴스 반환"""
    return QueryRewriter()