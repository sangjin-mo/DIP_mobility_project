# 커널 함수 및 일반 API 차이

## 1. 보고서 개요

### 1.1 수행 목표

본 학습은 Linux 커널 내부 API와 사용자 공간의 C 표준 라이브러리·POSIX API가 실행 환경, 메모리, 오류 처리, 동시성 및 출력 방식에서 어떻게 다른지 이해하는 것을 목적으로 한다.

- 동일한 문자열 뒤집기 기능을 사용자 프로그램과 커널 모듈로 구현한다.
- `malloc/free`와 `kmalloc/kfree`, `printf`와 `pr_info`의 차이를 비교한다.
- 사용자 포인터 접근, 오류 코드, 실행 문맥 및 locking 규칙을 학습한다.
- 커널 공간 구현이 필요한 경우와 사용자 공간을 유지해야 하는 경우를 구분한다.
- 커널 오류가 프로세스가 아니라 시스템 전체에 미칠 수 있는 영향을 이해한다.

> **실행 결과의 범위:** 이 문서와 예제는 현재 Windows 작성 환경에서 생성하고 정적 검토했다. Linux 커널 헤더와 모듈 적재 권한이 없는 환경이므로 실제 모듈 빌드·삽입을 성공했다고 기록하지 않는다. Linux 가상머신 또는 Raspberry Pi 4에서 아래 절차를 실행한 뒤 결과표를 작성한다.

## 2. 사용자 공간과 커널 공간

### 2.1 분리하는 이유

CPU의 권한 수준과 MMU가 사용자 공간과 커널 공간을 분리한다. 일반 프로세스는 자신의 가상 주소 공간과 허용된 시스템 호출만 사용한다. 커널은 모든 프로세스, 장치, 페이지 테이블 및 하드웨어를 관리할 권한이 있다.

```text
사용자 공간                                      커널 공간
┌───────────────────────────────┐                 ┌──────────────────────────────┐
│ application                   │                 │ VFS, scheduler, MM, drivers  │
│ libc: printf, malloc, pthread  │                 │ printk, kmalloc, mutex, IRQ  │
└───────────────┬───────────────┘                 └──────────────▲───────────────┘
                │ 시스템 호출 / 예외 / 복사 API                   │
                └─────────────────────────────────────────────────┘
                       검증된 경계를 통해서만 상호작용
```

이 분리는 다음 효과가 있다.

- 한 프로세스의 잘못된 포인터가 다른 프로세스나 커널 메모리를 직접 손상시키지 못하게 한다.
- 프로세스별 권한, 자원 제한, 격리 및 복구를 가능하게 한다.
- 커널 공격면을 시스템 호출과 명시적 인터페이스로 제한한다.
- 사용자 프로그램 장애는 일반적으로 해당 프로세스 종료로 제한되지만 커널 결함은 시스템 전체 장애가 될 수 있다.

커널의 self-protection 원칙도 사용자 메모리를 예상 없이 접근하거나 실행해서는 안 된다고 설명한다. x86의 SMEP/SMAP, ARM의 PXN/PAN 같은 하드웨어 기능이 이 경계를 강화한다.

### 2.2 “일반 API”의 의미

사용자 프로그램에서 호출하는 모든 함수가 곧 시스템 호출은 아니다.

- `strlen()` 같은 함수는 보통 사용자 공간 라이브러리 코드만 실행한다.
- `printf()`는 사용자 버퍼링과 포맷 처리를 수행한 뒤 필요할 때 `write` 시스템 호출을 사용한다.
- `malloc()`은 allocator가 이미 확보한 heap을 관리하고 필요할 때 `brk` 또는 `mmap` 계열 시스템 호출을 이용한다.
- `pthread_mutex_lock()`은 uncontended fast path를 사용자 공간에서 처리하고 필요한 경우 futex를 사용할 수 있다.

반면 커널 내부 함수는 같은 커널 주소 공간에서 직접 호출된다. libc를 링크하지 않으며 커널이 제공하는 헤더, 자료구조, allocator, locking 및 logging API를 사용한다. 이름이 비슷해도 의미와 호출 가능 문맥이 같다고 가정하면 안 된다.

## 3. 주요 API 비교

