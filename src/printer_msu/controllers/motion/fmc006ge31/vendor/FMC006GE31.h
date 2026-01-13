#pragma once
#ifndef _FMC006GE31_DLL_H_
#define _FMC006GE31_DLL_H_

/*#ifdef FMC006GE31_DLL
#define FMC006GE31_API  __declspec(dllexport) __stdcall
#else
#define FMC006GE31_API  __declspec(dllimport) __stdcall
#endif
*/

#define FMC006GE31_API  __declspec(dllexport) __stdcall

/* 最大轴数6，此数请勿修改，会造成数据错乱导致程序崩溃 */
#define MAX_AXIS							6
#define EX_IO_MAX                           64
/* 设备状态@machine_status.machineRunStatus */
#define MACHINE_MANUAL                     0x0001     //设备处于手动模式
#define MACHINE_AUTO                       0x0002     //设备处于运行脚本模式
#define MACHINE_AUTO_PAUSE                 0x0004		//设备处于运行脚本暂停状态
#define MACHINE_AUTO_RESUME                0x0008	//设备处于运行脚本恢复状态

/* 各轴状态@machine_status.axisStatus */
#define MACHINE_POWER_ON                   0x0000     //无意义
#define MACHINE_RUNNING                    0x0001     //轴正在运行
#define MACHINE_PAUSE                      0x0002     //轴暂停运行
#define MACHINE_RESUME                     0x0004     //无
#define MACHINE_STOP                       0x0008     //轴停止运行
#define MACHINE_LIMIT_N                    0x0010     //负限位触发
#define MACHINE_LIMIT_P                    0x0020	  //正限位触发
#define MACHINE_HOME_DONE                  0x0040     //轴回零完成
#define MACHINE_HOME                       0x0080     //轴回零中
#define MACHINE_AUTO_RUN                   0x0100     //无
#define MACHINE_LIMIT_N_NONE               0x0200     //负限位未触发
#define MACHINE_LIMIT_P_NONE               0x0400     //正限位未触发
#define MACHINE_HOME_NONE                  0x0800     //未回零
#define MACHINE_HOME_OVERTIME              0x1000     //回零超时

#define MAX_FILE        20
#define MAX_FILE_NAME    30	

struct machine_status {
	float encoderRealPos[MAX_AXIS];					  //编码器计算实时位置
	float realPos[MAX_AXIS];                          //当前各轴实时位置
	float realSpeed[MAX_AXIS];                        //当前各轴实时速度
	unsigned int inputStatus;                         //输入状态
	unsigned int outputStatus;                        //输出状态
	unsigned int limitNStatus;                        //负限位状态
	unsigned int limitPStatus;                        //正限位状态
	unsigned int machineRunStatus;                    //设备运行状态
	unsigned int axisStatus[MAX_AXIS];                //轴状态
	unsigned int homeStatus;                          //回零状态，未使用，通过轴状态来获取回零状态	
	unsigned int  batStatus;						  //主备电状态，2代表主电，3备电
	unsigned int  emergencyStatus;					  //急停状态，1代表急停未触发，2代表急停触发
};
struct machine_auto_status
{
	char file[MAX_FILE][MAX_FILE_NAME];              //控制器内程序文件，至多20个文件
	char currentRunFile[MAX_FILE_NAME];                //当前正在运行的文件
	int currentRunLine;                               //当前运行程序所处的行号
};
struct machine_device_para {
	unsigned int id;
	unsigned int bound232;
	unsigned int bound485;
	unsigned int boundCAN;
	unsigned char ip[4];
	int port;

	int div[MAX_AXIS];
	int lead[MAX_AXIS];
	int softLimitMax[MAX_AXIS];
	int softLimitMin[MAX_AXIS];
	int homeTime[MAX_AXIS];
	int encoderResolution[MAX_AXIS]; //编码器的物理分辨率
};

struct machine_version {
	unsigned int firmware;
	unsigned int lib;
	unsigned int serialnumber;
};


