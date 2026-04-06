obj-m	:=BH1750_driver.o

KERNELDIR := /home/uptech/fsl-8mm-source/kernel-4.14.98/
PWD := $(shell pwd)

all:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) clean
