"""
core/llm.py ─ Gemini LLM 클라이언트 with API 키 풀 (v4.0)

[v4.0 신규: API 키 풀 자동 교체]
최대 5개의 Google API 키를 순환하여 할당량 초과(429) 시 자동으로 다음 키로 전환합니다.

[키 풀 동작 방식]
  KEY_1 → 429 → KEY_2 → 429 → KEY_3 → ... → KEY_5 → 429 → LLMQuotaError

  · 쿨다운: 한 번 소진된 키는 60분간 재사용 금지 (기본값, QUOTA_COOLDOWN_SEC 로 조정)
  · 자정 리셋: Google API 할당량은 매일 자정(태평양 표준시) 초기화되므로
    쿨다운 1시간이면 대부분 커버됨
  · 모든 키 소진 시: LLMQuotaError 발생 (상위 레이어에서 사용자에게 안내)

[설정 방법 (.env)]
  GOOGLE_API_KEY=AIza...       <- 필수, 기본 키
  GOOGLE_API_KEY_2=AIza...     <- 선택
  GOOGLE_API_KEY_3=AIza...     <- 선택
  GOOGLE_API_KEY_4=AIza...     <- 선택
  GOOGLE_API_KEY_5=AIza...     <- 선택

[v3.1 속도 최적화 유지]
  · thinking_budget=0: gemini-2.5-flash 의 추론 단계 비활성화 (21초 → 3~5초)
  · max_output_tokens=1024: 장황한 답변 방지
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Generator, List, Optional

import google.genai as genai
from google.genai import types as genai_types

from config.settings import settings
from utils.exceptions import LLMError, LLMQuotaError
from utils.logger import get_logger, ContextLogger

logger = get_logger(__name__, log_dir=settings.log_dir)

# 소진된 키의 쿨다운 시간(초). 기본 60분
# Google 1분 할당량은 60초 후 복구, 1일 할당량은 자정 후 복구
# 1시간 쿨다운은 두 경우 모두 합리적으로 대응
QUOTA_COOLDOWN_SEC: int = 3600


# ──────────────────────────────────────────────────────────────────────
#  시스템 프롬프트
# ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
당신은 병원의 행정 / 간호 / 의료  및 규정 안내를 담당하는 전문 AI 가이드봇입니다.
사용자의 질문에 대해 반드시 제공된 [참조 문서]의 내용만을 기반으로 답변하십시오.

[답변 원칙]
1. 참조 문서에 없는 내용은 절대 지어내지 마십시오.
   정보가 없다면 "제공된 지침에서 해당 내용을 찾을 수 없습니다."라고 정중히 답변 한뒤  일반적인 안내로 대체하십시오. (예: "제공된 지침에서는 해당 내용을 찾을 수 없습니다. 하지만 일반적으로 병원에서는 ...")
2. 병원 직원이나 환자에게 안내하듯 친절하고 전문적인 톤(하십시오체, 해요체)을 사용하십시오.
3. 답변은 아래의 [출력 형식]을 엄격히 준수하여 가독성 좋게 작성하십시오.
4. 답변은 핵심만 간결하게 작성하십시오. 불필요한 반복이나 과도한 설명은 피하십시오.

[출력 형식]
✅ **핵심 요약:** (질문에 대한 명확하고 간결한 답변을 2~3문장으로 작성)

📝 **상세 내용:**
(필요시 부연 설명, 절차, 세부 규정 등을 불릿 포인트로 나열)

🔗 **출처:** [파일명] (관련 조항, 페이지 등 명시)
""".strip()


def _build_prompt(query: str, context: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"---\n\n"
        f"### [참조 문서]\n{context}\n\n"
        f"---\n\n"
        f"### [사용자 질문]\n{query}"
    )


