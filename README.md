# 내 쇼츠 파인더

한국+미국 쇼츠 채널을 하루 4회 수집해 급상승·신생·히든 젬 채널을 보여주는 개인용 도구.
GitHub Actions(수집) + GitHub Pages(화면). 서버·DB 없음.

## 구조
- `collector/collect.py` — YouTube Data API 수집 (발굴 → 채널 스냅샷 → 최근 영상)
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
