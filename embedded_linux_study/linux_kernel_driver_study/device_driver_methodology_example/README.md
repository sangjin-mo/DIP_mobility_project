# 256바이트 문자 디바이스 드라이버

`string_char_driver.c`는 `/dev/string_buffer`를 생성하는 교육용 외부 커널 모듈이다.

## 기능

- 동적 major, minor 0 할당
- `open`, `release`, `read`, `write` 구현
- 최근에 쓴 문자열 반환
- 내부 버퍼 256바이트
- 최대 payload 255바이트와 문자열 종료 문자
- mutex 기반 동시성 보호
- 초기화 실패 시 역순 자원 정리

## 빌드와 자동 시험

실행 중인 커널과 일치하는 헤더가 필요하다.

```bash
sudo apt install build-essential linux-headers-$(uname -r)
sudo sh ./test_driver.sh
```

수동 실행:

```bash
make
sudo insmod ./string_char_driver.ko
printf '%s' 'hello driver' | sudo tee /dev/string_buffer >/dev/null
sudo cat /dev/string_buffer
sudo rmmod string_char_driver
```

다른 커널 트리 또는 ARM64 크로스 빌드:

```bash
make KDIR=/path/to/prepared/kernel \
    ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
```

## 호환성

`class_create()` API 변경을 고려하여 Linux 6.4 이상과 이전 커널을 전처리 조건으로 구분한다. 실제 지원 여부는 대상 커널 헤더로 빌드해 확인한다.

## 주의

- VM 또는 복구 가능한 시험 보드에서 먼저 실행한다.
- `dmesg -w`로 warning과 Oops를 감시한다.
- 강제 모듈 적재·제거 옵션을 사용하지 않는다.
- 이 예제는 실제 MMIO, IRQ, DMA 또는 GPIO를 제어하지 않는다.
- Windows에서는 `.ko`를 빌드하거나 적재할 수 없다.