| 목적 | 사용자 공간 API | 커널 API | 핵심 차이 |
|---|---|---|---|
| 동적 메모리 | `malloc`, `calloc`, `free` | `kmalloc`, `kzalloc`, `kvzalloc`, `kfree` | 커널은 크기뿐 아니라 `GFP_*`로 sleep·reclaim 가능한 문맥을 표현 |
| 출력 | `printf`, `fprintf` | `pr_info`, `pr_err`, `printk` | stdout/stderr 대신 kernel ring buffer; 로그 레벨과 rate limit 필요 |
| 문자열 | libc `strlen`, `memcpy` | 커널 `strlen`, `memcpy`, `strscpy` | 구현과 헤더가 다르며 커널 printf는 부동소수점 등 제약 존재 |
| 오류 | `-1`/`NULL` + `errno`, 예외 없음 | `-E...`, `NULL`, `ERR_PTR/PTR_ERR/IS_ERR` | 각 함수의 반환 규약을 문서에서 확인해야 함 |
| 동기화 | pthread mutex/rwlock/condvar | mutex, spinlock, completion, wait queue, atomic, RCU | process/IRQ/softirq 문맥과 sleep 가능 여부가 선택을 결정 |
| 사용자 메모리 | 일반 포인터 역참조 | `copy_from_user`, `copy_to_user`, `get_user`, `put_user` | `__user` 포인터를 커널이 직접 신뢰하면 안 됨 |
| 시간 지연 | `sleep`, `nanosleep` | `msleep`, `usleep_range`, timer, workqueue | atomic context에서는 sleep 불가; 목적에 맞는 메커니즘 필요 |
| 파일 접근 | `open/read/write/close` | `filp_open` 등이 있으나 남용 금지 | 커널이 사용자 파일을 직접 다루는 설계는 namespace·권한·재귀 문제 유발 |

## 4. 메모리 접근과 할당

### 4.1 사용자 공간

```c
char *buffer = malloc(length + 1);
if (buffer == NULL) {
    perror("malloc");
    return EXIT_FAILURE;
}
/* 사용 */
free(buffer);
```

`malloc()`은 실패하면 `NULL`을 반환하고 `errno`를 설정한다. 잘못된 접근은 보통 해당 프로세스의 `SIGSEGV`로 이어진다. 운영체제가 프로세스별 가상 메모리를 격리하므로 다른 프로세스와 커널에 대한 직접 피해를 제한한다.

### 4.2 커널 공간

```c
char *buffer = kmalloc(length + 1, GFP_KERNEL);
if (!buffer)
    return -ENOMEM;
/* 사용 */
kfree(buffer);
```

`GFP_KERNEL`은 reclaim 과정에서 sleep할 수 있으므로 process context에서 사용한다. interrupt/atomic context에서는 sleep 가능한 함수를 호출할 수 없으며, 정말 필요한 경우 문맥에 맞는 non-sleeping 할당과 사전 할당을 검토한다. `GFP_ATOMIC`은 성공을 보장하지 않고 비상 reserve를 사용할 수 있으므로 편의 목적으로 남용하지 않는다.

크기와 목적에 따른 일반 선택은 다음과 같다.

- 작은 물리적으로 연속된 객체: `kmalloc/kzalloc`
- 큰 가상 연속 영역: `vmalloc/kvzalloc`
- 배열 곱셈 overflow 방지: `kmalloc_array/kcalloc`
- device 수명에 묶인 자원: `devm_kmalloc` 등 devres API
- DMA: 일반 `kmalloc`이 아니라 장치와 DMA API 제약을 확인

### 4.3 사용자 포인터

시스템 호출 또는 드라이버 콜백이 받은 `char __user *`를 커널 포인터처럼 직접 역참조하면 안 된다.

```c
if (copy_from_user(kernel_buffer, user_buffer, count))
    return -EFAULT;
```

특히 `copy_from_user()`와 `copy_to_user()`의 반환값은 성공 시 0, 실패 시 음수 errno가 아니라 **복사하지 못한 바이트 수**이다. 또한 page fault 처리 때문에 sleep할 수 있으므로 spinlock을 잡은 상태나 interrupt context에서 호출하면 안 된다.

## 5. 오류 처리 차이

### 5.1 사용자 공간 규약

