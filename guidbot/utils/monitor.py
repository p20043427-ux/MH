"""
utils/monitor.py ─ 성능 메트릭 수집 모듈 (v2.0 Streamlit 세션 완전 통합)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v1.0 → v2.0 핵심 변경: 데이터 영속성 문제 해결]

■ 문제 원인 (v1.0):
  Streamlit 은 매 사용자 인터랙션마다 전체 스크립트를 재실행합니다.
  v1.0 의 모듈 레벨 싱글톤 `metrics = MetricsCollector()` 는
  일부 Streamlit 버전·배포 환경에서 세션 간 데이터가 초기화됩니다.
  결과: 사이드바에 항상 "총 질문 0회, 아직 질문 기록이 없습니다" 표시.

■ 해결 방법 (v2.0):
  get_metrics() 팩토리 함수를 통해 MetricsCollector 를 st.session_state 에
  저장합니다. 동일 세션 내에서는 앱이 수십 번 rerun 되어도 데이터 유지.

■ query_count 2배 증가 버그 수정:
  v1.0: record_search() 에서 query_count +1, 이후 record_stream() 도 호출
        → main.py 에서 두 함수 모두 호출하면 질문 1개당 2회 카운트됨
  v2.0: record_search() 에서만 query_count +1
        record_stream() 은 시간/토큰 데이터만 추가 (카운트 없음)

[수집 지표]
  query_count   : 누적 질문 수 (record_search 1회 호출 = 정확히 +1)
  error_count   : 누적 오류 수
  search_times  : 최근 50회 RAG 검색 소요 시간 (초)
  stream_times  : 최근 50회 LLM 스트리밍 소요 시간 (초)
  token_counts  : 최근 50회 LLM 응답 글자 수
  last_queries  : 최근 10회 질문 미리보기 (30자)

[사용 방법 — 신규 코드]
    from utils.monitor import get_metrics

    m = get_metrics()
    m.record_search(elapsed_sec, query="연차휴가 신청 방법")
    m.record_stream(stream_elapsed_sec, token_count=450)
    m.record_error()

    stats = m.get_stats()
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, List

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_WINDOW_SIZE  = 50   # 검색/응답 시간 슬라이딩 윈도우 크기
_QUERY_WINDOW = 10   # 최근 질문 보관 수
_SESSION_KEY  = "_guidbot_metrics"  # st.session_state 저장 키


class MetricsCollector:
    """
    애플리케이션 성능 메트릭 수집기 (v2.0).

    [중요] 직접 인스턴스화 금지. 반드시 get_metrics() 를 사용하세요.
    → st.session_state 에 저장되어 세션 내 영속성을 보장합니다.
    """

    def __init__(self) -> None:
        # ── 누적 카운터 ───────────────────────────────────────────────
        self._query_count: int = 0
        self._error_count: int = 0

        # ── 슬라이딩 윈도우 시간 기록 (초 단위, float) ────────────────
        self._search_times: Deque[float] = deque(maxlen=_WINDOW_SIZE)
        self._stream_times: Deque[float] = deque(maxlen=_WINDOW_SIZE)
        self._token_counts: Deque[int]   = deque(maxlen=_WINDOW_SIZE)

        # ── 최근 질문 미리보기 (개인정보 보호: 30자만 저장) ───────────
        self._last_queries: Deque[str] = deque(maxlen=_QUERY_WINDOW)

        # ── 스레드 안전성 (Streamlit 멀티스레드 환경 대응) ────────────
        self._lock = threading.RLock()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  쓰기 메서드 (스레드 안전)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def record_search(self, search_time: float, query: str = "") -> None:
        """
        RAG 검색 완료 후 호출. query_count +1, 검색 시간 + 질문 기록.

        [v2.0 수정] 이 메서드에서만 query_count 를 증가시킵니다.
        record_stream() 과 쌍으로 호출되므로 카운트 중복 방지.

        Args:
            search_time : RAG 검색 소요 시간 (초)
            query       : 원본 질문 (30자 초과 시 절삭)
        """
        with self._lock:
            self._query_count += 1          # ← 정확히 1회만 증가
            self._search_times.append(float(search_time))
            if query:
                preview = (query[:30] + "…") if len(query) > 30 else query
                self._last_queries.append(preview)

    def record_stream(self, stream_time: float, token_count: int = 0) -> None:
        """
        LLM 스트리밍 완료 후 호출. 응답 시간과 토큰 수 기록 (카운트 증가 없음).

        [v2.0 수정] query_count 를 증가시키지 않습니다.
        record_search() 에서 이미 카운트되었으므로 이중 계산 방지.

        Args:
            stream_time  : LLM 스트리밍 소요 시간 (초)
            token_count  : 생성된 응답 문자 수
        """
        with self._lock:
            self._stream_times.append(float(stream_time))
            if token_count > 0:
                self._token_counts.append(token_count)

    def record_query(
        self,
        query_preview: str,
        search_time:   float,
        stream_time:   float,
        token_count:   int,
    ) -> None:
        """
        검색 + 스트리밍을 한 번에 기록 (레거시 호환용).
        record_search() + record_stream() 의 통합 버전.
        """
        with self._lock:
            self._query_count += 1
            self._search_times.append(float(search_time))
            self._stream_times.append(float(stream_time))
            self._token_counts.append(token_count)
            preview = (query_preview[:20] + "…") if len(query_preview) > 20 else query_preview
            self._last_queries.append(preview)

    def record_error(self) -> None:
        """오류 발생 시 오류 카운터 +1."""
        with self._lock:
            self._error_count += 1

    def reset(self) -> None:
        """모든 메트릭 초기화 (관리자 패널에서 사용)."""
        with self._lock:
            self._query_count = 0
            self._error_count = 0
            self._search_times.clear()
            self._stream_times.clear()
            self._token_counts.clear()
            self._last_queries.clear()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  읽기 메서드 (집계 통계)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _avg_ms(times: Deque[float]) -> int:
        """소요 시간 목록의 평균을 ms 정수로 반환. 빈 경우 0."""
        if not times:
            return 0
        return int(sum(times) / len(times) * 1000)

    def get_stats(self) -> Dict[str, Any]:
        """
        현재까지 수집된 성능 통계를 thread-safe 딕셔너리로 반환.

        Returns:
            {
              "query_count"   : int    총 처리 질문 수
              "error_count"   : int    총 오류 수
              "error_rate"    : float  오류율 (0.0~1.0)
              "avg_search_ms" : int    평균 검색 시간 (ms)
              "avg_stream_ms" : int    평균 LLM 응답 시간 (ms)
              "avg_tokens"    : int    평균 응답 글자 수
              "last_queries"  : list   최근 질문 미리보기 (최신 순)
            }
        """
        with self._lock:
            qc = self._query_count
            ec = self._error_count
            error_rate = round(ec / qc, 3) if qc > 0 else 0.0
            avg_tokens = (
                int(sum(self._token_counts) / len(self._token_counts))
                if self._token_counts else 0
            )
            return {
                "query_count":   qc,
                "error_count":   ec,
                "error_rate":    error_rate,
                "avg_search_ms": self._avg_ms(self._search_times),
                "avg_stream_ms": self._avg_ms(self._stream_times),
                "avg_tokens":    avg_tokens,
                "last_queries":  list(reversed(self._last_queries)),
            }

    def get_recent_times(self) -> Dict[str, List[float]]:
        """최근 시간 기록 반환 (차트/스파크라인용)."""
        with self._lock:
            return {
                "search": list(self._search_times),
                "stream": list(self._stream_times),
            }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  세션 기반 팩토리 함수 (v2.0 핵심 API)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_metrics() -> MetricsCollector:
    """
    현재 Streamlit 세션의 MetricsCollector 를 반환합니다 (v2.0 핵심).

    [동작 원리]
    1. st.session_state["_guidbot_metrics"] 에 인스턴스가 있으면 → 반환
    2. 없으면 → 새 MetricsCollector 생성 + session_state 저장 → 반환

    [효과]
    · 동일 세션 내에서 수십 번 rerun 되어도 데이터 누적 유지 ✅
    · 새 브라우저 탭/세션: 깨끗한 새 인스턴스로 시작 ✅
    · 멀티 유저: 각자 독립적인 session_state → 데이터 격리 ✅

    [Streamlit 외부 환경 (pytest 등)]
    st 임포트 실패 시 → 모듈 레벨 폴백 인스턴스 반환.

    Returns:
        MetricsCollector: 현재 세션에 바인딩된 메트릭 수집기
    """
    try:
        import streamlit as st
        if _SESSION_KEY not in st.session_state:
            st.session_state[_SESSION_KEY] = MetricsCollector()
        return st.session_state[_SESSION_KEY]
    except Exception:
        # Streamlit 컨텍스트 외부 (테스트, CLI) → 모듈 레벨 폴백
        return _fallback_metrics


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  레거시 호환 전역 인스턴스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기존 코드의 `from utils.monitor import metrics` 를 위한 폴백.
# 신규 코드는 반드시 get_metrics() 를 사용하세요.
_fallback_metrics = MetricsCollector()
metrics = _fallback_metrics  # 레거시 호환용