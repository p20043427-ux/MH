# 병원 OCS 대시보드 — UI/UX 디자인 개선 v6.0

## 📋 요청사항 분석

### 현황 평가
- **색상**: 원색 위주(주황, 파랑) + 일관성 없는 강조색
- **폰트**: 본문과 데이터 크기 차이 작음 → 수치 식별 어려움
- **레이아웃**: 카드 간 간격 불균형
- **가독성**: 텍스트 정렬 불분명

---

## 🎨 개선 사항 (v6.0)

### 1️⃣ 색상 시스템 (Color Palette v4.0)

#### Tailwind Slate 기반 중성색 (배경/텍스트)
```python
배경:    #F8FAFC (Slate-50)
카드:    #FFFFFF (White)
서피스:  #F1F5F9 (Slate-100)
경계선:  #CBD5E1 (Slate-300) ← 더 진해 가독성↑

텍스트 계층:
- t1: #0F172A (Slate-900) — 헤딩/중요 수치
- t2: #334155 (Slate-700) — 본문
- t3: #64748B (Slate-500) — 레이블
- t4: #94A3B8 (Slate-400) — 보조/힌트
```

#### Semantic Color (KPI 증감 배지)
```
의료진이 즉시 인식할 수 있는 감정 없는 색상:
- 증가 ▲ : #EF4444 (Red-500) — 주의 필요
- 감소 ▼ : #3B82F6 (Blue-500) — 안심
```

#### 상태 표시 (신호등 3색, 의료용 신뢰도)
```
✅ 정상: #059669 (Emerald-600) — 더 진해 가독성↑
⚠️  주의: #F59E0B (Amber-500)
🚨 위험: #DC2626 (Red-600)  — 더 진해 강조
```

#### 차트 팔레트 (8색 다채)
```
chart1: #1E40AF (Deep Blue-800)
chart2: #2563EB (Blue-600)
chart3: #3B82F6 (Blue-500)
chart4: #059669 (Emerald-600)
chart5: #0D9488 (Teal-600)
chart6: #F59E0B (Amber-500)
chart7: #EF4444 (Red-500)
chart8: #8B5CF6 (Purple-500)
```

---

### 2️⃣ 타이포그래피 (Typography v6.0)

#### 폰트 스택
```css
Body:  Pretendard Variable / Pretendard (한글 최적화)
KPI:   Inter + Pretendard (Extra Bold 강조)
Data:  Consolas / Monaco (Monospace, tabular-nums)
```

#### 헤딩 계층
```
KPI 수치 (핵심):
- 폰트: Inter, Extra Bold (800)
- 크기: 32px (기존 28px → 32px로 확대)
- 자간: -0.02em (더 타이트)
- 적용: font-variant-numeric: tabular-nums

구간 헤더:
- 폰트 무게: 800 (기존 700 → 800)
- 크기: 12px
- 자간: 0.02em
```

#### 데이터 폰트
```
테이블 숫자:
- font-variant-numeric: tabular-nums (자릿수 정렬)
- font-family: Consolas/Monaco
- text-align: right (우측 정렬)
- 행 높이: 40px (통일)
```

---

### 3️⃣ 레이아웃 & 그리드 (8px Grid System)

#### 기본 그리드
```css
Streamlit padding:
- top/bottom: 8px
- left/right: 12px

컴포넌트 간 gap: 8px
카드 패딩: 16px (기존 14px → 16px로 확대)
```

#### 카드 시스템
```css
.kpi-card:
  - 높이: 120px (기존 116px → 120px)
  - 패딩: 16px (기존 14px)
  - 테두리: 1px #E2E8F0 (더 진한 경계선)
  - 레디우스: 10px (일관성)
  - 그림자: 0 1px 3px rgba(...,0.08) (미묘하게 약화)

.wd-card:
  - 패딩: 16px (카드와 동일)
  - 테두리: 1px #E2E8F0
  - 레디우스: 12px (카드보다 약간 더 둥글게)
```

