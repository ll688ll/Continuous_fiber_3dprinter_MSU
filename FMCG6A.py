# -*- coding: UTF-8 -*-
"""
Deprecated entry-point kept for backward compatibility.

The original FMC006GE31 demo script has moved to:
- examples/fmc006ge31_basic_motion.py
"""

from __future__ import annotations

from pathlib import Path
import runpy


def main() -> None:
    target = Path(__file__).resolve().parent / "examples" / "fmc006ge31_basic_motion.py"
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()

# -*- coding: UTF-8 -*-
from ctypes import *
import time

dll_path = 'FMC006GE31.dll'
FMCG6A = windll.LoadLibrary(dll_path)

# 定义设备状态类，用于获取设备状态数据
# struct machine_status {
# 	float encoderRealPos[MAX_AXIS];					  //编码器计算实时位置
# 	float realPos[MAX_AXIS];                          //当前各轴实时位置
# 	float realSpeed[MAX_AXIS];                        //当前各轴实时速度
# 	unsigned int inputStatus;                         //输入状态
# 	unsigned int outputStatus;                        //输出状态
# 	unsigned int limitNStatus;                        //负限位状态
# 	unsigned int limitPStatus;                        //正限位状态
# 	unsigned int machineRunStatus;                    //设备运行状态
# 	unsigned int axisStatus[MAX_AXIS];                //轴状态
# 	unsigned int homeStatus;                          //回零状态，未使用，通过轴状态来获取回零状态
# 	unsigned int  batStatus;						  //主备电状态，2代表主电，3备电
# 	unsigned int  emergencyStatus;					  //急停状态，1代表急停未触发，2代表急停触发
# };

class machine_status(Structure):
    _fields_ = [
        ("encoderRealPos", c_float * 6),
        ("realPos", c_float * 6),
        ("realSpeed", c_float * 6),
        ("inputStatus", c_int32 * 1),
        ("outputStatus", c_int32 * 1),
        ("limitNStatus", c_int32 * 1),
        ("limitPStatus", c_int32 * 1),
        ("machineRunStatus", c_int32 * 1),
        ("axisStatus", c_int32 * 6),
        ("homeStatus", c_int32 * 1),
        ("batStatus", c_int32 * 1),
        ("emergencyStatus", c_int32 * 1)
    ]

ms = machine_status()

# 给控制器编号，此ID号唯一
iD = 1
axis = 1
ip = '192.168.0.30'
port = 8088

# 连接控制器
print(FMCG6A.FMC006GE31_Open_Device(iD, c_char_p(bytes(ip, 'utf-8')), port, 0))

# 控制器单轴运动
print(FMCG6A.FMC006GE31_Jog_Single_Axis(1, 1, c_float(200), c_float(100), c_float(200), c_float(200), 1))

# 延时等待，等待控制卡实际启动
time.sleep(1)

# 等待轴运行完成，过程中不断获取轴实际位置并输出
while FMCG6A.FMC006GE31_Check_Axis_Is_Stop(iD, axis) == 0:
    FMCG6A.FMC006GE31_Get_Machine_Status(iD, pointer(ms))
    print(ms.realPos[axis])

# 关闭控制器连接，使用完成一定调用此函数释放资源
print(FMCG6A.FMC006GE31_Close_Device(iD))

