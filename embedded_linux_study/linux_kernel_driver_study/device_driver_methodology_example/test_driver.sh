#!/bin/sh
set -eu

MODULE_NAME="string_char_driver"
MODULE_FILE="./${MODULE_NAME}.ko"
DEVICE_FILE="/dev/string_buffer"
LOADED_BY_SCRIPT=0

cleanup() {
	if [ "$LOADED_BY_SCRIPT" -eq 1 ]; then
		rmmod "$MODULE_NAME" 2>/dev/null || true
	fi
}

trap cleanup EXIT INT TERM

if [ "$(id -u)" -ne 0 ]; then
	echo "Run as root: sudo sh ./test_driver.sh" >&2
	exit 1
fi

if grep -q "^${MODULE_NAME} " /proc/modules; then
	echo "${MODULE_NAME} is already loaded; unload it first." >&2
	exit 1
fi

make clean
make

insmod "$MODULE_FILE"
LOADED_BY_SCRIPT=1

if [ ! -c "$DEVICE_FILE" ]; then
	echo "Character device was not created: ${DEVICE_FILE}" >&2
	exit 1
fi

first_message="hello kernel driver"
printf '%s' "$first_message" > "$DEVICE_FILE"
first_result="$(cat "$DEVICE_FILE")"

if [ "$first_result" != "$first_message" ]; then
	echo "First read mismatch: '${first_result}'" >&2
	exit 1
fi

second_message="second message"
printf '%s' "$second_message" > "$DEVICE_FILE"
second_result="$(cat "$DEVICE_FILE")"

if [ "$second_result" != "$second_message" ]; then
	echo "Second read mismatch: '${second_result}'" >&2
	exit 1
fi

modinfo "$MODULE_FILE" | sed -n '1,8p'

rmmod "$MODULE_NAME"
LOADED_BY_SCRIPT=0

if [ -e "$DEVICE_FILE" ]; then
	echo "Device node still exists after module removal." >&2
	exit 1
fi

echo "PASS: load, write, read, overwrite, and unload succeeded."
