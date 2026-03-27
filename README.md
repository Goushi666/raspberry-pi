# 硬件端（树莓派）

结构与说明见 `doc/硬件端设计文档.md`。

## 环境

```bash
cd /home/dayang/raspberry-pi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

DHT22 需接 **BCM GPIO4**（与 `config/pins.yaml` 一致）。光照当前为模拟数据，可在 `config/config.yaml` 将 `sensors.bh1750.enabled` 设为 `true` 接入 BH1750。

## 运行（MQTT 上报）

```bash
cd /home/dayang/raspberry-pi
source .venv/bin/activate
python3 src/main.py
```

默认每秒发布最新采样到主题 `sensor/data`（JSON）。MQTT 账号建议用环境变量覆盖密码：

```bash
export MQTT_PASSWORD='你的密码'
python3 src/main.py
```

## 配置

- `config/config.yaml`：MQTT、采样周期、功能开关  
- `config/pins.yaml`：GPIO 引脚  
