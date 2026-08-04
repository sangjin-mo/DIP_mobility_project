# 커널 소스 트리 통합용 문자 드라이버

이 폴더는 `string_buffer` 문자 드라이버를 Linux 커널 소스의 `drivers/char/` 아래에 통합하기 위한 실습 자료이다.

## 배치 구조

```text
linux/
└─ drivers/char/
   ├─ Kconfig                         # source 한 줄 추가
   ├─ Makefile                        # obj-$(CONFIG_...) 한 줄 추가
   └─ string_buffer/                  # 이 폴더를 그대로 복사
      ├─ Kconfig
      ├─ Makefile
      └─ string_buffer.c
```

커널 소스 루트에서 다음과 같이 복사한다.

```bash
cp -a /path/to/in_tree_driver_integration/drivers/char/string_buffer \
      drivers/char/
```

`drivers/char/Kconfig`의 `endmenu` 앞쪽에 다음 줄을 추가한다.

```kconfig
source "drivers/char/string_buffer/Kconfig"
```

`drivers/char/Makefile`에 다음 줄을 추가한다.

```make
obj-$(CONFIG_STRING_BUFFER_DRIVER) += string_buffer/
```

하위 `drivers/char/string_buffer/Makefile`에도 같은 tristate 심볼을 사용한다.

```make
obj-$(CONFIG_STRING_BUFFER_DRIVER) += string_buffer.o
```

상위 디렉터리가 `=m`으로 방문됐는데 하위 오브젝트를 `obj-y`에만 넣으면 해당 오브젝트가 커널이나 모듈에 링크되지 않고 고아 상태가 될 수 있다.

설정 후 확인:

```bash
make menuconfig
grep '^CONFIG_STRING_BUFFER_DRIVER=' .config
```

메뉴 위치는 `Device Drivers -> Character devices -> Educational string-buffer character driver`이다. `Y`는 커널 내장, `M`은 `string_buffer.ko`, `N`은 제외를 뜻한다.

실습의 전체 빌드·부팅·검증 절차와 안전 주의사항은 상위의 `리눅스_커널_내_드라이버_통합_및_관리.md`를 따른다.
