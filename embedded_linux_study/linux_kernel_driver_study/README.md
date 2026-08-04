# Linux 커널·디바이스 드라이버 학습 그룹

Linux 커널 아키텍처부터 문자 디바이스 드라이버 개발, 커널 소스 통합, 커널 API와 사용자 공간 API 비교까지 이어지는 네 단계 학습 자료이다.

## 학습 순서

1. [리눅스 커널 아키텍처의 이해](./리눅스커널%20_%20아키텍처%20.md)
   - x86, ARM, MIPS 특성과 아키텍처별 커널 최적화
2. [기본 디바이스 드라이버 개발 방법론](./디바이스_드라이버개발_방법론.md)
   - 문자 디바이스의 등록, open/read/write/release와 외부 모듈 실습
3. [리눅스 커널 내 드라이버 통합과 관리](./리눅스_커널_내_드라이버_통합_및_관리.md)
   - Kconfig·kbuild 연결, built-in/module 선택, Raspberry Pi 4 배포와 검증
4. [커널 내부 함수와 일반 API 차이](./커널_함수_및_API_차이.md)
   - 메모리, 오류, 동시성, 로깅 API 비교와 Hello 문자열 역순 출력 실습

## 실습 폴더

| 폴더 | 내용 |
|---|---|
| [`device_driver_methodology_example`](./device_driver_methodology_example/) | 외부 문자 디바이스 모듈과 시험 스크립트 |
| [`in_tree_driver_integration`](./in_tree_driver_integration/) | 커널 트리 통합용 Kconfig·Makefile·드라이버·config fragment |
| [`kernel_vs_user_api_example`](./kernel_vs_user_api_example/) | 동일 알고리즘의 사용자 공간 C 프로그램과 커널 모듈 비교 |

## 권장 환경

- Linux 데스크탑 또는 가상머신
- 실행 중인 커널과 일치하는 개발 헤더
- GCC, GNU Make 및 ARM64 크로스 컴파일러
- Raspberry Pi 4와 복구 가능한 MicroSD 카드

커널 모듈 적재와 커널 이미지 교체는 시스템 전체에 영향을 줄 수 있으므로 가상머신에서 먼저 검증하고, 실보드에서는 정상 부팅 이미지와 직렬 콘솔 또는 예비 MicroSD를 준비한다.
