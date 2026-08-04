CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -Wpedantic

.PHONY: all clean run

all: hello_reverse

hello_reverse: hello_reverse.c
	$(CC) $(CFLAGS) -o $@ $<

run: hello_reverse
	./hello_reverse

clean:
	$(RM) hello_reverse
