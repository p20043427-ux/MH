"""
ui/theme.py  ─  좋은문화병원 가이드봇 디자인 시스템 v9.5 (가독성 패치)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[v9.5 변경 내용]
  · 사이드바 광범위 `*` 셀렉터 제거로 인한 버튼/입력창 텍스트 소멸 버그 수정
  · 사이드바 컴포넌트(입력창, 드롭존, 버튼) 배경 대비 및 가독성 최적화

[v9.0 변경 내용]
  · 전체 폰트 사이즈 1px 축소 (사용자 요청)
    - Heading  : 28px → 27px
    - Section  : 20px → 19px
    - Body     : 16px → 15px
    - Sidebar  : 14px → 13px
    - Caption  : 13px → 12px
  · 한국어 상세 주석 전면 보강

[이 파일의 역할]
  UITheme 클래스는 프로젝트 전체에서 사용하는
  '디자인 토큰'을 한 곳에 모아둔 파일입니다.

  디자인 토큰이란?
  → 색상, 폰트 크기, 간격, 그림자 등의 값을 변수로 만든 것.
  → 예: 버튼 색깔을 바꾸고 싶으면 이 파일의 PRIMARY 값만 바꾸면
        전체 앱에 한 번에 반영됩니다.

[사용 방법]
  from ui.theme import UITheme as T

  # 색상 사용 예시
  color = T.PRIMARY          # "#2563EB"
  bg    = T.SIDEBAR_BG       # "#0F172A"

  # 전역 CSS 주입 (main.py 에서 1회 호출)
  st.markdown(T.get_global_css(), unsafe_allow_html=True)
"""

from __future__ import annotations