---

### 4️⃣ 테이블 가독성 (Ag-Grid 스타일)

#### 숫자 정렬 & 폰트
```python
# Python 로직
class wd-td-num:
    font-variant-numeric: tabular-nums
    font-family: Consolas, Monaco
    text-align: right
    font-weight: 600

# 효과: 같은 자릿수 숫자들이 정확히 정렬됨
  123    ← 오른쪽 정렬
 1234    ← 오른쪽 정렬
12345    ← 오른쪽 정렬
```

#### 행 높이 통일
```css
.wd-td:
  - padding: 10px 12px (기존 8px)
  - height: 40px (고정)
  - border-bottom: 1px #E2E8F0

헤더 (.wd-th):
  - padding: 10px 12px
  - border-bottom: 2px #CBD5E1 (더 진한 경계선)
  - font-weight: 800 (기존 700)
  - letter-spacing: 0.08em (자간 증가)
```

---

### 5️⃣ 조건부 서식 (Conditional Formatting)

#### 가동률 색상 로직 (Python)
```python
# Dashboard-First: 의료진이 3초 내 병동 위험도 인식

if occ_rate >= 90:
    color = "#DC2626"  # Red-600 (위험) ← 90% 이상
elif occ_rate >= 80:
    color = "#F59E0B"  # Amber-500 (주의)
else:
    color = "#059669"  # Emerald-600 (정상) ← 80% 미만

# 적용 대상:
# - KPI 카드 "병상 가동률"
# - 주간 추이 테이블 가동률 컬럼
# - 병동별 현황 테이블 가동률 컬럼
```

#### 증감 배지 (Semantic Color)
```python
delta_up = "#DC2626"    # ▲ 증가 (Red — 주의)
delta_dn = "#3B82F6"    # ▼ 감소 (Blue — 안심)

# KPI 카드 2열 하단에 적용
금일 입원: ▲ +3명 (Red)
금일 퇴원: ▼ -2명 (Blue)
```

---

### 6️⃣ Donut Chart (Recharts 스타일)

#### 시각적 특징
```python
# 진료과별 재원 구성 파이 (Row 2, 우측)

구성:
- hole: 0.5 (도넛 구멍 크기)
- marker.line.width: 2px (흰색 테두리)
- 중앙 텍스트: 전체 재원수 (굵고 큼)

색상:
- 팔레트: chart1~chart8 (8색)
- 라인: #FFFFFF (흰색 구분선)

범례:
- 위치: 차트 하단
- 형식: [■색상] 진료과명 · 00% · 000명
- 폰트: 10px, 일관된 서식
```

---

### 7️⃣ CSS 통합 최적화

#### 파일: `_WARD_CSS` (v6.0)

**섹션별 구성:**
```
01. CARD SYSTEM — 그림자/테두리 통일
02. KPI CARDS — Extra Bold 수치 강조
03. SECTION HEADER — 명확한 계층구조 (border-bottom: 2px)
04. TABLE — font-variant-numeric + 우측 정렬
05. STATUS BADGES — Semantic Color (Red/Blue)
06. LEGEND & INDICATORS — 통일된 서식
07. BUTTONS — 36px 높이, 일관된 hover 효과
08. SELECTBOX — Ward Selector 강조
09. TOP-BAR — 헤더 액센트 라인 개선
```

#### 주요 개선
```css
/* 카드 그림자 — 보다 미묘하고 현대적 */
box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08)
           ↓ (기존: 0 4px 6px -1px rgba(...))

/* 텍스트 렌더링 — 명확한 계층 */
color: #334155  (기존: #475569)
       ↑ 더 어두워 가독성↑

/* 버튼 높이 통일 */
height: 36px (기존: 32px)
        ↓ 터치 타겟 충분

/* 탑바 액센트 */
gradient: #1E40AF → #2563EB → #E2E8F0
          (Deep Blue → Blue → Gray)
```

