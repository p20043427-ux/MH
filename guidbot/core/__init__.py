"""
core 패키지 ─ RAG 파이프라인 핵심 모듈

[패키지 구성]
  document_loader  : PDF 로드 + 텍스트 전처리 + 청킹
  embeddings       : HuggingFace 임베딩 모델 관리
  vector_store     : FAISS 벡터 DB 구축·로드·백업
  retriever        : FAISS 검색 + Cross-Encoder 리랭킹
  hybrid_retriever : BM25 + FAISS 하이브리드 검색
  query_rewriter   : LLM 기반 쿼리 확장·정제
  context_builder  : 검색 결과 → LLM 컨텍스트 변환
  rag_pipeline     : 위 모듈들을 통합한 단일 파이프라인
  llm              : Gemini LLM 클라이언트 (API 키 풀)

[빠른 사용]
  # 파이프라인 실행
  from core.rag_pipeline import RAGPipeline, get_pipeline

  # 벡터 DB 관리
  from core.vector_store import VectorStoreManager

  # 검색 결과 타입
  from core.retriever import RankedDocument
"""

from core.retriever import RankedDocument
from core.rag_pipeline import RAGPipeline, PipelineResult, get_pipeline
from core.vector_store import VectorStoreManager
from core.llm import get_llm_client

__all__ = [
    "RankedDocument",
    "RAGPipeline",
    "PipelineResult",
    "get_pipeline",
    "VectorStoreManager",
    "get_llm_client",
]
