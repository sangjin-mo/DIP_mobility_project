obj-m += string_char_driver.o

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(CURDIR)

.PHONY: all clean install

all:
	$(MAKE) -C "$(KDIR)" M="$(PWD)" W=1 modules

clean:
	$(MAKE) -C "$(KDIR)" M="$(PWD)" clean

install:
	$(MAKE) -C "$(KDIR)" M="$(PWD)" modules_install