class UITheme:
    """
    디자인 토큰 + 전역 CSS 주입 클래스 (v9.5)

    [클래스 변수]
    Python 클래스 변수로 정의하여 인스턴스 생성 없이
    UITheme.PRIMARY 처럼 바로 접근할 수 있습니다.

    [CSS 변수 연동]
    get_global_css() 메서드가 이 클래스의 값을 읽어서
    CSS :root 변수로 변환합니다.
    → 파이썬 변수와 CSS 변수가 항상 동기화됩니다.
    """

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  색상 시스템 (요구사항 §4 기준)
    #  [Tailwind CSS 색상 팔레트 기반]
    #  참고: https://tailwindcss.com/docs/customizing-colors
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # ── 주 색상 (Blue-600 계열) ─────────────────────────────
    # 버튼, 링크, 강조 요소에 사용하는 핵심 색상입니다.
    PRIMARY = "#2563EB"  # 주 색상 — Blue-600
    PRIMARY_DARK = "#1D4ED8"  # 호버 시 더 진한 파랑 — Blue-700
    PRIMARY_LIGHT = "#EFF6FF"  # 연한 파랑 배경 — Blue-50
    PRIMARY_MID = "#BFDBFE"  # 중간 파랑 테두리용 — Blue-200

    # ── 사이드바 배경 ──────────────────────────────────────
    # 왼쪽 사이드바의 배경색. 매우 어두운 네이비 사용.
    SIDEBAR_BG = "#0F172A"  # 사이드바 배경 — Slate-900

    # ── 카드 / 페이지 배경 ──────────────────────────────────
    CARD_BG = "#FFFFFF"  # 카드 배경 — 순백색
    PAGE_BG = "#F8FAFC"  # 페이지 배경 — 아주 연한 회색 (Slate-50)

    # ── 테두리 ─────────────────────────────────────────────
    BORDER = "#E5E7EB"  # 기본 테두리 색상 — Gray-200
    BORDER_FOCUS = "#2563EB"  # 입력창 포커스 테두리 — PRIMARY와 동일

    # ── 텍스트 계층 ────────────────────────────────────────
    # 텍스트는 중요도에 따라 4단계로 구분합니다.
    TEXT = "#111827"  # 1단계: 제목·주요 텍스트 — Gray-900 (가장 진함)
    TEXT_SECONDARY = "#374151"  # 2단계: 본문 텍스트 — Gray-700
    TEXT_MUTED = "#6B7280"  # 3단계: 설명·캡션 — Gray-500
    TEXT_HINT = "#9CA3AF"  # 4단계: 힌트·비활성 — Gray-400 (가장 연함)

    # ── 검색 모드 카드 색상 (요구사항 §1) ──────────────────
    # 사이드바의 빠른/표준/심층 검색 카드에 사용.
    # 각 모드마다 배경색(BG)과 텍스트색(TEXT)이 쌍을 이룹니다.
    SEARCH_FAST_BG = "#F0F9FF"  # 빠른 검색 카드 배경 — Sky-50
    SEARCH_FAST_TEXT = "#0C4A6E"  # 빠른 검색 카드 텍스트 — Sky-900
    SEARCH_STD_BG = "#F0FDF4"  # 표준 검색 카드 배경 — Green-50
    SEARCH_STD_TEXT = "#14532D"  # 표준 검색 카드 텍스트 — Green-900
    SEARCH_DEEP_BG = "#FEFCE8"  # 심층 검색 카드 배경 — Yellow-50
    SEARCH_DEEP_TEXT = "#713F12"  # 심층 검색 카드 텍스트 — Yellow-900
    SEARCH_SELECTED = "#2563EB"  # 선택된 카드 테두리 색상 — PRIMARY와 동일

    # ── 청록색 계열 (병원 브랜드 보조 컬러) ────────────────
    # 좋은문화병원 HI(Hospital Identity)에 맞는 청록색입니다.
    TEAL = "#0097B2"  # 청록 기본값
    TEAL_DARK = "#006F87"  # 청록 진한 버전 (호버용)
    TEAL_LIGHT = "#E0F7FC"  # 청록 연한 배경
    TEAL_SIDEBAR = "#26D4BF"  # 사이드바 청록 강조

    # 구버전 호환 별칭 (기존 코드가 T.A500 같은 이름을 사용하므로 유지)
    A500 = "#0097B2"
    A400 = "#00B4D0"
    A600 = "#006F87"
    A100 = "#B3E8F2"
    A50 = "#E0F7FC"

    # ── 상태 색상 ──────────────────────────────────────────
    # 성공/경고/오류/정보 상태를 나타내는 표준 색상입니다.
    SUCCESS = "#15803D"  # 성공 — Green-700
    WARNING = "#B45309"  # 경고 — Amber-700
    ERROR = "#B91C1C"  # 오류 — Red-700
    INFO = "#0097B2"  # 정보 — TEAL과 동일
    SUCCESS_SIDEBAR = "#4ADE80"  # 사이드바용 성공 (밝은 배경에서 사용)
    ERROR_SIDEBAR = "#FC8181"  # 사이드바용 오류

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  [v9.0] 폰트 사이즈 시스템 (5단계)
    #
    #  [왜 5단계인가?]
    #  UI의 텍스트가 너무 다양한 크기를 쓰면 시각적으로 혼란스럽습니다.
    #  5가지 크기로 제한하면 일관성이 생깁니다.
    #
    #  [v9.0 변경] 사용자 요청으로 모든 크기 1px 감소
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    FS_HEADING = "27px"  # 페이지 제목  (v9.0: 28px → 27px)
    FS_SECTION = "19px"  # 섹션 타이틀  (v9.0: 20px → 19px)
    FS_BODY = "15px"  # 본문 텍스트  (v9.0: 16px → 15px)
    FS_SIDEBAR = "13px"  # 사이드바     (v9.0: 14px → 13px)
    FS_CAPTION = "12px"  # 캡션·설명    (v9.0: 13px → 12px)

    # rem 단위 별칭 (1rem = 16px 기준, 1px = 0.0625rem)
    # 구버전 코드 호환용으로 유지합니다.
    FS_XS = "0.6875rem"  # ≈ 11px  (v9.0: 0.75rem → 0.6875rem)
    FS_SM = "0.75rem"  # ≈ 12px  (v9.0: 0.8125rem → 0.75rem)
    FS_BASE = "0.875rem"  # ≈ 14px  (v9.0: 0.9375rem → 0.875rem)
    FS_MD = "1rem"  # ≈ 16px  (v9.0: 1.0625rem → 1rem)
    FS_LG = "1.1875rem"  # ≈ 19px  (v9.0: 1.25rem → 1.1875rem)
    FS_2XL = "1.8125rem"  # ≈ 29px  (v9.0: 1.875rem → 1.8125rem)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  하위 호환 별칭 (구버전 코드 충돌 방지용)
    #  [주의] 새 코드에서는 위의 FS_* 상수를 사용하세요.
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # 파란색 계열 (구버전이 P600, P800 같은 이름을 사용하므로 유지)
    P900 = "#061B3F"
    P800 = "#0B2B5C"
    P700 = "#1A4B8C"
    P600 = "#2563AD"
    P200 = "#A8BFDC"
    P100 = "#D0DCF0"
    P50 = "#EBF0F9"

    # 텍스트 구버전 별칭
    TXT1 = "#1C2D42"
    TXT2 = "#3A5068"
    TXT3 = "#6B85A0"
    TXT4 = "#8FA6C4"
    TEXT_PRIMARY = "#111827"
    TEXT_TERTIARY = "#6B7280"
    TEXT_DISABLED = "#9CA3AF"

    # 배경 구버전 별칭
    BG_PRIMARY = "#FFFFFF"
    BG_SECONDARY = "#F6F8FC"
    BG_TERTIARY = "#EEF2F8"
    BG_WHITE = "#FFFFFF"
    BG_CANVAS = "#F8FAFC"
    BG_SURFACE = "#F1F5F9"
    S0 = "#FFFFFF"
    S1 = "#F6F8FC"
    S2 = "#EEF2F8"
    S3 = "#E5EAEF"
    BG1 = "#FFFFFF"
    BG2 = "#F6F8FC"

    # 테두리 구버전 별칭
    BORDER_DEFAULT = "#E5E7EB"
    BORDER_DARK = "#D1D5DB"
    BORDER_LIGHT = "#F3F4F6"
    DIV = "#E5E7EB"

    # 사이드바 구버전 별칭
    SIDEBAR_TEXT = "#E8F0F8"
    SIDEBAR_MUTED = "#7FA8CC"
    SIDEBAR_BORDER = "#1E4A80"
    SIDEBAR_BADGE = "#2A5F9E"

    # 상태 구버전 별칭 (OK=성공, WRN=경고, ERR=오류)
    OK = "#15803D"
    WRN = "#B45309"
    ERR = "#B91C1C"

    # ── 간격 (Spacing) ──────────────────────────────────────
    # CSS padding, margin, gap 값으로 사용합니다.
    # SP_1 = 4px(소), SP_8 = 32px(대) 처럼 숫자가 클수록 넓습니다.
    SP_1 = "0.25rem"  # 4px
    SP_2 = "0.5rem"  # 8px
    SP_3 = "0.75rem"  # 12px
    SP_4 = "1rem"  # 16px
    SP_5 = "1.25rem"  # 20px
    SP_6 = "1.5rem"  # 24px
    SP_8 = "2rem"  # 32px

    # ── 모서리 반경 ─────────────────────────────────────────
    # 카드, 버튼 모서리를 둥글게 만드는 값입니다.
    R_SM = "6px"  # 살짝 둥근 모서리 (작은 배지용)
    R_MD = "10px"  # 보통 둥근 모서리 (카드용)
    R_LG = "14px"  # 많이 둥근 모서리 (채팅 말풍선용)
    R_XL = "16px"  # 더 많이 둥근 모서리
    R_FULL = "9999px"  # 완전한 원형/약 모양 (pill 버튼)
    R_PILL = "9999px"  # R_FULL 과 동일 (별칭)

    # ── 전환 애니메이션 ─────────────────────────────────────
    # CSS transition 에 사용합니다. 부드러운 애니메이션을 만듭니다.
    # cubic-bezier 는 가속/감속 곡선을 정의하는 함수입니다.
    TR_BASE = "200ms cubic-bezier(0.4,0,0.2,1)"
    EASE = "200ms cubic-bezier(0.4,0,0.2,1)"
    EASES = "300ms cubic-bezier(0.4,0,0.2,1)"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  유틸리티 메서드
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def shadow(level: int = 1) -> str:
        """
        그림자(box-shadow) CSS 값을 반환하는 함수.

        그림자 강도를 1~3단계로 선택할 수 있습니다.
        level이 높을수록 그림자가 더 진하고 넓어집니다.

        Args:
            level: 그림자 강도 (1=가장 약함, 3=가장 강함)

        Returns:
            CSS box-shadow 문자열

        사용 예시:
            shadow_css = UITheme.shadow(2)
            # → "0 3px 8px rgba(0,0,0,.10), 0 2px 4px rgba(0,0,0,.06)"
        """
        # 딕셔너리로 레벨별 CSS 값을 관리
        shadow_map = {
            1: "0 1px 3px rgba(0,0,0,.07), 0 1px 2px rgba(0,0,0,.05)",
            2: "0 3px 8px rgba(0,0,0,.10), 0 2px 4px rgba(0,0,0,.06)",
            3: "0 8px 20px rgba(0,0,0,.12), 0 4px 8px rgba(0,0,0,.08)",
        }
        # .get(level, "none") → level에 해당하는 값 없으면 "none" 반환
        return shadow_map.get(level, "none")

    @staticmethod
    def focus_ring() -> str:
        """
        접근성을 위한 포커스 링(focus ring) CSS 값 반환.

        키보드로 탭 이동 시 현재 선택된 요소를 시각적으로 표시합니다.
        흰색 테두리(2px) + 파란색 테두리(4px) 이중 구조.

        Returns:
            CSS box-shadow 문자열
        """
        # 흰색 링(2px) → 파란색 링(4px) 순서로 이중 테두리 효과
        return "0 0 0 2px #FFFFFF, 0 0 0 4px #2563EB"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  전역 CSS 생성 (핵심 메서드)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @classmethod
    def get_global_css(cls) -> str:
        """
        앱 전체에 적용할 CSS를 HTML 문자열로 반환합니다.

        [이 메서드가 하는 일]
        1. :root 에 CSS 변수 정의 (위 파이썬 값들을 CSS 변수로 변환)
        2. Streamlit 기본 UI 요소 스타일 재정의 (사이드바, 채팅, 버튼 등)
        3. 커스텀 컴포넌트 스타일 정의 (검색 모드 카드, 출처 카드 등)

        [호출 방법] (main.py 의 맨 위에서 1회만 호출)
            st.markdown(UITheme.get_global_css(), unsafe_allow_html=True)

        [주의사항]
        - unsafe_allow_html=True 가 반드시 필요합니다.
        - @classmethod 이므로 UITheme.get_global_css() 로 호출합니다.
          (인스턴스 없이 클래스에서 직접 호출 가능)

        Returns:
            <style>...</style> 태그를 포함한 HTML 문자열
        """
        return f"""
<style>
/* ═══════════════════════════════════════════════════════
   웹폰트 로드 (Noto Sans KR)
   [왜 이 폰트인가?]
   한국어를 깔끔하게 표시하는 구글 폰트입니다.
   wght@300;400;500;600;700;800 → 다양한 굵기를 지원합니다.
   display=swap → 폰트 로딩 중에도 글자가 먼저 표시됩니다.
═══════════════════════════════════════════════════════ */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700;800&display=swap');

/* ═══════════════════════════════════════════════════════
   CSS 디자인 토큰 변수 (v9.0)
   [CSS 변수란?]
   --변수명: 값; 형식으로 정의하고
   var(--변수명) 으로 사용합니다.
   :root 에 정의하면 페이지 전체에서 사용 가능합니다.
═══════════════════════════════════════════════════════ */
:root {{
  /* ── 주 색상 계열 ── */
  --clr-primary:        {cls.PRIMARY};
  --clr-primary-dark:   {cls.PRIMARY_DARK};
  --clr-primary-light:  {cls.PRIMARY_LIGHT};
  --clr-primary-mid:    {cls.PRIMARY_MID};

  /* ── 사이드바 ── */
  --clr-sidebar-bg:     {cls.SIDEBAR_BG};

  /* ── 카드 / 페이지 배경 ── */
  --clr-card:           {cls.CARD_BG};
  --clr-page:           {cls.PAGE_BG};

  /* ── 테두리 ── */
  --clr-border:         {cls.BORDER};
  --clr-border-focus:   {cls.BORDER_FOCUS};

  /* ── 텍스트 4단계 ── */
  --clr-text:           {cls.TEXT};
  --clr-text-secondary: {cls.TEXT_SECONDARY};
  --clr-text-muted:     {cls.TEXT_MUTED};
  --clr-text-hint:      {cls.TEXT_HINT};

  /* ── 청록 브랜드 컬러 ── */
  --clr-accent:         {cls.TEAL};
  --clr-accent-dark:    {cls.TEAL_DARK};
  --clr-accent-light:   {cls.TEAL_LIGHT};

  /* ── 상태 색상 ── */
  --clr-success:        {cls.SUCCESS};
  --clr-warning:        {cls.WARNING};
  --clr-error:          {cls.ERROR};

  /* ── 검색 모드 카드 색상 ── */
  --search-fast-bg:     {cls.SEARCH_FAST_BG};
  --search-fast-text:   {cls.SEARCH_FAST_TEXT};
  --search-std-bg:      {cls.SEARCH_STD_BG};
  --search-std-text:    {cls.SEARCH_STD_TEXT};
  --search-deep-bg:     {cls.SEARCH_DEEP_BG};
  --search-deep-text:   {cls.SEARCH_DEEP_TEXT};
  --search-selected:    {cls.SEARCH_SELECTED};

  /* ── [v9.0] 폰트 사이즈 (5단계, 전 버전보다 1px 작음) ── */
  --fs-heading:  {cls.FS_HEADING};   /* 27px — 페이지 제목 */
  --fs-section:  {cls.FS_SECTION};   /* 19px — 섹션 타이틀 */
  --fs-body:     {cls.FS_BODY};      /* 15px — 본문 */
  --fs-sidebar:  {cls.FS_SIDEBAR};   /* 13px — 사이드바 */
  --fs-caption:  {cls.FS_CAPTION};   /* 12px — 캡션·설명 */

  /* ── 폰트 패밀리 ── */
  /* -apple-system → macOS/iOS 시스템 폰트 폴백 */
  /* sans-serif    → 모든 환경 최종 폴백 */
  --font-body: "Noto Sans KR", "Apple SD Gothic Neo", -apple-system, sans-serif;
  --font-mono: "IBM Plex Mono", "Consolas", monospace;

  /* ── 모서리 반경 ── */
  --r-sm: 4px; --r-md: 8px; --r-lg: 12px; --r-xl: 16px; --r-pill: 9999px;

  /* ── 그림자 ── */
  --sh1: {cls.shadow(1)};
  --sh2: {cls.shadow(2)};
  --sh3: {cls.shadow(3)};

  /* ── 전환 애니메이션 ── */
  --ease:  {cls.EASE};
  --eases: {cls.EASES};

  /* ── 구버전 호환 토큰 (신규 코드에서는 위 변수 사용) ── */
  --p800:{cls.P800}; --p700:{cls.P700}; --p600:{cls.P600};
  --a500:{cls.TEAL}; --a400:{cls.A400}; --a600:{cls.TEAL_DARK};
  --t1:{cls.TXT1}; --t2:{cls.TXT2}; --t3:{cls.TXT3};
  --s0:{cls.S0}; --s1:{cls.S1}; --s2:{cls.S2};
  --div:{cls.BORDER};
  --ok:{cls.SUCCESS}; --wrn:{cls.WARNING}; --err:{cls.ERROR};
}}

/* ═══════════════════════════════════════════════════════
   CSS 초기화 (box-sizing)
   [box-sizing: border-box 란?]
   width: 200px 짜리 요소에 padding: 10px 를 주면
   - content-box (기본값): 실제 너비 = 200+10+10 = 220px
   - border-box          : 실제 너비 = 200px (padding 포함)
   border-box 가 레이아웃 계산이 훨씬 직관적입니다.
═══════════════════════════════════════════════════════ */
*, *::before, *::after {{ box-sizing: border-box; }}

/* 기본 폰트 · 배경 설정 */
html, body {{
  font-family: var(--font-body) !important;
  font-size:   var(--fs-body) !important;
  background-color: var(--clr-page) !important;
  color: var(--clr-text) !important;
  -webkit-font-smoothing: antialiased; /* macOS에서 폰트 안티앨리어싱 */
}}

/* ═══════════════════════════════════════════════════════
   Streamlit 레이아웃 재정의
   [!important 를 쓰는 이유]
   Streamlit 이 자체 CSS를 가지고 있어서
   !important 없이는 우리 스타일이 무시됩니다.
═══════════════════════════════════════════════════════ */
/* 메인 콘텐츠 영역 패딩·너비 제한 */
.main .block-container {{
  padding: 2rem 2.5rem 6rem !important;
  max-width: 960px !important;
  background: var(--clr-page) !important;
}}

/* 상단 헤더바 숨김 (불필요한 공간 제거) */
header[data-testid="stHeader"] {{
  background: var(--clr-page) !important;
  border-bottom: 1px solid var(--clr-border) !important;
  height: 0 !important;
}}

/* ═══════════════════════════════════════════════════════
   사이드바 스타일 — Slate-900 어두운 배경 (가독성 패치 적용)
═══════════════════════════════════════════════════════ */
/* 사이드바 전체 배경을 어두운 네이비로 설정 */
[data-testid="stSidebar"] {{
  background: var(--clr-sidebar-bg) !important;
  border-right: none !important;
}}

[data-testid="stSidebar"] > div {{
  background: var(--clr-sidebar-bg) !important;
}}

/* [v9.5 수정] 사이드바 일반 텍스트 및 라벨 가독성 보강
   (광범위한 * 셀렉터를 제거하여 버튼 내부 색상 충돌 방지) */
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stText,
[data-testid="stSidebar"] label {{
  color: rgba(255, 255, 255, 0.88) !important;
  font-size: var(--fs-sidebar) !important;
}}

/* metric — 계층별 명도 차별화 */
[data-testid="stSidebar"] [data-testid="stMetricLabel"] label,
[data-testid="stSidebar"] [data-testid="stMetricLabel"] div {{
  color: rgba(255,255,255,0.55) !important;
  font-size: 11px !important;
}}
[data-testid="stSidebar"] [data-testid="stMetricValue"] div {{
  color: rgba(255,255,255,0.95) !important;
  font-size: 1.05rem !important;
  font-weight: 700 !important;
}}

/* caption */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{
  color: rgba(255,255,255,0.48) !important;
  font-size: 11px !important;
}}

/* 탭 버튼 */
[data-testid="stSidebar"] [data-testid="stTab"] button p {{
  color: rgba(255,255,255,0.70) !important;
  font-size: 12px !important;
}}
[data-testid="stSidebar"] [data-testid="stTab"] button[aria-selected="true"] p {{
  color: rgba(255,255,255,0.97) !important;
  font-weight: 700 !important;
}}

/* 검색 모드 버튼 — secondary/primary 색상 유지 */
[data-testid="stSidebar"] .search-mode-wrap
    div[data-testid="stButton"] > button[kind="secondary"] p {{
  color: rgba(255,255,255,0.58) !important;
}}
[data-testid="stSidebar"] .search-mode-wrap
    div[data-testid="stButton"] > button[kind="primary"] p {{
  color: rgba(255,255,255,0.97) !important;
  font-weight: 700 !important;
}}

/* 사이드바 구분선 */
[data-testid="stSidebar"] hr {{
  border-color: rgba(255,255,255,0.10) !important;
  margin: 0.875rem 0 !important;
}}

/* ── 사이드바 컬럼 컨테이너 배경 투명화 (v9.4)
   st.columns() 내부 모든 컨테이너 투명화 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"],
[data-testid="stSidebar"] [data-testid="stVerticalBlock"],
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {{
  background: transparent !important;
}}
[data-testid="stSidebar"] [data-testid="stColumn"] {{
  background: transparent !important;
  min-width: 0 !important;
}}

/* ── oc-btn-row 전용 (재연결/상태확인) ── */
[data-testid="stSidebar"] .oc-btn-row {{
  background: transparent !important;
}}
[data-testid="stSidebar"] .oc-btn-row div[data-testid="stButton"] > button {{
  background: rgba(255,255,255,0.08) !important;
  border: 1px solid rgba(255,255,255,0.22) !important;
  color: rgba(255,255,255,0.88) !important;
}}
[data-testid="stSidebar"] .oc-btn-row div[data-testid="stButton"] > button p,
[data-testid="stSidebar"] .oc-btn-row div[data-testid="stButton"] > button span {{
  color: inherit !important;
  background: transparent !important;
}}

/* 사이드바 버튼 — 반투명 어두운 배경 + 흰 테두리 (v9.5 수정)
   columns 여부와 무관하게 항상 어두운 배경 강제 */
[data-testid="stSidebar"] .stButton > button {{
  background: rgba(255,255,255,0.08) !important;
  border: 1px solid rgba(255,255,255,0.22) !important;
  color: #FFFFFF !important;
  border-radius: var(--r-md) !important;
  padding: 0.4rem 0.6rem !important;
  transition: all var(--ease) !important;
  box-shadow: none !important;
}}

/* [v9.5] 버튼 내부 p/span 상속 설정 (텍스트 소멸 현상 해결) */
[data-testid="stSidebar"] .stButton > button p,
[data-testid="stSidebar"] .stButton > button span,
[data-testid="stSidebar"] [data-testid="stColumn"] .stButton > button p,
[data-testid="stSidebar"] [data-testid="stColumn"] .stButton > button span {{
  color: inherit !important;
  font-size: var(--fs-sidebar) !important;
  font-weight: 500 !important;
  background: transparent !important;
}}

/* 사이드바 버튼 호버 */
[data-testid="stSidebar"] .stButton > button:hover {{
  background: rgba(255,255,255,0.16) !important;
  border-color: rgba(255,255,255,0.40) !important;
  color: #FFFFFF !important;
}}

/* type="primary" 버튼 (DB 업데이트 등) */
[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {{
  background: rgba(37,99,235,0.85) !important;
  border-color: rgba(37,99,235,0.90) !important;
  color: #FFFFFF !important;
}}
[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"]:hover {{
  background: rgba(37,99,235,1.0) !important;
}}

/* 사이드바 확장 패널(expander) */
[data-testid="stSidebar"] [data-testid="stExpander"] {{
  background: rgba(255,255,255,0.07) !important;
  border: 1px solid rgba(255,255,255,0.13) !important;
  border-radius: 8px !important;
  box-shadow: none !important;
  overflow: hidden !important;
}}

[data-testid="stSidebar"] [data-testid="stExpander"] summary p {{
  font-size: var(--fs-sidebar) !important;
  color: rgba(255,255,255,0.85) !important;
  font-weight: 600 !important;
}}

/* 사이드바 텍스트 입력창 (v9.5 수정: 입력 내용 가시성 확보) */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] [data-testid="stTextInput"] input {{
  background: rgba(255,255,255,0.1) !important;
  border: 1px solid rgba(255,255,255,0.2) !important;
  color: #FFFFFF !important;
  font-size: var(--fs-sidebar) !important;
  border-radius: var(--r-md) !important;
}}

/* 사이드바 파일 업로더 */
/* ── 사이드바 파일 업로더 Dropzone UI (v9.2) ──
   ChatGPT / Notion 스타일 카드형 업로드 박스
   어두운 사이드바 배경(#0F172A)과 명확히 구분되도록 강조 */
[data-testid="stSidebar"] [data-testid="stFileUploader"] {{
  background: rgba(30, 41, 59, 0.8) !important;          /* Slate-800 — 배경 대비 보강 */
  border: 2px dashed rgba(56, 189, 248, 0.6) !important;   /* Sky-400 점선 — 업로드 영역 강조 */
  border-radius: 10px !important;
  padding: 0.75rem 0.5rem !important;
  transition: background 160ms ease, border-color 160ms ease !important;
  cursor: pointer !important;
}}

/* hover — 배경 밝아지고 테두리 강조 */
[data-testid="stSidebar"] [data-testid="stFileUploader"]:hover {{
  background: rgba(38, 51, 72, 0.9) !important;
  border-color: #7dd3fc !important;        /* Sky-300 더 밝게 */
}}

/* 안내 텍스트 ("Drag and drop" → 한국어 안내) */
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {{
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  gap: 0.3rem !important;
  padding: 0.6rem 0.4rem !important;
}}

/* 업로드 안내 텍스트 강조 (v9.5 명도 조정) */
[data-testid="stSidebar"] [data-testid="stFileUploader"] span {{
  color: #bae6fd !important;               /* Sky-200 — 안내 텍스트 가독성 최적화 */
  font-size: 11px !important;
  font-weight: 600 !important;
  text-align: center !important;
  line-height: 1.5 !important;
}}

[data-testid="stSidebar"] [data-testid="stFileUploader"] small,
[data-testid="stSidebar"] [data-testid="stFileUploader"] p {{
  color: rgba(255,255,255,0.65) !important;
  font-size: 10px !important;
}}

/* 선택된 파일명 표시 — 초록 강조 */
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {{
  background: rgba(74,222,128,0.12) !important;
  border: 1px solid rgba(74,222,128,0.35) !important;
  border-radius: 6px !important;
  padding: 0.3rem 0.5rem !important;
  margin-top: 0.35rem !important;
}}

[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderFileName"] {{
  color: #4ade80 !important;               /* Green-400 — 선택된 파일명 초록 */
  font-weight: 600 !important;
  font-size: 11px !important;
}}

/* "Browse files" 버튼 → 테두리 강조 (v9.5 텍스트 충돌 완벽 방어) */
[data-testid="stSidebar"] [data-testid="stFileUploader"] button {{
  background: rgba(37, 99, 235, 0.7) !important; /* Blue 계열로 변경하여 대비 극대화 */
  border: 1px solid #60a5fa !important;
  color: #ffffff !important;
  font-size: 11px !important;
  border-radius: 6px !important;
  padding: 0.25rem 0.6rem !important;
  font-weight: 600 !important;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button p,
[data-testid="stSidebar"] [data-testid="stFileUploader"] button span {{
  color: inherit !important;
  font-size: 11px !important;
}}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {{
  background: rgba(37, 99, 235, 1.0) !important;
  border-color: #93c5fd !important;
}}

/* selectbox / dropdown (v9.5 명도 조정) */
[data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-testid="stSelectboxInternal"] {{
  background: rgba(255,255,255,0.1) !important;
  border: 1px solid rgba(255,255,255,0.2) !important;
  color: #FFFFFF !important;
}}
[data-testid="stSidebar"] [data-testid="stSelectbox"] label {{
  color: rgba(255,255,255,0.85) !important;
  font-size: 12px !important;
  font-weight: 600 !important;
}}

/* expander 내부 버튼 (스키마 탭, 히스토리 등) */
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button {{
  background: rgba(255,255,255,0.08) !important;
  border: 1px solid rgba(255,255,255,0.20) !important;
  color: rgba(255,255,255,0.95) !important;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button p,
[data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button span {{
  color: inherit !important;
  background: transparent !important;
  font-size: 11px !important;
}}

/* textarea (v9.5 가시성 확보) */
[data-testid="stSidebar"] textarea {{
  background: rgba(255,255,255,0.1) !important;
  border: 1px solid rgba(255,255,255,0.2) !important;
  color: #FFFFFF !important;
  font-size: 12px !important;
}}

/* ═══════════════════════════════════════════════════════
   .custom-app wrapper 스타일
   [이 wrapper 가 필요한 이유]
   Streamlit 자체 UI 요소(버튼, 입력창 등)는 건드리지 않고
   우리가 만든 HTML 컴포넌트만 스타일링하기 위해
   .custom-app 클래스를 wrapper 로 사용합니다.
═══════════════════════════════════════════════════════ */
.custom-app {{
  font-family: var(--font-body);
  color: var(--clr-text);
}}

/* 페이지 제목 스타일 (27px/700) */
.custom-app .ca-heading {{
  font-size: var(--fs-heading) !important;
  font-weight: 700 !important;
  color: var(--clr-text) !important;
  letter-spacing: -0.03em;
  line-height: 1.2;
  margin: 0;
}}

/* 섹션 타이틀 스타일 (19px/600) */
.custom-app .ca-section-title {{
  font-size: var(--fs-section) !important;
  font-weight: 600 !important;
  color: var(--clr-text) !important;
  letter-spacing: -0.01em;
  line-height: 1.3;
  margin: 0;
}}

/* 본문 텍스트 스타일 (15px/400) */
.custom-app .ca-body {{
  font-size: var(--fs-body) !important;
  font-weight: 400 !important;
  color: var(--clr-text-secondary) !important;
  line-height: 1.65;
}}

/* 캡션 텍스트 스타일 (12px/회색) */
.custom-app .ca-caption {{
  font-size: var(--fs-caption) !important;
  font-weight: 400 !important;
  color: var(--clr-text-muted) !important;
  line-height: 1.5;
}}

/* ── 카드 기본 스타일 ── */
.custom-app .ca-card {{
  background: var(--clr-card);
  border: 1px solid var(--clr-border);
  border-radius: var(--r-lg);
  padding: 1rem 1.25rem;
  box-shadow: var(--sh1);
  transition: box-shadow var(--ease), border-color var(--ease);
}}

.custom-app .ca-card:hover {{
  box-shadow: var(--sh2);
  border-color: var(--clr-primary-mid);
}}

/* ── 커스텀 버튼 스타일 ── */
/* [주의] Streamlit button 태그를 직접 수정하면 충돌이 발생합니다.
   ca-btn-primary 클래스를 HTML 링크나 div 에만 사용하세요. */
.custom-app .ca-btn-primary {{
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: var(--clr-primary);
  color: #fff;
  font-size: var(--fs-body);
  font-weight: 600;
  padding: 0.5rem 1.25rem;
  border-radius: var(--r-md);
  border: none;
  cursor: pointer;
  transition: background var(--ease);
  text-decoration: none;
}}

.custom-app .ca-btn-primary:hover {{
  background: var(--clr-primary-dark);
}}

/* ═══════════════════════════════════════════════════════
   검색 모드 카드 CSS
   [구조 설명]
   .search-mode-grid          → 카드 목록 컨테이너 (flex column)
     .search-mode-card         → 개별 카드 (flex row)
       .smc-icon                → 아이콘 영역
       .smc-label               → 모드 이름
       .smc-desc                → 설명 텍스트
       .smc-check               → 선택됐을 때 체크 표시
   
   --fast / --std / --deep     → 각 모드별 색상 변형
   --selected                  → 선택된 카드 강조 테두리
═══════════════════════════════════════════════════════ */

/* 카드 목록 컨테이너 */
.search-mode-grid {{
  display: flex;
  flex-direction: column; /* 세로로 쌓기 */
  gap: 0.4rem;
  margin: 0.5rem 0;
}}

/* 개별 카드 기본 스타일 */
.search-mode-card {{
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  border: 1.5px solid transparent;
  cursor: pointer;
  transition: all 180ms ease;
  user-select: none;      /* 드래그로 텍스트 선택 방지 */
  -webkit-user-select: none;
  position: relative;
}}

/* ⚡ 빠른 검색 카드 색상 */
.search-mode-card--fast {{
  background: var(--search-fast-bg);
  border-color: rgba(12, 74, 110, 0.15);
}}

.search-mode-card--fast .smc-icon,
.search-mode-card--fast .smc-label,
.search-mode-card--fast .smc-desc {{
  color: var(--search-fast-text);
}}

.search-mode-card--fast:hover {{
  border-color: rgba(12, 74, 110, 0.35);
  box-shadow: 0 1px 4px rgba(12,74,110,0.10);
}}

/* ⚖️ 표준 검색 카드 색상 */
.search-mode-card--std {{
  background: var(--search-std-bg);
  border-color: rgba(20, 83, 45, 0.15);
}}

.search-mode-card--std .smc-icon,
.search-mode-card--std .smc-label,
.search-mode-card--std .smc-desc {{
  color: var(--search-std-text);
}}

.search-mode-card--std:hover {{
  border-color: rgba(20, 83, 45, 0.35);
  box-shadow: 0 1px 4px rgba(20,83,45,0.10);
}}

/* 🧠 심층 검색 카드 색상 */
.search-mode-card--deep {{
  background: var(--search-deep-bg);
  border-color: rgba(113, 63, 18, 0.15);
}}

.search-mode-card--deep .smc-icon,
.search-mode-card--deep .smc-label,
.search-mode-card--deep .smc-desc {{
  color: var(--search-deep-text);
}}

.search-mode-card--deep:hover {{
  border-color: rgba(113, 63, 18, 0.35);
  box-shadow: 0 1px 4px rgba(113,63,18,0.10);
}}

/* 선택된 카드 — 파란 테두리 + 외부 글로우 */
.search-mode-card--selected {{
  border: 2px solid var(--search-selected) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,0.12) !important;
}}

/* 카드 내부 각 요소 크기 (v9.0: 1px 감소) */
.smc-icon  {{ font-size: 0.9375rem; flex-shrink: 0; }}  /* 15px */
.smc-label {{
  font-size: var(--fs-sidebar);  /* 13px */
  font-weight: 600;
  line-height: 1.2;
}}
.smc-desc  {{
  font-size: var(--fs-caption);  /* 12px */
  opacity: 0.78;
  margin-top: 0.05rem;
}}

/* 선택 체크 배지 (파란 원 + 흰 체크마크) */
.smc-check {{
  margin-left: auto;     /* 오른쪽 끝으로 밀기 */
  width: 14px;
  height: 14px;
  border-radius: 50%;    /* 원형 */
  background: var(--search-selected);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}}

/* 체크마크 — CSS 로 V자 모양 만들기 */
.smc-check::after {{
  content: '';
  display: block;
  width: 5px;
  height: 3px;
  border-left: 1.5px solid #fff;
  border-bottom: 1.5px solid #fff;
  transform: rotate(-45deg) translateY(-1px);
}}

/* ═══════════════════════════════════════════════════════
   Streamlit 탭 스타일
   [밑줄 스타일 탭]
   기본 Streamlit 탭보다 시각적으로 명확하게 만들었습니다.
   선택된 탭은 파란 밑줄이 표시됩니다.
═══════════════════════════════════════════════════════ */
[data-testid="stTabs"] [data-testid="stTab"] {{
  font-size: var(--fs-body) !important;    /* 15px */
  font-weight: 500 !important;
  color: var(--clr-text-muted) !important;
  padding: 0.5rem 1rem !important;
  border-bottom: 2px solid transparent !important;
  transition: all var(--ease) !important;
}}

/* 탭 호버 */
[data-testid="stTabs"] [data-testid="stTab"]:hover {{
  color: var(--clr-primary) !important;
}}

/* 선택된 탭 */
[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {{
  color: var(--clr-primary) !important;
  border-bottom-color: var(--clr-primary) !important;
  font-weight: 600 !important;
}}

/* ═══════════════════════════════════════════════════════
   Streamlit 채팅 메시지 스타일
═══════════════════════════════════════════════════════ */
[data-testid="stChatMessage"] {{
  background: var(--clr-card) !important;
  border: 1px solid var(--clr-border) !important;
  border-radius: var(--r-lg) !important;
  padding: 1rem !important;
  margin-bottom: 0.75rem !important;
  box-shadow: var(--sh1) !important;
  font-size: var(--fs-body) !important;   /* 15px */
}}

/* 채팅 입력창 */
[data-testid="stChatInput"] {{
  border-radius: var(--r-xl) !important;
  border: 1.5px solid var(--clr-border) !important;
  font-size: var(--fs-body) !important;   /* 15px */
  transition: border-color var(--ease) !important;
}}

/* 채팅 입력창 포커스 상태 */
[data-testid="stChatInput"]:focus-within {{
  border-color: var(--clr-primary) !important;
  box-shadow: 0 0 0 3px rgba(37,99,235,0.10) !important;
}}

/* ═══════════════════════════════════════════════════════
   Streamlit 알림 박스 스타일
═══════════════════════════════════════════════════════ */
[data-testid="stAlert"] {{
  border-radius: var(--r-md) !important;
  border: 1px solid var(--clr-border) !important;
  font-size: var(--fs-body) !important;
}}

/* ═══════════════════════════════════════════════════════
   구분선 (st.divider() 스타일)
═══════════════════════════════════════════════════════ */
hr {{
  border: none !important;
  border-top: 1px solid var(--clr-border) !important;
  margin: 1.25rem 0 !important;
  opacity: 1 !important;
}}

/* ═══════════════════════════════════════════════════════
   스크롤바 커스텀 스타일
   [왜 커스텀 스크롤바인가?]
   OS 기본 스크롤바는 디자인과 잘 안 어울립니다.
   얇고 연한 스크롤바로 교체합니다.
═══════════════════════════════════════════════════════ */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: var(--clr-page); }}
::-webkit-scrollbar-thumb {{
  background: var(--clr-border);
  border-radius: var(--r-pill);
}}
::-webkit-scrollbar-thumb:hover {{ background: #D1D5DB; }}

/* ═══════════════════════════════════════════════════════
   십자형 모티프 (병원 브랜드 디자인)
   [십자 구분선이란?]
   병원 심볼을 연상시키는 +자 모양 아이콘이 들어간 구분선입니다.
   .cross-divider 클래스를 div에 적용하면 표시됩니다.
═══════════════════════════════════════════════════════ */
.cross-divider {{
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 1rem 0 0.5rem;
}}

/* 좌측 수평선 */
.cross-divider::before {{
  content: '';
  flex: 1;
  height: 1px;
  background: var(--clr-border);
}}

/* 십자 아이콘 컨테이너 */
.cross-divider-icon {{
  width: 18px; height: 18px;
  flex-shrink: 0;
  position: relative;
  display: flex; align-items: center; justify-content: center;
}}

/* 가로 막대 */
.cross-divider-icon::before {{
  content: '';
  position: absolute;
  width: 12px; height: 2px;
  background: var(--clr-accent);
  border-radius: 1px;
}}

/* 세로 막대 */
.cross-divider-icon::after {{
  content: '';
  position: absolute;
  width: 2px; height: 12px;
  background: var(--clr-accent);
  border-radius: 1px;
}}

/* DB 상태 점 펄스 애니메이션
   [keyframes 란?]
   애니메이션의 각 단계를 정의합니다.
   0%, 100% → 시작/끝 상태
   50%      → 중간 상태 */
@keyframes crossPulse {{
  0%,100% {{ box-shadow: 0 0 0 0 rgba(0,151,178,.3); }}
  50% {{
    box-shadow:
      -4px 0 0 1px rgba(0,151,178,.2),
       4px 0 0 1px rgba(0,151,178,.2),
       0 -4px 0 1px rgba(0,151,178,.2),
       0  4px 0 1px rgba(0,151,178,.2);
  }}
}}

/* DB 정상 상태 점 — 십자 방향으로 빛이 퍼지는 애니메이션 */
.status-dot-healthy {{ animation: crossPulse 2.5s ease-in-out infinite; }}

/* ═══════════════════════════════════════════════════════
   출처 카드 스타일 (.source-card)
   [출처 카드란?]
   LLM 답변 아래에 "이 내용의 출처는 이 문서입니다"를
   표시하는 카드 목록입니다.
═══════════════════════════════════════════════════════ */
.source-card {{
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.875rem 1rem;
  background: var(--clr-card);
  border: 1px solid var(--clr-border);
  border-top: none;    /* 카드들이 이어붙은 것처럼 보이도록 위쪽 테두리 제거 */
  transition: background var(--ease);
}}

/* 첫 번째 카드는 위쪽 테두리 + 위 모서리 둥글게 */
.source-card:first-child {{
  border-top: 1px solid var(--clr-border);
  border-top-left-radius: var(--r-md);
  border-top-right-radius: var(--r-md);
}}

/* 마지막 카드는 아래 모서리 둥글게 */
.source-card:last-child {{
  border-bottom-left-radius: var(--r-md);
  border-bottom-right-radius: var(--r-md);
}}

.source-card:hover {{ background: var(--clr-page); }}

/* ── 순위 배지 (1, 2, 3 숫자 표시) ── */
.rank-badge {{
  position: relative;
  width: 22px; height: 22px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--r-sm);
  font-size: 0.65rem; font-weight: 700;   /* v9.0: 0.7rem → 0.65rem */
  flex-shrink: 0;
}}

/* ── 홈 화면 질문 카드 (.q-card) ── */
/* [참고] 현재 st.button 으로 대체되어 사용하지 않지만 유지 */
.q-card {{
  display: block;
  width: 100%;
  padding: 0.75rem 1rem;
  background: var(--clr-card);
  border: 1px solid var(--clr-border);
  border-left: 3px solid transparent;
  border-radius: var(--r-md);
  font-size: var(--fs-body);      /* 15px */
  font-weight: 400;
  color: var(--clr-text-secondary);
  text-align: left;
  cursor: pointer;
  transition: all var(--ease);
  box-shadow: var(--sh1);
  line-height: 1.5;
}}

.q-card:hover {{
  border-left-color: var(--clr-accent);
  background: var(--clr-accent-light);
  color: var(--clr-text);
  box-shadow: var(--sh2);
  transform: translateX(2px);    /* 마우스 올리면 오른쪽으로 살짝 이동 */
}}

/* ── 신뢰도 게이지 바 ── */
/* 출처 카드 오른쪽에 표시되는 신뢰도 막대 그래프 */
.trust-bar {{
  width: 56px; height: 5px;
  background: var(--clr-border);
  border-radius: var(--r-pill);
  overflow: hidden;
  flex-shrink: 0;
}}

.trust-bar-fill {{
  height: 100%;
  border-radius: var(--r-pill);
  transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
}}

/* ── 슬라이드업 등장 애니메이션 ── */
/* 페이지 로드 시 요소가 아래에서 위로 부드럽게 등장합니다. */
@keyframes fadeSlideUp {{
  from {{ opacity:0; transform:translateY(12px); }}
  to   {{ opacity:1; transform:translateY(0); }}
}}

@keyframes fadeIn {{
  from {{ opacity:0; }}
  to   {{ opacity:1; }}
}}

/* 애니메이션 적용 클래스 */
.anim-slide-up {{ animation: fadeSlideUp 0.4s ease both; }}
/* 여러 요소가 순차적으로 등장하도록 딜레이를 줍니다. */
.delay-1 {{ animation-delay: 0.05s; }}
.delay-2 {{ animation-delay: 0.10s; }}
.delay-3 {{ animation-delay: 0.15s; }}
.delay-4 {{ animation-delay: 0.20s; }}

/* ═══════════════════════════════════════════════════════
   접근성 미디어 쿼리
   [prefers-reduced-motion]
   모션/애니메이션에 민감한 사용자(전정 장애 등)를 위해
   사용자 OS 설정에서 "애니메이션 줄이기"를 선택한 경우
   모든 애니메이션을 비활성화합니다.
═══════════════════════════════════════════════════════ */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation: none !important;
    transition: none !important;
  }}
}}

/* [forced-colors] 고대비 모드 (시각 장애 사용자) */
@media (forced-colors: active) {{
  .q-card {{ border: 2px solid ButtonText; }}
  .source-card {{ border: 1px solid ButtonText; }}
  .search-mode-card--selected {{ border: 2px solid Highlight !important; }}
}}
</style>
"""
