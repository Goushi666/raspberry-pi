/*****************************
*模块名称：光照强度传感器测试程序
*    描述：采集光照强度数值
*    作者：meichenghao@outlook.com
*    时间：2017年4月21日
*    版本：UP-Magic-Version 1.0
******************************/
#include <fcntl.h>                                                       
#include <stdio.h>  
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>

/*设备节点名字*/
#define I2C_DEV		"/dev/i2c-1"
/*ioctl 命令*/
#define I2C_SLAVE       0x0703  /* Use this slave address */
#define I2C_TENBIT      0x0704	/* 0 for 7 bit addrs, != 0 for 10 bit */
/* 传感器设备地址*/
#define CHIP_ADDR	0x23
/**********************************************
	*函数名		：read_BH1750
	*描述		：读取传感器数据
	*输入参数	：
	*	@int fd			文件描述符
	*	@void *buff		缓存地址
	*	@size_t count		读取子节数
	*输出参数	：*buff
	*返回值		：出错:-1；	成功：返回读取的字节数			
	***********************************************/
static int read_BH1750(int fd, void *buff, int count)
{
	int res;	
	res=read(fd,buff,count);		//读取count个字节数据数据到buff缓存区
	return res;
}
/**********************************************
	*函数名		：write_BH1750
	*描述		：向模块写入数据
	*输入参数	：
	*	@int fd			文件描述符
	*	@unsigned char addr	写入地址
	*	@size_t count		写入子节数
	*返回值		：出错:-1；	成功：写入的字节数			
	***********************************************/
static int write_BH1750(int fd, unsigned char addr, size_t count)
{
	int res;
	char  sendbuffer[count+1];
	sendbuffer[0] = addr;
	res=write(fd,sendbuffer,count);
	return 0;
}
int main(void)
{
	int fd,res;
	float flux;
	unsigned char buf[2];
	fd = open(I2C_DEV, O_RDWR);			//打开i2c设备节点
	if(fd < 0){
		printf("####i2c test device open failed####\n");
		return (-1);
	}
	if(-1 == ioctl(fd,I2C_TENBIT,0)){		//设置地址位长度为 7bit
		printf("ioctl error on line %d\n",__LINE__);
		return (-1);
	}
	if(-1 == ioctl(fd,I2C_SLAVE,CHIP_ADDR)){	//设置从设备地址
		printf("ioctl error on line %d\n",__LINE__);
		return (-1);
	}
	res = write_BH1750(fd, 0x01, 1);		//发送指令0x01：等待测量指令
	if(res == -1){
		printf("write error on line %d\n", __LINE__);
	}
	res = write_BH1750(fd, 0x10, 1);		//发送指令0x10：连续H分辨率模式
	if(res == -1){
		printf("write error on line %d\n", __LINE__);
	}
	while(1){
		usleep(120*1000);			//延时120ms
		memset(buf,0,sizeof(buf));		//清零
		read_BH1750(fd,buf,2);			//读取光照强度数据
		flux = (float)(buf[0] << 8 | buf[1])/1.2;
		printf("BH1750: %6.2f lux\n", flux);	//打印输出	
		fflush(stdout);		
	}
	close(fd);
	return(0);
}

