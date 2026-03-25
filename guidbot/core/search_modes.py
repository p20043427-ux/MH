"""
core/search_modes.py  ─  검색 모드 정의 및 설정 관리 (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[검색 모드 개요]
  병원 업무 환경에서는 요청 성격에 따라 적절한 검색 전략이 다릅니다.

  ⚡ Fast   (빠른 검색)  : Vector similarity 단독, top_k=3
  ⚖️ Balanced (표준 검색): Vector + CE 리랭킹,  top_k=10
  🧠 Deep   (심층 검색)  : Hybrid + CE + Query Expansion, top_k=20

[설계 원칙]
  · SearchConfig 는 불변 dataclass — 실수로 값이 변경되지 않음
  · 모든 파이프라인 함수는 SearchConfig 한 개만 받으면 동작
  · main.py ↔ sidebar.py ↔ rag_pipeline.py 간 타입 안전 공유

[성능 예상치 (CPU, 7845청크 기준)]
  Fast     :  0.5 ~ 1.5초   (FAISS top-3, CE 없음)
  Balanced :  2.5 ~ 4.0초   (FAISS top-10 + CE 리랭킹)
  Deep     :  4.0 ~ 7.0초   (Hybrid top-20 + CE + 쿼리 확장)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 검색 모드 열거형
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SearchMode(str, Enum):
    """
    사용자가 선택 가능한 검색 전략 열거형.

    str 을 상속하여 JSON 직렬화 및 st.radio 옵션으로 직접 사용 가능.
    예: SearchMode.FAST == "fast"  →  JSON key 로 저장 가능
    """

    FAST = "fast"  # ⚡ 빠른 검색
    BALANCED = "balanced"  # ⚖️ 표준 검색
    DEEP = "deep"  # 🧠 심층 검색


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 검색 설정 데이터클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class SearchConfig:
    """
    검색 전략을 결정하는 불변 설정 객체.

    [frozen=True 이유]
      파이프라인 실행 도중 설정이 변경되면 예측 불가능한 동작이 발생합니다.
      불변 객체로 만들어 멀티스레드 환경(Streamlit)에서도 안전하게 공유합니다.

    Attributes:
        mode:             SearchMode 열거값 (식별용)
        label:            UI 표시 레이블 (예: "⚡ 빠른 검색")
        description:      UI 설명 텍스트
        top_k:            FAISS / Hybrid 초기 후보 수
        rerank_top_n:     CE 리랭킹 후 최종 반환 수
        use_rerank:       Cross-Encoder 리랭킹 사용 여부
        use_hybrid:       BM25 + FAISS 하이브리드 검색 여부
        use_query_expand: 쿼리 확장(동의어·관련어 추가) 여부
        expected_latency: UI 표시용 예상 응답 시간 (사용자 안내)
        color:            UI 강조 색상 (hex)
        icon:             모드 아이콘 이모지
    """

    mode: SearchMode
    label: str
    description: str
    top_k: int
    rerank_top_n: int
    use_rerank: bool
    use_hybrid: bool
    use_query_expand: bool
    expected_latency: str  # "약 1초", "약 3초" 등 사용자 표시용
    color: str  # hex (#2196F3 등)
    icon: str  # 이모지


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 모드별 사전 정의 설정 (싱글톤)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#  ⚡ Fast Mode
#  ─────────────────────────────────────────────────────
#  · FAISS L2 유사도 top-3 만 사용
#  · CE 리랭킹·BM25·쿼리확장 생략으로 I/O 최소화
#  · 적합 상황: 명확한 키워드 질문, 응답 속도가 중요한 경우
FAST_CONFIG = SearchConfig(
    mode=SearchMode.FAST,
    label=" 빠른 검색",
    description="벡터 유사도 검색. 응답 최우선.",
    top_k=3,
    rerank_top_n=3,  # fast 는 rerank 생략이므로 top_k == top_n
    use_rerank=False,
    use_hybrid=False,
    use_query_expand=False,
    expected_latency="약 1초",
    color="#1976D2",  # 파란색 계열
    icon="",
)

#  ⚖️ Balanced Mode  (기본값)
#  ─────────────────────────────────────────────────────
#  · FAISS top-10 후보 → CE 리랭킹으로 top-3 정제
#  · 속도와 정확도의 최적 균형점
#  · 적합 상황: 일반적인 규정 질문, 대부분의 업무 질의
BALANCED_CONFIG = SearchConfig(
    mode=SearchMode.BALANCED,
    label=" 표준 검색",
    description="벡터 검색 + AI 리랭킹. 기본 추천.",
    top_k=10,
    rerank_top_n=3,
    use_rerank=True,
    use_hybrid=False,
    use_query_expand=False,
    expected_latency="약 3초",
    color="#388E3C",  # 초록색 계열
    icon="",
)

#  🧠 Deep Mode
#  ─────────────────────────────────────────────────────
#  · BM25 + FAISS → RRF 병합 top-20 후보
#  · CE 리랭킹 top-5 정제
#  · QueryRewriter 확장 쿼리 적용
#  · 적합 상황: 복잡한 규정 해석, 여러 조항 비교, 희귀 키워드
DEEP_CONFIG = SearchConfig(
    mode=SearchMode.DEEP,
    label="심층 검색",
    description="하이브리드 + 쿼리 확장. 최고 정확도.",
    top_k=20,
    rerank_top_n=5,
    use_rerank=True,
    use_hybrid=True,
    use_query_expand=True,
    expected_latency="약 6초",
    color="#7B1FA2",  # 보라색 계열
    icon="",
)

# ── 모드 → 설정 매핑 딕셔너리 ────────────────────────────
MODE_CONFIGS: Dict[SearchMode, SearchConfig] = {
    SearchMode.FAST: FAST_CONFIG,
    SearchMode.BALANCED: BALANCED_CONFIG,
    SearchMode.DEEP: DEEP_CONFIG,
}

# ── UI 표시 순서 (radio 버튼 순서와 동일) ─────────────────
MODE_ORDER: list[SearchMode] = [
    SearchMode.FAST,
    SearchMode.BALANCED,
    SearchMode.DEEP,
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 편의 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def get_config(mode: SearchMode) -> SearchConfig:
    """
    SearchMode 로 SearchConfig 를 반환합니다.

    Args:
        mode: SearchMode 열거값

    Returns:
        해당 모드의 SearchConfig (불변 객체)

    Raises:
        KeyError: 등록되지 않은 mode 가 전달된 경우
    """
    return MODE_CONFIGS[mode]


def get_default_config() -> SearchConfig:
    """
    기본 검색 설정 (Balanced) 반환.

    [기본값을 Balanced 로 선택한 이유]
    · Fast: 정확도 낮아 의료 규정 업무에 부적합
    · Deep: 첫 사용자에게 느린 경험 제공
    · Balanced: 대부분 상황에서 충분한 정확도 + 합리적 속도
    """
    return BALANCED_CONFIG


def mode_from_label(label: str) -> SearchMode:
    """
    UI 레이블 문자열 → SearchMode 변환.

    Streamlit st.radio 는 레이블 문자열을 반환하므로
    SearchMode 로 변환할 때 사용합니다.

    Args:
        label: "⚡ 빠른 검색" 형태의 레이블 문자열

    Returns:
        해당 SearchMode

    Raises:
        ValueError: 일치하는 모드 없음
    """
    for cfg in MODE_CONFIGS.values():
        if cfg.label == label:
            return cfg.mode
    raise ValueError(f"알 수 없는 검색 모드 레이블: '{label}'")


def all_labels() -> list[str]:
    """UI radio 버튼용 레이블 목록 반환 (Fast → Balanced → Deep 순)."""
    return [MODE_CONFIGS[m].label for m in MODE_ORDER]
