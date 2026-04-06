CC  =   aarch64-poky-linux-gcc --sysroot=/opt/fsl-imx-wayland/4.14-sumo/sysroots/aarch64-poky-linux
TARGET = BH1750_test
EXTRA_LIBS += -lpthread
all:
	$(CC) -o $(TARGET) $(TARGET).c $(EXTRA_LIBS)  
	
clean:
	rm $(TARGET) 