struct external_io_para
{
	unsigned char  flag;
	unsigned char id[EX_IO_MAX];                 //从机物理地址
	unsigned char deviceType[EX_IO_MAX];         //设备类型
	unsigned char inputCnt[EX_IO_MAX];           //输入个数
	unsigned char outputCnt[EX_IO_MAX];          //输出个数
};
struct external_io_status
{
	unsigned char id[EX_IO_MAX]; //从机物理地址
	unsigned int inputStatus[EX_IO_MAX];  //输入状态
	unsigned int outputStatus[EX_IO_MAX]; //输出状态
};
struct emergency_io_para
{
	unsigned char  flag;
	unsigned char  IO;
	unsigned char  mode;
};
#ifdef __cplusplus
extern "C" {
#endif

	/* 设备打开与关闭相关函数 */
	int FMC006GE31_API FMC006GE31_Open_Device(int id, char* ip, int port, int mode); //mode = 0 TCP连接阻塞模式，= 1非阻塞
	int FMC006GE31_API FMC006GE31_Close_Device(int id);

	/* 单轴运动与轴状态检查相关函数 */
	int FMC006GE31_API FMC006GE31_Jog_Single_Axis(int id, int axis, float pos, float speed, float acc, float dec, int mode);
	int FMC006GE31_API FMC006GE31_Check_Axis_Is_Stop(int id, int axis);
	int FMC006GE31_API FMC006GE31_Home_Single_Axis(int id, int axis, float homeSpeed, float homeAccDec, float homeFallStep, int homeDir);
	int FMC006GE31_API FMC006GE31_Stop_Single_Axis(int id, int axis, int mode);
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Pos(int id, int axis, float* pos);
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Pos_encoder(int id, int axis, float* pos);
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Speed(int id, int axis, float* speed);

	/* IO操作函数 */
	int FMC006GE31_API FMC006GE31_Set_Output(int id, int io, int status);
	int FMC006GE31_API FMC006GE31_Get_Input(int id, int io, int* status);
	int FMC006GE31_API FMC006GE31_Set_EXIO_Output(int id, int addr, int io, int status);

	/* 多轴插补相关函数 */
	/** 功能：以当前点为起点的两轴直线插补运动
	  * id：卡号
	  * axis：插补的轴号，采用十六进制的低三位表示X、Y、Z轴，可选组合有（0x03、0x05、0x06）
	  * endX：终点的X坐标值
	  * endY：终点的Y坐标值
	  * speed：插补速度，此速度为两轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  */
	int FMC006GE31_API FMC006GE31_Line_2Axis(int id, unsigned int axis, float endX, float endY, float speed, float acc, float dec);

	/** 功能：以当前点为起点的三轴直线插补运动
	  * id：卡号
	  * endX：终点的X坐标值
	  * endY：终点的Y坐标值
	  * endZ：终点的Z坐标值
	  * speed：插补速度，此速度为三轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  */
	int FMC006GE31_API FMC006GE31_Line_3Axis(int id, unsigned int axis, float endX, float endY, float endZ, float speed, float acc, float dec);

	/** 功能：以当前点为起点的两轴圆弧插补运动
	  * id：卡号
	  * axis：插补的轴号，采用十六进制的低三位表示X、Y、Z轴，可选组合有（0x03、0x05、0x06）
	  * endX：终点的X坐标值
	  * endY：终点的Y坐标值
	  * centerX：圆心X坐标值
	  * centerY：圆心Y坐标值
	  * radius：圆半径
	  * speed：插补速度，此速度为两轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  */
	int FMC006GE31_API FMC006GE31_Arc_2Axis(int id, unsigned int axis, float endX, float endY, float centerX, float centerY, float radius, float speed, float acc, float dec, int dir);

	int FMC006GE31_API FMC006GE31_Pause_Run(int id, unsigned int axis);
	int FMC006GE31_API FMC006GE31_Resume_Run(int id, unsigned int axis);
	int FMC006GE31_API FMC006GE31_Stop_Run(int id);

	/* 设备状态查询相关函数 */
	/** 功能：查询设备状态信息
	  * id：卡号
	  * machineData：传入的字符数组指针，由machine_status定义的结构体组成，调用完成后，数组将会填充数据
	  */
	int FMC006GE31_API FMC006GE31_Get_Machine_Status(int id, unsigned char* machineData);
	int FMC006GE31_API FMC006GE31_Get_Machine_Atuo_Status(int id, unsigned char* machineAtuoData);
	int FMC006GE31_API FMC006GE31_Get_Exio_Status(int id, unsigned char* exioData);
	int FMC006GE31_API FMC006GE31_Get_Device_Para(int id, unsigned char* devicePara);
	int FMC006GE31_API FMC006GE31_Set_Device_Para(int id, unsigned char* devicePara);
	int FMC006GE31_API FMC006GE31_Get_Ex_IO_Para(int id, unsigned char* exioPara);
	int FMC006GE31_API FMC006GE31_Set_Ex_IO_Para(int id, unsigned char* exioPara);
	int FMC006GE31_API FMC006GE31_Get_Version_Info(int id, unsigned char* version);
	int FMC006GE31_API FMC006GE31_Get_Emergency_IO_Para(int id, unsigned char* emergencyPara);
	int FMC006GE31_API FMC006GE31_Set_Emergency_IO_Para(int id, unsigned char* emergencyPara);

	/* 文件操作相关函数 */
	/** 功能：下载控制卡升级文件或脚本程序文件，分别为.bin  .elo,此函数在下载文件较大时会阻塞较长时间
	  * id：卡号
	  * filePath：文件路径
	  * fileType：文件类型  1：控制卡升级程序文件  2：控制卡自动运行执行脚本文件
	  */
	int FMC006GE31_API FMC006GE31_Download_File(int id, char* filePath, int fileType);

	/* 自动控制相关函数 */
	/** 功能：启动控制卡内的程序进行自动运行
	  * id：卡号
	  * file：待启动的文件名，此文件名从控制卡内部获取
	  */
	int FMC006GE31_API FMC006GE31_Start_Auto_Run(int id, char* file);
	/** 功能：暂停控制卡内的程序进行自动运行
	  * id：卡号
	  * file：待启动的文件名，此文件名从控制卡内部获取
	  */
	int FMC006GE31_API FMC006GE31_Pause_Auto_Run(int id, char* file);

	/** 功能：用于急停触发或者主电断电，重新恢复执行当前的自动化程序，可以通过FMC006GE31_Get_Machine_Atuo_Status函数获以下需要的参数
	  * id：卡号
	  * currentRunLine: 待恢复的当前文件运行的行号，从控制卡内部获取
	  *  currentRunFile：待恢复的当前文件名，此文件名从控制卡内部获取
	  */
	int FMC006GE31_API FMC006GE31_Resume_Auto_Run(int id, int currentRunLine, char* currentRunFile);

	/** 功能：停止控制卡自动运行
	  * id：卡号
	  */
	int FMC006GE31_API FMC006GE31_Stop_Auto_Run(int id);

	/** 功能：删除控制器内程序文件
	  * id：卡号
	  * file：待删除的文件名，此文件名从控制卡内部获取
	  */
	int FMC006GE31_API FMC006GE31_Delete_Script_File(int id, char* file);


#ifdef __cplusplus
}
#endif 

#endif