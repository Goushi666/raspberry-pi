/*****************************
*模块名称：光照强度传感器驱动程序
*    描述：设置模块DVI引脚为高点平
*    作者：meichenghao@outlook.com
*    时间：2017年4月21日
*    版本：UP-Magic-Version 1.0
******************************/
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/device.h>
#include <linux/of_gpio.h>
#include <linux/platform_device.h>
static unsigned int pin_table [1];

/*驱动程序入口初始化函数*/
static int p8_probe_func(struct platform_device *pdev)
{
	/*获得GPIO引脚*/
    struct device *dev = &pdev->dev;
    struct device_node *of_node = dev->of_node;
    if(!of_node) {
        return -ENODEV;
    }
    pin_table[0] = of_get_named_gpio(of_node, "p8_gpio0", 0);

	gpio_request(pin_table[0], "dvi_gpio");
	gpio_direction_output(pin_table[0], 1);
	gpio_set_value(pin_table[0], 1);
	printk("BH1750 Drver init\n");
	return 0;
}
/*驱动卸载函数*/
static int p8_remove_func(struct platform_device *pdev)
{
	gpio_free(pin_table[0]);
	printk("\nBH1750 Drver exit\n");
	return 0;
}

/*P8端口的设备信息描述*/
static struct of_device_id p8_of_match[] = {
        { .compatible = "fsl,p8-ports"},
        { },
};

/*struct platform_driver结构体赋值*/
static struct platform_driver p8_device_driver = {
    .probe        = p8_probe_func,			//驱动加载函数
    .remove       = p8_remove_func,			//驱动卸载函数
/*struct device_driver结构体赋值*/
    .driver       = {
		.name    = "bh1750",				//设备驱动程序的名称
        .owner   = THIS_MODULE, 			//设备驱动程序所有者
        .of_match_table = of_match_ptr(p8_of_match),	//驱动程序匹配的设备信息
    }
};

module_platform_driver(p8_device_driver);		//驱动程序入口与出口 
/*驱动属性：遵循协议、作者、驱动描述*/
MODULE_LICENSE("GPL");
MODULE_AUTHOR("uptech");
MODULE_DESCRIPTION("UP P8-PORT Driver");