사용자 API는 함수별로 `NULL`, `-1`, 오류 번호 자체 또는 pthread 오류 번호를 반환할 수 있다. `errno`는 실패를 나타내는 반환값을 확인한 뒤에만 읽어야 하며, 성공 후의 값에는 의미가 없다.

```c
FILE *file = fopen(path, "r");
if (file == NULL) {
    fprintf(stderr, "fopen: %s\n", strerror(errno));
    return EXIT_FAILURE;
}
```

### 5.2 커널 규약

커널 함수는 흔히 성공 시 0, 실패 시 `-EINVAL`, `-ENOMEM`, `-EFAULT` 같은 음수 errno를 반환한다. 포인터 반환 API 일부는 오류를 pointer 값에 encode한다.

```c
device = device_create(...);
if (IS_ERR(device))
    return PTR_ERR(device);
```

`NULL` 실패인지 `ERR_PTR` 실패인지 API마다 다르므로 추측하지 말고 문서와 선언을 확인한다. 초기화가 여러 단계라면 확보한 자원을 역순으로 해제한다.

```text
resource A 획득
  └─ resource B 실패
       └─ A 해제 후 오류 반환
```

커널에서 오류를 무시하면 단일 작업 실패를 넘어 use-after-free, double free, deadlock, 메모리 손상과 panic으로 확대될 수 있다.

## 6. 동시성 제어 차이

### 6.1 사용자 공간

일반 스레드는 block/sleep할 수 있어 `pthread_mutex_t`, condition variable, semaphore, read-write lock을 사용한다. 잘못된 locking은 해당 프로세스를 deadlock시키거나 data race를 만들지만 보통 다른 프로세스와 커널은 계속 실행된다.

### 6.2 커널 공간

커널에서는 “공유 데이터인가?”뿐 아니라 “어떤 실행 문맥인가?”를 먼저 확인한다.

| 문맥 | sleep 가능 | 대표 동기화 |
|---|---:|---|
| process context | 조건부 가능 | mutex, semaphore, completion, wait queue |
| hard IRQ | 불가 | spinlock/atomic, 최소 처리 후 threaded IRQ 또는 workqueue로 전달 |
| softirq/tasklet | 불가 | spinlock/atomic, work 분리 |
| NMI | 극도로 제한 | NMI-safe API만 사용 |

mutex는 sleeping lock이므로 IRQ, timer, tasklet에서 사용할 수 없다. spinlock을 잡거나 preemption/interrupt가 비활성화된 상태에서도 sleep 가능한 `GFP_KERNEL`, `copy_*_user`, mutex 획득 등을 호출하면 안 된다.

성능을 위해 lock을 무조건 없애는 것도 답이 아니다. 다음 순서로 설계한다.

1. 데이터 소유권을 나누어 공유 자체를 줄인다.
2. 필요한 최소 상태만 임계 구역에 둔다.
3. 실행 문맥에 맞는 가장 단순한 primitive를 선택한다.
4. lock ordering을 문서화하고 lockdep으로 검증한다.
5. 실제 contention을 측정한 뒤 per-CPU, RCU, lockless 구조를 검토한다.

## 7. 문자열 뒤집기 비교 실습

동봉된 예제는 두 환경에서 같은 결과를 만든다.

```text
original: Hello, World!
reversed: !dlroW ,olleH
```

### 7.1 사용자 프로그램 핵심 코드

```c
length = strlen(source);
destination = malloc(length + 1);
if (destination == NULL)
    return NULL;

for (i = 0; i < length; ++i)
    destination[i] = source[length - i - 1];
destination[length] = '\0';

printf("reversed: %s\n", destination);
free(destination);
```

빌드 및 실행:

```bash
cd kernel_vs_user_api_example/user_space
make
./hello_reverse
```

### 7.2 커널 모듈 핵심 코드

```c
reversed = kmalloc(length + 1, GFP_KERNEL);
if (!reversed)
    return -ENOMEM;

for (i = 0; i < length; ++i)
    reversed[i] = message[length - i - 1];
reversed[length] = '\0';

pr_info("reversed: %s\n", reversed);
kfree(reversed);
```

빌드 및 실행:

```bash
cd kernel_vs_user_api_example/kernel_module
make
sudo insmod hello_reverse_module.ko
sudo dmesg | tail -n 10
sudo rmmod hello_reverse_module
```

