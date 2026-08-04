# 커널 함수와 사용자 공간 API 비교 실습

같은 문자열 `Hello, World!`를 출력한 뒤 역순으로 출력하는 두 프로그램이다.

```text
kernel_vs_user_api_example/
├─ user_space/
│  ├─ hello_reverse.c
│  └─ Makefile
└─ kernel_module/
   ├─ hello_reverse_module.c
   └─ Makefile
```

## 1. 사용자 공간 프로그램

```bash
cd user_space
make
./hello_reverse
```

예상 출력:

```text
original: Hello, World!
reversed: !dlroW ,olleH
```

## 2. 커널 모듈

현재 실행 중인 커널과 일치하는 개발 헤더가 필요하다.

```bash
cd kernel_module
make
sudo insmod hello_reverse_module.ko
sudo dmesg | tail -n 10
sudo rmmod hello_reverse_module
sudo dmesg | tail -n 10
```

예상 로그:

```text
hello_reverse_module: original: Hello, World!
hello_reverse_module: reversed: !dlroW ,olleH
hello_reverse_module: module removed
```

`insmod` 전에 별도의 터미널에서 `sudo dmesg -w`를 실행하면 로그 발생 시점을 관찰하기 쉽다.

## 3. 안전 주의사항

- 가상머신 또는 복구 가능한 실습 장비에서 먼저 시험한다.
- 실행 중인 커널과 `/lib/modules/$(uname -r)/build`의 헤더가 일치해야 한다.
- 빌드 오류나 `insmod` 실패를 무시하지 않는다.
- 모듈 삽입 후 oops, WARN, lockup이 보이면 즉시 실습을 중지하고 전체 로그를 보존한다.
- 이 예제는 초기화 함수의 private 데이터만 사용하므로 불필요한 lock을 넣지 않았다.

전체 API 비교, 결과 분석 및 평가 질문 답변은 `커널_함수_및_API_차이.md`를 참고한다.