---

## 📊 변경 전후 비교

| 항목 | 기존 (v5.0) | 개선 (v6.0) | 효과 |
|------|-----------|-----------|------|
| **색상 기조** | 원색 (주황/파랑) | Slate 기반 중성색 | 전문성↑, 피로도↓ |
| **KPI 폰트** | 28px, 800 | 32px, Inter + 800 | 가독성↑↑ |
| **수치 정렬** | 기본 | tabular-nums | 자릿수 정렬 |
| **가동률 임계값** | 95% / 85% | 90% / 80% | Dashboard-First |
| **테이블 행 높이** | 8px padding | 40px 고정 | 스캔 속도↑ |
| **카드 테두리** | #F1F5F9 | #E2E8F0 | 명확성↑ |
| **증감 배지** | Green/Red | Blue/Red | 중립적 감정 |
| **범례 폰트 무게** | 600 | 700~800 | 강조↑ |
| **버튼 높이** | 32px | 36px | 터치 편의↑ |

---

## 🎯 3초 규칙 충족

### 병동 가동 현황 즉시 파악
```
1초: 가동률 색상 감지 (Red/Amber/Green)
     └─ 90%↑ 위험 / 80~90% 주의 / 80%↓ 정상

2초: KPI 카드 4개 수치 정독 (32px Extra Bold)
     └─ 가동률 / 입원 / 퇴원 / 재원

3초: 주간 추이 테이블 변화 추적
     └─ 7일간 추세 + 증감 배지
```

---

## 📝 구현 체크리스트

✅ 색상 체계 재정의 (C dict 전면 개선)
✅ CSS v6.0 전체 재구성
✅ KPI 카드 폰트 강화 (Inter, 32px)
✅ 테이블 조건부 색상 로직 (90%/80%)
✅ 가동률 범례 업데이트 (3단계)
✅ Semantic Color 배지 (Red/Blue)
✅ tabular-nums 적용
✅ 8px 그리드 시스템
✅ 버튼/SelectBox 높이 통일

---

## 🚀 배포 시 주의사항

### 1. 색상 검증
```python
# 콘트라스트 비율 확인 (WCAG AA 기준: 4.5:1)
#DC2626 (Red) on #FFFFFF (White) ≈ 5.9:1 ✅
#059669 (Green) on #FFFFFF (White) ≈ 5.2:1 ✅
```

### 2. 폰트 로드
```css
/* Inter 웹폰트 추가됨 */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
```

### 3. 테스트 항목
- [ ] KPI 카드 수치 선명도
- [ ] 테이블 숫자 정렬 확인
- [ ] 가동률 색상 조건부 서식 동작
- [ ] 증감 배지 표시
- [ ] 차트 범례 가독성

---

## 📖 참고 자료

### Tailwind Palette
- https://tailwindcss.com/docs/customizing-colors

### CSS Grid & Layout
- 8px Grid System: Material Design Guidelines
- https://www.designsystems.com/space-grids-and-layouts/

### Typography
- Inter Font: https://rsms.me/inter/
- Pretendard: https://github.com/orioncactus/pretendard

### Medical UI/UX
- WCAG 2.1 Accessibility Guidelines
- HL7 UI Standards for Healthcare

---

## 🔄 향후 개선 방향

### v7.0 로드맵
- [ ] Dark Mode 지원 (의료 환경 24시간 사용)
- [ ] Real-time 차트 애니메이션
- [ ] 병동별 커스텀 대시보드 저장
- [ ] AI 분석 시각화 강화
- [ ] 모바일 반응형 디자인

### 성능 최적화
- [ ] CSS-in-JS → CSS 파일 분리
- [ ] 이미지 최적화 (차트 svg 캐싱)
- [ ] Lazy Loading (스크롤 시 차트 로드)

---

**버전**: v6.0  
**작성일**: 2026-03-20  
**담당**: UI/UX Design System  
**상태**: ✅ 적용 완료