### 7.3 관찰되는 차이

| 관찰 항목 | 사용자 프로그램 | 커널 모듈 |
|---|---|---|
| 실행 시작 | `main()` | `module_init()` 등록 함수 |
| 종료 | `main` 반환·프로세스 종료 | `rmmod` 시 `module_exit()` |
| 출력 위치 | 터미널 stdout | kernel ring buffer, `dmesg` |
| 할당 | `malloc` | `kmalloc(..., GFP_KERNEL)` |
| 할당 오류 | `NULL`과 `errno` | `NULL` 확인 후 `-ENOMEM` 반환 |
| 권한 | 일반 사용자 가능 | 빌드 후 적재에 관리자 권한 필요 |
| 장애 범위 | 주로 해당 프로세스 | oops, 데이터 손상, panic 가능 |
| 업데이트 | 실행 파일 교체 | 모듈 ABI·서명·커널 버전 일치 필요 |

이 예제는 module init의 private 변수만 사용하며 동시 호출되는 공유 데이터가 없다. 따라서 mutex를 넣지 않는 것이 올바르다. 필요 없는 lock은 코드 복잡도와 비용만 높인다.

### 7.4 결과 기록표

| 항목 | 기대 결과 | 실제 결과 |
|---|---|---|
| 사용자 프로그램 빌드 | warning/error 없음 | 실습 후 기록 |
| 사용자 출력 | 원문과 역문자열 | 실습 후 기록 |
| 모듈 빌드 | 실행 커널용 `.ko` 생성 | 실습 후 기록 |
| `insmod` | 반환값 0 | 실습 후 기록 |
| 커널 로그 | 원문과 역문자열 | 실습 후 기록 |
| `rmmod` | 제거 로그, 오류 없음 | 실습 후 기록 |
| 시스템 상태 | oops/WARN/panic 없음 | 실습 후 기록 |

두 구현의 알고리즘은 모두 길이 `n`에 대해 시간 복잡도 `O(n)`, 추가 공간 `O(n)`이다. 작은 문자열에서 출력과 모듈 적재 비용이 알고리즘보다 훨씬 크므로 이 예제로 커널 구현이 더 빠르다고 결론 내리면 안 된다.

## 8. 일반 기능이 커널 구현으로 재구성된 사례: ksmbd

“일반 프로그램을 그대로 커널 모듈로 변환”하는 일은 드물고 권장되지 않는다. libc, 주소 공간, blocking 방식, 보안 경계가 다르기 때문에 보통 같은 기능을 커널 API에 맞춰 **재설계**한다.

현실적인 사례가 Linux의 SMB3 서버 `ksmbd`다. SMB 파일 공유는 Samba 같은 사용자 공간 서버로 널리 제공되어 왔다. `ksmbd`는 Samba 코드를 그대로 모듈로 바꾼 것이 아니라, 성능 민감한 파일 연산을 커널 공간에 구현하고 관리·인증 성격의 기능은 사용자 공간 daemon에 남긴 별도 구현이다.

```text
SMB client
    │ TCP/445
    ▼
ksmbd (kernel)
  open/read/write/close, VFS 연동, 병렬 work 처리
    │ netlink
    ▼
ksmbd.mountd (user space)
  사용자·공유 설정, 계정/암호, 일부 DCE/RPC 관리
```

커널 구현을 선택한 이유는 다음과 같다.

- 파일 I/O fast path를 VFS와 직접 연동한다.
- 요청을 kernel worker에 분배해 병렬로 처리한다.
- 사용자/커널 경계를 오가는 일부 데이터 이동과 전환 비용을 줄일 여지가 있다.

그러나 모든 기능을 커널로 옮기지 않았다. 사용자 관리와 DCE/RPC처럼 복잡하고 보안 사고 이력이 있는 관리 기능은 사용자 공간에 남겨 커널 공격면과 장애 영향을 줄였다. 이 사례의 핵심은 “커널이 항상 빠르다”가 아니라, 측정상 가치가 있는 data path만 커널에 두고 policy/control plane은 사용자 공간에 유지하는 분할 설계다.

## 9. 커널 프로그래밍의 안전성

### 9.1 치명적인 오류가 발생하면

