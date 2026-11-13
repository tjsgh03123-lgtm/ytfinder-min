# 내 쇼츠 파인더

한국+미국 쇼츠 채널을 하루 4회 수집해 급상승·신생·히든 젬 채널을 보여주는 개인용 도구.
GitHub Actions(수집) + GitHub Pages(화면). 서버·DB 없음.

## 구조
- `collector/collect.py` — YouTube Data API 수집 (발굴 → 채널 스냅샷 → 최근 영상)
- `collector/kwsearch.py` — 키워드 검색 / 채널 인기순 검색 (결과: `docs/data/kw.json`)
- `collector/compute.py` — 비율·꿀통/떡상 지수·급상승 등 계산 → `docs/data/*.json`
- `docs/index.html` — 화면 (대시보드 / 데일리 떡상 / 채널 탐색 / 내 활동)
- `.github/workflows/collect.yml` — 6시간마다 실행 + 배포
- `config.json` — 국가·카테고리·임계값·할당량 예산

## 할당량
공식 문서 기준 기본 할당량은 search.list 일 100회 + 그 외 일 10,000 units(별도 버킷).
search는 발굴에만 쓰고 회당 20회(일 80회, 예산 90회). 채널 스냅샷은 50개당 1 unit.
예상 사용량은 하루 약 2,000~3,000 units. 예산 도달 시 그 실행은 안전하게 중단.
태평양 시간 자정에 리셋되므로 한국 시간으로는 오후 5시(여름 4시)쯤 초기화됩니다.

## 테스트
`python3 tests/fake_test.py` — 가짜 API로 수집→계산 전체 검증.

## 키워드 검색 (API 키는 브라우저에 노출되지 않음)
- **수집 데이터에서 즉시 검색:** 화면의 "키워드" 탭 (제목·채널명, 여러 단어는 AND)
- **YouTube에서 새로 검색:** Actions → collect → Run workflow → `keyword` / `region` / `days` 입력.
  채널 인기순은 `channel`에 채널 ID 또는 @핸들. 결과는 1~2분 뒤 "키워드" 탭과 채널 상세에 표시.
- **추적 키워드:** `config.json`의 `keywords`에 `{"q":"야구","region":"KR","days":30}` 형태로 넣으면 정기 수집마다 회전 실행.
- 검색 호출은 하루 약 100회 한도를 정기 수집(회당 14회)과 나눠 쓴다.

## 영상에서 가져온 절차를 반영한 기능
레퍼런스 채널 인기순 보기, 영상별 원본 찾기 링크(구글 렌즈·유튜브·구글·틱톡), 같은 제목이 여러 채널에서 터진 "같은 소재 N곳" 표시,
방송사/공식 채널로 보이는 이름의 ⚠ 권리자 표시(이름 휴리스틱일 뿐 권리 판단 아님).