def _make_generate_config() -> genai_types.GenerateContentConfig:
    """Gemini 생성 설정 (thinking OFF, 토큰 제한)"""
    cfg: dict = {
        "max_output_tokens": settings.llm_max_output_tokens,
        "temperature": 0.1,
    }
    if settings.llm_thinking_disabled:
        try:
            cfg["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=0)
        except AttributeError:
            logger.debug("ThinkingConfig 미지원 버전 → thinking 설정 건너뜀")
    return genai_types.GenerateContentConfig(**cfg)


# ──────────────────────────────────────────────────────────────────────
#  API 키 풀 관리
# ──────────────────────────────────────────────────────────────────────


@dataclass
class _KeySlot:
    """API 키 하나의 상태를 추적하는 슬롯."""

    key: str
    exhausted_at: Optional[float] = field(default=None)

    @property
    def suffix(self) -> str:
        """로그·UI 표시용 마스킹 (마지막 4자리만)"""
        return f"...{self.key[-4:]}" if len(self.key) >= 4 else "****"

    def is_available(self) -> bool:
        """현재 사용 가능한지 반환. 쿨다운 지나면 자동 복구."""
        if self.exhausted_at is None:
            return True
        if time.time() - self.exhausted_at >= QUOTA_COOLDOWN_SEC:
            self.exhausted_at = None
            return True
        return False

    def mark_exhausted(self) -> None:
        self.exhausted_at = time.time()

    def remaining_cooldown(self) -> int:
        """남은 쿨다운 시간(초)."""
        if self.exhausted_at is None:
            return 0
        return max(0, int(QUOTA_COOLDOWN_SEC - (time.time() - self.exhausted_at)))


class ApiKeyPool:
    """
    Google API 키 풀 — 할당량 초과 시 자동 교체.

    [탐색 전략] 현재 인덱스부터 순환 탐색, 쿨다운 키는 건너뜀
    [스레드 안전] RLock 으로 동시 갱신 충돌 방지
    """

    def __init__(self, keys: List[str]) -> None:
        if not keys:
            raise ValueError(
                "API 키 풀이 비어있습니다. .env 에 GOOGLE_API_KEY 를 설정하세요."
            )
        self._slots: List[_KeySlot] = [_KeySlot(key=k) for k in keys]
        self._current_idx: int = 0
        self._lock = threading.RLock()
        logger.info(
            f"API 키 풀 초기화: {len(self._slots)}개 키 "
            f"({', '.join(s.suffix for s in self._slots)})"
        )

    @classmethod
    def from_settings(cls) -> "ApiKeyPool":
        return cls(settings.get_api_key_pool())

    def get_available_key(self) -> Optional[str]:
        """사용 가능한 키 반환. 모두 소진이면 None."""
        with self._lock:
            n = len(self._slots)
            for offset in range(n):
                idx = (self._current_idx + offset) % n
                if self._slots[idx].is_available():
                    return self._slots[idx].key
            return None

    def mark_key_exhausted(self, key: str) -> None:
        """키를 소진 처리하고 다음 키로 인덱스 이동."""
        with self._lock:
            for i, slot in enumerate(self._slots):
                if slot.key == key:
                    slot.mark_exhausted()
                    remaining_min = slot.remaining_cooldown() // 60
                    logger.warning(
                        f"API 키 소진 [{slot.suffix}] → "
                        f"약 {remaining_min}분 후 자동 복구"
                    )
                    self._current_idx = (i + 1) % len(self._slots)
                    break

    def available_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots if s.is_available())

    def total_count(self) -> int:
        return len(self._slots)

    def status(self) -> List[dict]:
        """사이드바 모니터링용 상태 목록."""
        with self._lock:
            return [
                {
                    "suffix": s.suffix,
                    "available": s.is_available(),
                    "cooldown_min": s.remaining_cooldown() // 60,
                }
                for s in self._slots
            ]


# ──────────────────────────────────────────────────────────────────────
#  전역 키 풀 싱글톤
# ──────────────────────────────────────────────────────────────────────

_key_pool_instance: Optional[ApiKeyPool] = None
_key_pool_lock = threading.Lock()


def get_key_pool() -> ApiKeyPool:
    """API 키 풀 싱글톤 (스레드 안전 지연 초기화)."""
    global _key_pool_instance
    if _key_pool_instance is None:
        with _key_pool_lock:
            if _key_pool_instance is None:
                _key_pool_instance = ApiKeyPool.from_settings()
    return _key_pool_instance


# ──────────────────────────────────────────────────────────────────────
#  Gemini 클라이언트
# ──────────────────────────────────────────────────────────────────────


