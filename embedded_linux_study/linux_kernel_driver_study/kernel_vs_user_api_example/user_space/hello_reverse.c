// SPDX-License-Identifier: MIT

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *reverse_copy(const char *source)
{
	size_t i;
	size_t length;
	char *destination;

	if (source == NULL) {
		errno = EINVAL;
		return NULL;
	}

	length = strlen(source);
	destination = malloc(length + 1);
	if (destination == NULL)
		return NULL;

	for (i = 0; i < length; ++i)
		destination[i] = source[length - i - 1];
	destination[length] = '\0';

	return destination;
}

int main(void)
{
	static const char message[] = "Hello, World!";
	char *reversed;

	reversed = reverse_copy(message);
	if (reversed == NULL) {
		perror("reverse_copy");
		return EXIT_FAILURE;
	}

	printf("original: %s\n", message);
	printf("reversed: %s\n", reversed);

	free(reversed);
	return EXIT_SUCCESS;
}
