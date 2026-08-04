// SPDX-License-Identifier: GPL-2.0

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/errno.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/string.h>

static int __init hello_reverse_init(void)
{
	static const char message[] = "Hello, World!";
	const size_t length = strlen(message);
	char *reversed;
	size_t i;

	/* Module initialization runs in process context, so GFP_KERNEL is valid. */
	reversed = kmalloc(length + 1, GFP_KERNEL);
	if (!reversed)
		return -ENOMEM;

	for (i = 0; i < length; ++i)
		reversed[i] = message[length - i - 1];
	reversed[length] = '\0';

	pr_info("original: %s\n", message);
	pr_info("reversed: %s\n", reversed);

	kfree(reversed);
	return 0;
}

static void __exit hello_reverse_exit(void)
{
	pr_info("module removed\n");
}

module_init(hello_reverse_init);
module_exit(hello_reverse_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Embedded Linux Study");
MODULE_DESCRIPTION("Kernel/user API comparison: reverse a string");