class GeminiClient:
    """
    Google Gemini API 스트리밍 클라이언트 (v4.0 키 풀 지원).

    [429 발생 시 동작]
    1. 현재 키 소진 처리
    2. 풀에서 다음 사용 가능 키 획득
    3. 즉시 재시도 (대기 없음)
    4. 모든 키 소진 → LLMQuotaError
    """

    def __init__(
        self,
        model_name: str = settings.chat_model,
        max_retries: int = 3,
    ) -> None:
        self.model_name = model_name
        self.max_retries = max_retries
        self._gen_config = _make_generate_config()

        pool = get_key_pool()
        logger.info(
            f"GeminiClient 초기화 완료 "
            f"(모델={model_name}, "
            f"thinking={'OFF' if settings.llm_thinking_disabled else 'ON'}, "
            f"max_tokens={settings.llm_max_output_tokens}, "
            f"API 키={pool.available_count()}/{pool.total_count()}개 사용 가능)"
        )

    def generate_stream(
        self,
        query: str,
        context: str,
        request_id: str = "",
    ) -> Generator[str, None, None]:
        """
        Gemini API 스트리밍 응답 제너레이터 (키 풀 자동 전환).

        Yields:
            str: 생성된 텍스트 청크

        Raises:
            LLMQuotaError: 모든 API 키의 할당량 소진
            LLMError:      최대 재시도 초과
        """
        log = ContextLogger(logger, request_id=request_id) if request_id else logger
        prompt = _build_prompt(query, context)
        pool = get_key_pool()
        last_exc: Optional[Exception] = None

        # 총 시도 상한: 키 수 × 재시도 수 (무한 루프 방지)
        max_total = pool.total_count() * self.max_retries
        attempt = 0

        while attempt < max_total:
            current_key = pool.get_available_key()
            if current_key is None:
                exhausted_info = ", ".join(
                    f"{s['suffix']}(복구까지 {s['cooldown_min']}분)"
                    for s in pool.status()
                )
                log.error(f"모든 API 키 소진: {exhausted_info}")
                raise LLMQuotaError()

            attempt += 1
            key_suffix = f"...{current_key[-4:]}"

            try:
                log.info(
                    f"Gemini 스트리밍 시작 "
                    f"(키={key_suffix}, 시도={attempt}/{max_total})"
                )
                start = time.time()
                token_count = 0

                client = genai.Client(api_key=current_key)
                response = client.models.generate_content_stream(
                    model=self.model_name,
                    contents=prompt,
                    config=self._gen_config,
                )

                for chunk in response:
                    if chunk.text:
                        token_count += len(chunk.text)
                        yield chunk.text

                elapsed = time.time() - start
                log.info(
                    f"Gemini 완료 (키={key_suffix}, {elapsed:.1f}초, {token_count:,}자)"
                )

                if settings.monitoring_enabled:
                    try:
                        from utils.monitor import metrics

                        metrics.record_stream(elapsed, token_count)
                    except Exception:
                        pass

                return  # 성공

            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()

                # ── 429 할당량 초과: 다음 키로 즉시 전환 ─────────────
                if (
                    "429" in exc_str
                    or "quota" in exc_str
                    or "resource_exhausted" in exc_str
                ):
                    pool.mark_key_exhausted(current_key)
                    avail = pool.available_count()
                    if avail > 0:
                        log.warning(
                            f"키 [{key_suffix}] 할당량 초과 → "
                            f"다음 키로 전환 (남은 키: {avail}개)"
                        )
                        continue  # 대기 없이 즉시 다음 키
                    else:
                        raise LLMQuotaError() from exc

                # ── 일반 오류: 지수 백오프 ────────────────────────────
                wait_sec = 2 ** min(attempt, 4)  # 최대 16초
                log.warning(
                    f"LLM 오류 (키={key_suffix}): {exc} → {wait_sec}초 후 재시도"
                )
                time.sleep(wait_sec)

        raise LLMError(f"최대 시도({max_total}회) 초과: {last_exc}") from last_exc

    def generate(self, query: str, context: str, request_id: str = "") -> str:
        return "".join(self.generate_stream(query, context, request_id=request_id))


# ──────────────────────────────────────────────────────────────────────
#  전역 싱글톤
# ──────────────────────────────────────────────────────────────────────

_client_instance: Optional[GeminiClient] = None
_client_lock = threading.Lock()


def get_llm_client() -> GeminiClient:
    """GeminiClient 싱글톤 (스레드 안전 지연 초기화)."""
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = GeminiClient()
    return _client_instance
