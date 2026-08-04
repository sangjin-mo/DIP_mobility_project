// SPDX-License-Identifier: GPL-2.0
/*
 * Educational in-tree character driver.
 *
 * The device keeps the most recently written byte string in a 256-byte
 * kernel buffer and returns it through /dev/string_buffer.
 */

#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#define DRIVER_NAME "string_buffer"
#define DEVICE_NAME "string_buffer"
#define CLASS_NAME  "string_buffer_class"
#define BUFFER_SIZE 256

static dev_t string_buffer_dev;
static struct cdev string_buffer_cdev;
static struct class *string_buffer_class;
static struct device *string_buffer_device;

static char data_buffer[BUFFER_SIZE];
static size_t data_length;
static DEFINE_MUTEX(data_lock);

static int string_buffer_open(struct inode *inode, struct file *file)
{
	return nonseekable_open(inode, file);
}

static int string_buffer_release(struct inode *inode, struct file *file)
{
	return 0;
}

static ssize_t string_buffer_read(struct file *file, char __user *user_buffer,
				  size_t count, loff_t *offset)
{
	ssize_t result;

	if (mutex_lock_interruptible(&data_lock))
		return -ERESTARTSYS;

	result = simple_read_from_buffer(user_buffer, count, offset,
					 data_buffer, data_length);
	mutex_unlock(&data_lock);

	return result;
}

static ssize_t string_buffer_write(struct file *file,
				   const char __user *user_buffer,
				   size_t count, loff_t *offset)
{
	size_t bytes_to_copy;

	if (!count)
		return 0;

	/* Reserve one byte so the internal copy is always NUL-terminated. */
	bytes_to_copy = min_t(size_t, count, BUFFER_SIZE - 1);

	if (mutex_lock_interruptible(&data_lock))
		return -ERESTARTSYS;

	if (copy_from_user(data_buffer, user_buffer, bytes_to_copy)) {
		mutex_unlock(&data_lock);
		return -EFAULT;
	}

	data_buffer[bytes_to_copy] = '\0';
	data_length = bytes_to_copy;
	mutex_unlock(&data_lock);

	/* A short write tells user space that the input exceeded the buffer. */
	return bytes_to_copy;
}

static const struct file_operations string_buffer_fops = {
	.owner = THIS_MODULE,
	.open = string_buffer_open,
	.release = string_buffer_release,
	.read = string_buffer_read,
	.write = string_buffer_write,
	.llseek = no_llseek,
};

static int __init string_buffer_init(void)
{
	int ret;

	ret = alloc_chrdev_region(&string_buffer_dev, 0, 1, DRIVER_NAME);
	if (ret)
		return ret;

	cdev_init(&string_buffer_cdev, &string_buffer_fops);
	string_buffer_cdev.owner = THIS_MODULE;

	ret = cdev_add(&string_buffer_cdev, string_buffer_dev, 1);
	if (ret)
		goto err_unregister_region;

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 4, 0)
	string_buffer_class = class_create(CLASS_NAME);
#else
	string_buffer_class = class_create(THIS_MODULE, CLASS_NAME);
#endif
	if (IS_ERR(string_buffer_class)) {
		ret = PTR_ERR(string_buffer_class);
		goto err_del_cdev;
	}

	string_buffer_device = device_create(string_buffer_class, NULL,
					     string_buffer_dev, NULL,
					     DEVICE_NAME);
	if (IS_ERR(string_buffer_device)) {
		ret = PTR_ERR(string_buffer_device);
		goto err_destroy_class;
	}

	pr_info(DRIVER_NAME ": initialized (major=%u minor=%u)\n",
		MAJOR(string_buffer_dev), MINOR(string_buffer_dev));
	return 0;

err_destroy_class:
	class_destroy(string_buffer_class);
err_del_cdev:
	cdev_del(&string_buffer_cdev);
err_unregister_region:
	unregister_chrdev_region(string_buffer_dev, 1);
	return ret;
}

static void __exit string_buffer_exit(void)
{
	device_destroy(string_buffer_class, string_buffer_dev);
	class_destroy(string_buffer_class);
	cdev_del(&string_buffer_cdev);
	unregister_chrdev_region(string_buffer_dev, 1);
	pr_info(DRIVER_NAME ": removed\n");
}

module_init(string_buffer_init);
module_exit(string_buffer_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Embedded Linux Study");
MODULE_DESCRIPTION("Educational string-buffer character driver");
