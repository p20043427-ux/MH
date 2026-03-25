"""
core/embeddings.py ─ 임베딩 모델 관리 (v3.0)

[v3.0 수정사항]
- P-01 버그 수정 (CRITICAL): detect_device() 에 @lru_cache 추가
  이전: detect_device() 에 캐시 없음 → vector_store.py 의 배치 루프 안에서
        self._embeddings 프로퍼티를 호출할 때마다 detect_device() 가 실행됨
        → torch.cuda.is_available() 이 배치(100개씩)마다 반복 호출
        → 1000개 문서 = 10배치 = 10번의 불필요한 GPU 탐지 실행
  수정: @lru_cache(maxsize=1) 추가 → 최초 1회만 실행 후 결과 캐시

[설계 원칙]
- 로컬 HuggingFace 모델 사용 → 외부 API 비용·쿼터 제로
- GPU(CUDA) > Apple Silicon(MPS) > CPU 자동 감지 및 선택
- @lru_cache 싱글톤으로 동일 설정에서 모델 중복 로딩 방지
- 실패 시 EmbeddingError 로 명확히 전파 (조용히 실패하지 않음)

[선택된 기본 모델: jhgan/ko-sroberta-multitask]
- 한국어 의미 검색(Semantic Search)에 최적화된 Sentence-BERT 계열
- KLUE, KorSTS 벤치마크에서 높은 성능
- 768차원 임베딩, 코사인 유사도 정규화(normalize_embeddings=True) 지원
- 용량 약 350MB, CPU 에서도 실용적인 속도 (약 100문장/초)

[임베딩 파이프라인]
텍스트 입력
  → HuggingFaceEmbeddings.embed_documents()
  → sentence-transformers SentenceTransformer.encode()
  → L2 정규화 (normalize_embeddings=True)
  → 768차원 float32 벡터 반환
  → FAISS 인덱스에 저장
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from langchain_huggingface import HuggingFaceEmbeddings

from config.settings import settings
from utils.exceptions import EmbeddingError
from utils.logger import get_logger

logger = get_logger(__name__, log_dir=settings.log_dir)

# 지원 디바이스 타입 (Literal 로 타입 힌트 강제)
DeviceType = Literal["cpu", "cuda", "mps"]


@lru_cache(maxsize=1)
def detect_device() -> DeviceType:
    """
    사용 가능한 최적 연산 디바이스를 자동 탐지합니다.

    [P-01 수정] @lru_cache(maxsize=1) 추가
    이전 버전: 캐시 없이 매번 torch.cuda.is_available() 호출
    → vector_store._build_in_batches() 의 배치 루프 안에서
      self._embeddings 프로퍼티 → get_embeddings_auto() → detect_device() 순으로
      배치마다 GPU 탐지가 반복 실행되는 성능 낭비 발생.
    → lru_cache 로 최초 1회만 실행 후 결과를 메모리에 캐시.

    [탐지 우선순위]
    1. CUDA (NVIDIA GPU): 가장 빠름. VRAM 이 충분하면 CPU 대비 10~50배 속도
    2. MPS (Apple Silicon): M1/M2/M3 맥의 Neural Engine 활용
    3. CPU: 폴백. 느리지만 모든 환경에서 동작 보장

    Returns:
        "cuda" | "mps" | "cpu" 중 하나

    Example::

        device = detect_device()   # "cuda" 또는 "cpu"
        print(f"사용 디바이스: {device}")
    """
    try:
        import torch  # torch 미설치 환경 대비 (try-except 안에서 import)

        if torch.cuda.is_available():
            # NVIDIA GPU 감지 → 모델명도 로그에 기록
            device_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            logger.info(f"GPU 감지됨: {device_name} ({vram_gb:.1f}GB VRAM) → CUDA 모드")
            return "cuda"

        if torch.backends.mps.is_available():
            # Apple Silicon (M1/M2/M3) 감지
            logger.info("Apple Silicon GPU 감지됨 → MPS 모드")
            return "mps"

    except ImportError:
        # torch 미설치: sentence-transformers 가 내부적으로 설치할 수 있으므로 경고만
        logger.warning("torch 미설치 → CPU 모드로 폴백")

    logger.info("GPU 미감지 → CPU 모드 (느리지만 안전)")
    return "cpu"


@lru_cache(maxsize=1)
def get_embeddings(device: DeviceType = "cpu") -> HuggingFaceEmbeddings:
    """
    로컬 HuggingFace 임베딩 모델 싱글톤을 반환합니다.

    @lru_cache(maxsize=1): device 파라미터가 동일하면 캐시된 인스턴스 재사용.
    앱 전체에서 동일한 임베딩 모델 인스턴스를 공유합니다.

    [캐시 효과]
    - 모델 로딩 시간(3~10초)이 최초 1회만 소요
    - 이후 호출은 이미 메모리에 로드된 모델 즉시 반환 (ms 수준)
    - GPU 메모리에 한 번만 올라가므로 VRAM 낭비 없음

    [encode_kwargs 설명]
    - normalize_embeddings=True: 출력 벡터를 L2 정규화
      → 코사인 유사도 = 내적(dot product) 이 되어 계산 간소화
      → FAISS 의 IndexFlatL2 와 함께 사용 시 정확한 코사인 유사도 보장
    - batch_size 를 encode_kwargs 에 포함하지 않는 이유:
      HuggingFaceEmbeddings 내부에서 batch_size 를 자체 관리하며,
      encode_kwargs 로 전달 시 일부 버전에서 TypeError 발생 (BUG#9 수정 내용)

    Args:
        device: 연산 디바이스 ("cpu" | "cuda" | "mps")

    Returns:
        HuggingFaceEmbeddings 인스턴스 (LangChain 호환)

    Raises:
        EmbeddingError: 모델 파일 없음, 다운로드 실패, 초기화 오류 등

    Example::

        emb = get_embeddings("cuda")
        vectors = emb.embed_documents(["연차휴가 신청 방법", "병가 처리 절차"])
    """
    model_name = settings.embedding_model
    logger.info(f"임베딩 모델 로딩 시작 (모델={model_name}, 디바이스={device})")

    # HuggingFace 모델 캐시 경로 설정
    # 이 환경변수가 없으면 기본 ~/.cache/torch/sentence_transformers/ 에 저장됨
    # settings 의 local_cache_path 로 통일하여 관리 용이성 향상
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(settings.local_cache_path)

    try:
        embeddings = HuggingFaceEmbeddings(
            model_name=model_name,

            # model_kwargs: sentence-transformers SentenceTransformer 초기화 인자
            model_kwargs={
                "device": device,   # "cpu", "cuda", "mps" 중 하나
            },

            # encode_kwargs: SentenceTransformer.encode() 호출 시 전달되는 인자
            encode_kwargs={
                "normalize_embeddings": True,
                # normalize_embeddings=True 이유:
                # - 코사인 유사도 = 정규화된 벡터의 내적 (dot product)
                # - FAISS similarity_search 시 거리 계산 일관성 보장
                # - 유사도 점수가 항상 [-1, 1] 범위에 있어 해석 용이
                #
                # [주의] batch_size 는 여기에 포함하지 않음 (BUG#9 수정)
                # HuggingFaceEmbeddings 가 내부적으로 batch_size 를 관리하며,
                # encode_kwargs 에 포함 시 일부 버전에서 TypeError 발생
            },

            # cache_folder: 모델 파일(.bin) 저장 경로 (다운로드 캐시)
            cache_folder=str(settings.local_cache_path),
        )

        logger.info(
            f"임베딩 모델 로딩 완료 "
            f"(캐시 경로: {settings.local_cache_path})"
        )
        return embeddings

    except Exception as exc:
        # EmbeddingError 로 래핑하여 상위에서 일관되게 처리
        logger.error(f"임베딩 모델 로딩 실패: {exc}", exc_info=True)
        raise EmbeddingError(model=model_name, reason=str(exc)) from exc


def get_embeddings_auto() -> HuggingFaceEmbeddings:
    """
    디바이스를 자동 감지하여 임베딩 모델을 반환합니다.

    대부분의 호출부에서 이 함수를 사용합니다.
    내부적으로 detect_device() → get_embeddings(device) 를 순서대로 호출합니다.

    [성능 특성]
    - detect_device(): @lru_cache 로 최초 1회만 GPU 탐지 실행 (P-01 수정)
    - get_embeddings(): @lru_cache 로 동일 디바이스에서 모델 중복 로딩 방지
    - 두 함수 모두 캐시되므로 반복 호출해도 성능 부담 없음

    Returns:
        HuggingFaceEmbeddings 인스턴스

    Example::

        # vector_store.py, retriever.py 등에서 사용
        embeddings = get_embeddings_auto()
        db = FAISS.from_documents(docs, embeddings)
    """
    device = detect_device()   # @lru_cache: 최초 1회만 GPU 탐지
    return get_embeddings(device)  # @lru_cache: 동일 device 는 캐시 반환
