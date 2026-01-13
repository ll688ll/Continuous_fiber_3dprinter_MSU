# -*- coding: UTF-8 -*-
"""
Basic motion demo for FMC006GE31-type controller.

Notes:
- This demo is **Windows-only** (it loads the vendor .dll via ctypes).
- The vendor files live under:
  `src/printer_msu/controllers/motion/fmc006ge31/vendor/`
"""

from __future__ import annotations

import sys
import time
from ctypes import Structure, c_char_p, c_float, c_int32, byref
from pathlib import Path

import ctypes

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

def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This example requires Windows (ctypes.windll).")

    dll_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "printer_msu"
        / "controllers"
        / "motion"
        / "fmc006ge31"
        / "vendor"
        / "FMC006GE31.dll"
    )

    if not dll_path.exists():
        raise SystemExit(f"Vendor DLL not found: {dll_path}")

    FMCG6A = ctypes.windll.LoadLibrary(str(dll_path))

    # 给控制器编号，此ID号唯一
    iD = 1
    axis = 1
    ip = "192.168.0.30"
    port = 8088

    # 连接控制器
    print(FMCG6A.FMC006GE31_Open_Device(iD, c_char_p(ip.encode("utf-8")), port, 0))

    # 控制器单轴运动
    print(FMCG6A.FMC006GE31_Jog_Single_Axis(iD, axis, c_float(200), c_float(100), c_float(200), c_float(200), 1))

    # 延时等待，等待控制卡实际启动
    time.sleep(1)

    # 等待轴运行完成，过程中不断获取轴实际位置并输出
    while FMCG6A.FMC006GE31_Check_Axis_Is_Stop(iD, axis) == 0:
        FMCG6A.FMC006GE31_Get_Machine_Status(iD, byref(ms))
        print(ms.realPos[axis])

    # 关闭控制器连接，使用完成一定调用此函数释放资源
    print(FMCG6A.FMC006GE31_Close_Device(iD))


if __name__ == "__main__":
    main()

