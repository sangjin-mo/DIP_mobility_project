// SPDX-License-Identifier: GPL-2.0
#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/sched.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#define DEVICE_NAME "string_buffer"
#define CLASS_NAME "string_buffer_class"
#define DEVICE_BUFFER_SIZE 256

static dev_t device_number;
static struct cdev string_cdev;
static struct class *string_class;
static struct device *string_device;

static char device_buffer[DEVICE_BUFFER_SIZE];
static size_t device_buffer_length;
static DEFINE_MUTEX(device_buffer_lock);

static int string_open(struct inode *inode, struct file *file)
{
	pr_debug(DEVICE_NAME ": opened by pid %d\n", current->pid);
	return nonseekable_open(inode, file);
}

static int string_release(struct inode *inode, struct file *file)
{
	pr_debug(DEVICE_NAME ": released by pid %d\n", current->pid);
	return 0;
}

static ssize_t string_read(struct file *file, char __user *user_buffer,
			   size_t count, loff_t *offset)
{
	ssize_t result;

	if (mutex_lock_interruptible(&device_buffer_lock))
		return -ERESTARTSYS;

	result = simple_read_from_buffer(user_buffer, count, offset,
					 device_buffer, device_buffer_length);

	mutex_unlock(&device_buffer_lock);
	return result;
}

static ssize_t string_write(struct file *file,
			    const char __user *user_buffer,
			    size_t count, loff_t *offset)
{
	char temporary_buffer[DEVICE_BUFFER_SIZE];

	if (count == 0)
		return 0;

	if (count >= DEVICE_BUFFER_SIZE)
		return -EMSGSIZE;

	if (copy_from_user(temporary_buffer, user_buffer, count))
		return -EFAULT;

	temporary_buffer[count] = '\0';

	if (mutex_lock_interruptible(&device_buffer_lock))
		return -ERESTARTSYS;

	memcpy(device_buffer, temporary_buffer, count + 1);
	device_buffer_length = count;
	*offset = 0;

	mutex_unlock(&device_buffer_lock);
	return count;
}

static const struct file_operations string_fops = {
	.owner = THIS_MODULE,
	.open = string_open,
	.release = string_release,
	.read = string_read,
	.write = string_write,
	.llseek = no_llseek,
};

static int __init string_driver_init(void)
{
	int result;

	result = alloc_chrdev_region(&device_number, 0, 1, DEVICE_NAME);
	if (result) {
		pr_err(DEVICE_NAME ": alloc_chrdev_region failed: %d\n",
		       result);
		return result;
	}

	cdev_init(&string_cdev, &string_fops);
	string_cdev.owner = THIS_MODULE;

	result = cdev_add(&string_cdev, device_number, 1);
	if (result) {
		pr_err(DEVICE_NAME ": cdev_add failed: %d\n", result);
		goto unregister_region;
	}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 4, 0)
	string_class = class_create(CLASS_NAME);
#else
	string_class = class_create(THIS_MODULE, CLASS_NAME);
#endif
	if (IS_ERR(string_class)) {
		result = PTR_ERR(string_class);
		pr_err(DEVICE_NAME ": class_create failed: %d\n", result);
		goto delete_cdev;
	}

	string_device = device_create(string_class, NULL, device_number, NULL,
				      DEVICE_NAME);
	if (IS_ERR(string_device)) {
		result = PTR_ERR(string_device);
		pr_err(DEVICE_NAME ": device_create failed: %d\n", result);
		goto destroy_class;
	}

	device_buffer[0] = '\0';
	device_buffer_length = 0;

	pr_info(DEVICE_NAME ": loaded; major=%d minor=%d buffer=%d bytes\n",
		MAJOR(device_number), MINOR(device_number), DEVICE_BUFFER_SIZE);
	return 0;

destroy_class:
	class_destroy(string_class);
delete_cdev:
	cdev_del(&string_cdev);
unregister_region:
	unregister_chrdev_region(device_number, 1);
	return result;
}

static void __exit string_driver_exit(void)
{
	device_destroy(string_class, device_number);
	class_destroy(string_class);
	cdev_del(&string_cdev);
	unregister_chrdev_region(device_number, 1);

	pr_info(DEVICE_NAME ": unloaded\n");
}

module_init(string_driver_init);
module_exit(string_driver_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Embedded Linux Study");
MODULE_DESCRIPTION("256-byte educational character device driver");
MODULE_VERSION("1.0");