- `WARN`: 잘못된 상태를 보고하지만 보통 실행은 계속한다.
- `Oops`: 커널이 예외를 기록하고 문제를 일으킨 task를 종료하거나 경로에서 벗어날 수 있지만, 이미 lock이나 상태가 손상됐을 수 있다. 이후 커널은 신뢰하기 어렵다.
- `panic`: 복구 불가능하다고 판단해 커널이 정지하거나 설정에 따라 재부팅한다.

따라서 “Oops 후 계속 동작하니 복구됐다”고 판단하면 안 된다. 전체 로그, taint 상태, `vmlinux`, `System.map`, `.config`, 커널 커밋을 보존하고 재부팅 가능한 실습 환경에서 재현한다.

### 9.2 주요 위험과 대응

| 위험 | 결과 | 대응 |
|---|---|---|
| 사용자 포인터 직접 접근 | fault, 정보 노출, 임의 메모리 접근 | uaccess API, 크기·overflow 검증 |
| unchecked allocation | NULL dereference | 모든 반환값 확인, 실패 경로 설계 |
| use-after-free/double free | 메모리 손상, 권한 상승 | 명확한 소유권, refcount, devres, KASAN |
| buffer overflow | 데이터 손상, 보안 취약점 | 길이 검증, size overflow helper, FORTIFY |
| 잘못된 lock | deadlock, sleep-in-atomic, race | 문맥 분석, lock ordering, lockdep |
| 과도한 printk | hot path 지연·로그 폭주 | `pr_debug`, dynamic debug, rate-limited log, tracepoint |
| 긴 IRQ 처리 | latency, packet loss, lockup | top half 최소화, threaded IRQ/workqueue |
| 불안정한 내부 API 의존 | 다음 커널에서 빌드 실패 | 지원 커널 범위 정의, CI 다중 버전 빌드 |

## 10. 성능 고려사항

커널 내부 호출은 시스템 호출 경계가 없어 빠를 수 있지만 다음 비용과 위험이 있다.

- cache miss, lock contention, interrupt disable 시간이 시스템 전체 latency에 영향을 준다.
- allocation reclaim과 page fault는 예상보다 오래 block될 수 있다.
- copy를 줄이려는 zero-copy 설계는 page pinning, DMA lifetime, 보안 검증을 복잡하게 한다.
- module code도 kernel text/data와 메모리를 사용하며 디버그 출력은 hot path를 크게 왜곡할 수 있다.
- 커널에 기능을 넣으면 crash blast radius와 유지보수 비용이 커진다.

최적화 순서는 측정 → 병목 확인 → 최소 변경 → 회귀 시험이다. `perf`, ftrace, tracepoint, lockstat, eBPF 기반 관측 도구를 목적에 맞게 사용하고, 사용자 공간 구현으로 요구 성능을 만족한다면 커널 이전을 피한다.

## 11. 개발 및 검증 방법론

### 11.1 빌드 검사

```bash
# 사용자 프로그램
make CFLAGS='-O2 -Wall -Wextra -Wpedantic -Werror'

# 커널 모듈
make
make -C /lib/modules/$(uname -r)/build M=$PWD W=1 modules
```

추가로 sparse, `scripts/checkpatch.pl`, Coccinelle, KASAN, UBSAN, lockdep, kmemleak를 단계적으로 적용한다. 진단 옵션의 성능 overhead는 운영 성능 결과와 분리한다.

### 11.2 안전한 시험 순서

1. 소스 정적 검사와 warning 없는 빌드를 확인한다.
2. 복구 가능한 VM에서 모듈을 적재한다.
3. `dmesg -w`로 삽입·제거 로그를 관찰한다.
4. 반복 삽입·제거, 실패 주입, 경계값과 동시성을 시험한다.
5. Raspberry Pi 4에서 장시간 시험과 reboot 후 상태를 확인한다.
6. oops나 WARN이 한 번이라도 발생하면 전체 로그를 분석하기 전 운영에 사용하지 않는다.

## 12. 평가 질문 답변

### 커널 내부 함수와 일반 API의 핵심 차이는 무엇인가?

호출되는 권한과 실행 문맥이다. 사용자 API는 프로세스의 격리된 주소 공간, libc/POSIX 규약 및 block 가능한 thread 문맥을 전제로 한다. 커널 API는 공유 kernel address space에서 process/IRQ 등 서로 다른 문맥으로 호출되며, 잘못된 호출은 시스템 전체에 영향을 준다.

### 일반 프로그램을 커널 모듈로 바꾸는 목적은 무엇인가?

하드웨어 직접 관리, IRQ 처리, 커널 서브시스템/VFS/network data path와 긴밀한 통합, 매우 짧은 latency 등 사용자 공간만으로 충족하기 어려운 요구가 있을 때 검토한다. 단순 계산이나 문자열 처리는 커널 모듈로 만들 이유가 없으며 격리·개발 편의·안전성 때문에 사용자 공간이 적합하다.

### 커널 영역과 사용자 영역은 왜 분리하는가?

최소 권한과 장애 격리를 위해서다. 사용자 프로그램에 전체 하드웨어·메모리 권한을 주지 않고 시스템 호출에서 포인터·길이·권한을 검사함으로써 한 프로그램의 오류나 공격이 시스템 전체로 확산되는 것을 제한한다.

### 커널에서 치명적인 오류가 나면 어떻게 되는가?

오류 종류와 설정에 따라 task 종료 및 Oops, lockup, 데이터 손상, kernel panic과 재부팅으로 이어질 수 있다. Oops 후에도 실행은 가능하지만 상태가 손상됐을 수 있어 정상 복구로 간주하지 않는다.

### 성능과 안전성을 함께 확보하는 방법은 무엇인가?

복잡한 기능과 policy는 사용자 공간에 두고 꼭 필요한 최소 data path만 커널에 둔다. 커널에서는 실행 문맥, 자원 수명, 모든 오류 경로, lock 순서를 설계한 뒤 정적 분석과 KASAN/lockdep, stress test 및 실측 profiling으로 검증한다.

## 13. 수행 체크리스트

- [ ] 사용자 프로그램을 warning 없이 컴파일했다.
- [ ] 사용자 출력의 원문과 역문자열을 확인했다.
- [ ] 실행 커널과 일치하는 헤더로 모듈을 빌드했다.
- [ ] `insmod`와 `rmmod`가 성공했다.
- [ ] `dmesg`에서 원문, 역문자열, 제거 로그를 확인했다.
- [ ] `malloc/kmalloc`, `errno/-errno`, `printf/pr_info` 차이를 설명할 수 있다.
- [ ] `copy_from_user()` 반환값과 sleep 가능성을 설명할 수 있다.
- [ ] mutex와 spinlock의 사용 문맥을 구분할 수 있다.
- [ ] Oops와 panic의 차이 및 대응법을 설명할 수 있다.
- [ ] ksmbd가 kernel/user 혼합 구조를 선택한 이유를 설명할 수 있다.

## 14. 획득 역량

| 역량명 | 달성 내용 |
|---|---|
| 커널 프로그래밍과 일반 API 차이 이해 | 실행 권한, 메모리, 오류, 출력 및 수명 규약 비교 |
| 커널 안전성과 성능 고려 | 실행 문맥, 자원 해제, locking, 진단 및 측정 방법 이해 |
| 커널 및 사용자 공간 프로그래밍 기술 | 동일 알고리즘의 사용자 실행 파일과 커널 모듈 구현·시험 |

## 15. 참고 자료

- [Linux Kernel: Memory Management APIs](https://docs.kernel.org/core-api/mm-api.html)
- [Linux Kernel: Memory Allocation Guide](https://docs.kernel.org/core-api/memory-allocation.html)
- [Linux Kernel: Generic Mutex Subsystem](https://docs.kernel.org/locking/mutex-design.html)
- [Linux Kernel: Lock types and their rules](https://docs.kernel.org/locking/locktypes.html)
- [Linux Kernel: Message logging with printk](https://docs.kernel.org/core-api/printk-basics.html)
- [Linux Kernel: Kernel Self-Protection](https://docs.kernel.org/security/self-protection.html)
- [Linux Kernel: KSMBD - SMB3 Kernel Server](https://docs.kernel.org/filesystems/smb/ksmbd.html)
- [Linux Kernel Module Programming Guide](https://sysprog21.github.io/lkmpg/)
- [Linux man-pages: malloc(3)](https://man7.org/linux/man-pages/man3/malloc.3.html)
