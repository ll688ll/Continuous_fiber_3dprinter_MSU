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
	float encoderRealPos[MAX_AXIS];					  //编码器计算实时位置，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	float realPos[MAX_AXIS];                          //当前各轴实时位置，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	float realSpeed[MAX_AXIS];                        //当前各轴实时速度，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	unsigned int inputStatus;                         //输入状态，按位表示bit7-bit0，对应IN7-IN0，对应位置1表示输入触发，置0未触发
	unsigned int outputStatus;                        //输出状态，按位表示bit7-bit0，对应OUT7-OUT0，对应位置1表示输入触发，置0未触发
	unsigned int limitNStatus;                        //负限位状态，按位标识bit5-bi0，对应Z1、Y1、X1、Z、Y、X轴，置1标识负限位触发，置0表示未触发
	unsigned int limitPStatus;                        //正限位状态，按位标识bit5-bi0，对应Z1、Y1、X1、Z、Y、X轴，置1标识正限位触发，置0表示未触发
	unsigned int machineRunStatus;                    //设备运行状态，如上/* 设备状态@machine_status.machineRunStatus */，对应位置1则有效
	unsigned int axisStatus[MAX_AXIS];                //轴状态，如上/* 各轴状态@machine_status.axisStatus */所示，对应位置1则有效
	unsigned int homeStatus;                          //回零状态，未使用，通过轴状态来获取回零状态	
	unsigned int  batStatus;						  //主备电状态，2代表主电，3备电
	unsigned int  emergencyStatus;					  //急停状态，1代表急停未触发，2代表急停触发
};
#pragma pack(push)
#pragma pack(1)	
struct machine_auto_status
{
	char file[MAX_FILE][MAX_FILE_NAME];              //控制器内程序文件，至多20个文件
	char currentRunFile[MAX_FILE_NAME];                //当前正在运行的文件
	int currentRunLine;                               //当前运行程序所处的行号
};
#pragma pack(pop)
struct machine_device_para {
	unsigned int id;     //设备ID
	unsigned int bound232;  //232波特率
	unsigned int bound485; //485波特率
	unsigned int boundCAN; //CAN的波特率
	unsigned char ip[4];  //IP地址
	int port;				//端口

	int div[MAX_AXIS];     //细分，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	int lead[MAX_AXIS];   //导程，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	int softLimitMax[MAX_AXIS]; //正软限位，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	int softLimitMin[MAX_AXIS];//负软限位，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	int homeTime[MAX_AXIS];  //回零超时时间，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
	int encoderResolution[MAX_AXIS]; //编码器的物理分辨率，0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5：Z1轴
};

struct machine_version {
	unsigned int firmware; //控制卡固件版本
	unsigned int lib;		//动态库版本
	unsigned int serialnumber;//控制卡串号
};


struct external_io_para
{
	unsigned char  flag;
	unsigned char id[EX_IO_MAX];                 //扩展IO从机物理地址
	unsigned char deviceType[EX_IO_MAX];         //设备类型
	unsigned char inputCnt[EX_IO_MAX];           //输入个数
	unsigned char outputCnt[EX_IO_MAX];          //输出个数
};
struct external_io_status
{
	unsigned char id[EX_IO_MAX]; //扩展IO从机物理地址
	unsigned int inputStatus[EX_IO_MAX];  //输入状态
	unsigned int outputStatus[EX_IO_MAX]; //输出状态
};
struct emergency_io_para   //急停IO的状态参数
{
	unsigned char  flag;
	unsigned char  IO;   //本地急停IO号
	unsigned char  mode; //急停模式：1：减速停止，2：立即停止
};
#ifdef __cplusplus
extern "C" {
#endif

	/* 设备打开与关闭相关函数 */
	/*函数名称：FMC006GE31_Open_Device
	  * 功能：连接控制器
	  * id：设备ID。此id号唯一不能重复
	  * ip：IP地址输入缓存。例如：192.168.0.30，可修改，若忘记可以通过复位按钮重置出厂设置
	  * port：端口
	  * mode：0：TCP连接阻塞模式，1：非阻塞
	  * 返回：0，成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Open_Device(int id, char* ip, int port, int mode);
	/* 设备打开与关闭相关函数 */
	/*函数名称：FMC006GE31_Close_Device
	  * 功能：断开控制器，在退出程序之前必须调用此函数进行资源释放，否则下次连接将会失败
	  * id：之前分配给控制器的id号，唯一且一一对应
	  * 返回：0，成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Close_Device(int id);

	/* 单轴运动与轴状态检查相关函数 */
	/*函数名称：FMC006GE31_Jog_Single_Axis
	  * 功能：单轴运动控制。可多次启动不同轴，启动同一轴时若前次运动未完成，则此次指令不响应
	  * id：之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * pos：目标距离
	  * speed：速度
	  * acc：加速度
	  * dec:减速度
	  * mode：运动模式。1：相对运动，2：绝对运动
	  * 返回：为0成功，其它失败。
	  */
	int FMC006GE31_API FMC006GE31_Jog_Single_Axis(int id, int axis, float pos, float speed, float acc, float dec, int mode);
	/*函数名称：FMC006GE31_Check_Axis_Is_Stop
	  * 功能：检查某轴是否为停止状态，用于判断某轴的运行状态。
	  * id。之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * 返回：1：标志轴为停止状态，0标志轴为运行状态
	  */
	int FMC006GE31_API FMC006GE31_Check_Axis_Is_Stop(int id, int axis);
	/*函数名称：FMC006GE31_Home_Single_Axis
	  * 功能：单轴回零运动。
	  * id：之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * homeSpeed：回零速度
	  * homeAccDec：回零加减速
	  * homeFallStep：回零脱落距离
	  * homeDir：回零方向，1：正限位作为原点限位回零，2：负限位作为原点限位回零，3：当前位置回零
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Home_Single_Axis(int id, int axis, float homeSpeed, float homeAccDec, float homeFallStep, int homeDir);
	/*函数名称：FMC006GE31_Stop_Single_Axis
	  * 功能：停止单轴运动。
	  * id：之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * mode：停止模式运动模式。1：减速停止：2：立即停止
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Stop_Single_Axis(int id, int axis, int mode);
	/** 函数名称：FMC006GE31_Get_Axis_Current_Pos
	  功能：获取指定轴的实时位置
	  * id：之前分配给控制器的id号。
	  * axis：轴号：0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5:Z1轴
	  * pos：位置数据缓存
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Pos(int id, int axis, float* pos);
	/** 函数名称：FMC006GE31_Get_Axis_Current_Pos_encoder
	  功能：获取指定轴的编码器计算的实时位置
	  * id：之前分配给控制器的id号。
	  * axis：轴号：0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5:Z1轴
	  * pos：位置数据缓存
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Pos_encoder(int id, int axis, float* pos);
	/** 函数名称：FMC006GE31_Get_Axis_Current_Speed
	  功能：获取指定轴的实时速度
	  * id：之前分配给控制器的id号。
	  * axis：轴号：0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1轴，5:Z1轴
	  * speed：速度数据缓存
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Get_Axis_Current_Speed(int id, int axis, float* speed);

	/* IO操作函数 */
	/** 函数名称：FMC006GE31_Set_Output
		功能：设置本地输出IO的输出
	  * id：之前分配给控制器的id号。
	  * io：本地IO输出号：范围0-7，对应本地O1-O8输出口
	  * status：1：输出断开。0：输出闭合
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Set_Output(int id, int io, int status);
	/** 函数名称：FMC006GE31_Get_Input
		功能：获取本地输入IO的状态
	  * id：之前分配给控制器的id号。
	  * io：本地IO输出号：范围0-7，对应本地I1-I8输出口
	  * status：1：输入断开。0：输入闭合
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Get_Input(int id, int io, int* status);
	/** 函数名称：FMC006GE31_Set_EXIO_Output
		功能：设置扩展IO输出
	  * id：之前分配给控制器的id号。
	  * addr:扩展ID的地址0-64.
	  * io：扩展IO输出号：范围0-31，对应扩展IO的OUT1-OUT32输出口
	  * status：1：输出断开。0：输出闭合
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Set_EXIO_Output(int id, int addr, int io, int status);

	/* 多轴插补相关函数 */
	/**  函数名称：FMC006GE31_Line_2Axis
	 功能：以当前点为起点的两轴直线插补运动
	  * id：之前分配给控制器的id号。
	  * axis：插补的轴号，采用十六进制的低六位表示X、Y、Z、X1、Y1、Z1轴，
			bit0-X轴，bit1-Y轴，bit2-Y轴，bit3-X1轴，bit4-Y1轴，bit5-Z1轴，
		   例如：组合 0x03对应的二进制为000011，就表示XY轴的组合
	  * endX：虚拟的终点的X坐标值，具体看组合
	  * endY：虚拟的终点的Y坐标值，具体看组合
	  * speed：插补速度，此速度为两轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Line_2Axis(int id, unsigned int axis, float endX, float endY, float speed, float acc, float dec);

	/** 函数名称：FMC006GE31_Line_3Axis
		功能：以当前点为起点的三轴直线插补运动
	  * id：之前分配给控制器的id号。
	  * axis：插补的轴号，采用十六进制的低六位表示X、Y、Z、X1、Y1、Z1轴，
			bit0-X轴，bit1-Y轴，bit2-Y轴，bit3-X1轴，bit4-Y1轴，bit5-Z1轴，
		   例如：组合 0x07对应的二进制为000111，就表示XYZ轴的组合
	  * endX：虚拟的终点的X坐标值，具体看组合
	  * endY：虚拟的终点的Y坐标值，具体看组合
	  * endZ：虚拟的终点的Z坐标值，具体看组合
	  * speed：插补速度，此速度为三轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Line_3Axis(int id, unsigned int axis, float endX, float endY, float endZ, float speed, float acc, float dec);


	/** 函数名称：FMC006GE31_Arc_2Axis
	 功能：以当前点为起点的两轴圆弧插补运动
	  * id：之前分配给控制器的id号。
	  * axis：插补的轴号，采用十六进制的低六位表示X、Y、Z、X1、Y1、Z1轴，
			bit0-X轴，bit1-Y轴，bit2-Y轴，bit3-X1轴，bit4-Y1轴，bit5-Z1轴，
		   例如：组合 0x03对应的二进制为000011，就表示XY轴的组合
	  * endX：终点的X坐标值
	  * endY：终点的Y坐标值
	  * centerX：圆心X坐标值
	  * centerY：圆心Y坐标值
	  * radius：圆半径
	  * speed：插补速度，此速度为两轴合成速度，并非各独立轴实际运行速度
	  * acc：插补加速度，此加速度同样为合成加速度
	  * dcc：插补减速度，此减速度同样为合成减速度
	  * dir：1正圆，2逆圆
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Arc_2Axis(int id, unsigned int axis, float endX, float endY, float centerX, float centerY, float radius, float speed, float acc, float dec, int dir);
	/*函数名称：FMC006GE31_Pause_Run
	  * 功能：暂停单轴运动。
	  * id：之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * mode：停止模式运动模式。1：减速停止：2：立即停止
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Pause_Run(int id, unsigned int axis);
	/*函数名称：FMC006GE31_Resume_Run
	  * 功能：恢复。
	  * id：之前分配给控制器的id号。
	  * axis：需要运动的轴，范围0-5。0：X轴，1：Y轴，2：Z轴，3：X1轴，4：Y1	轴，5:Z1轴
	  * mode：停止模式运动模式。1：减速停止：2：立即停止
	  * 返回：为0成功，其它失败
	  */
	int FMC006GE31_API FMC006GE31_Resume_Run(int id, unsigned int axis);
	/*函数名称：FMC006GE31_Stop_Run
	  * 功能：未实现。
	  */
	int FMC006GE31_API FMC006GE31_Stop_Run(int id);

	/* 设备状态查询相关函数 */
	/*函数名称：FMC006GE31_Get_Machine_Status
	* 功能：查询设备状态信息。
	* id：之前分配给控制器的id号。
	* machineData：传入的字符数组指针，由machine_status定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Machine_Status(int id, unsigned char* machineData);
	/*函数名称：FMC006GE31_Get_Machine_Atuo_Status
	* 功能：查询自动运行状态信息。
	* id：之前分配给控制器的id号。
	* machineAtuoData：传入的字符数组指针，由machine_auto_status定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Machine_Atuo_Status(int id, unsigned char* machineAtuoData);
	/*函数名称：FMC006GE31_Get_Exio_Status
	* 功能：查询扩展IO状态信息。
	* id：之前分配给控制器的id号。
	* exioData：传入的字符数组指针，由external_io_status定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Exio_Status(int id, unsigned char* exioData);
	/*函数名称：FMC006GE31_Get_Device_Para
	* 功能：查询设备参数信息。
	* id：之前分配给控制器的id号。
	* devicePara：传入的字符数组指针，由machine_device_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Device_Para(int id, unsigned char* devicePara);
	/*函数名称：FMC006GE31_Set_Device_Para
	* 功能：设置设备参数信息。
	* id：之前分配给控制器的id号。
	* devicePara：传入的字符数组指针，由machine_device_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Set_Device_Para(int id, unsigned char* devicePara);
	/*函数名称：FMC006GE31_Get_Ex_IO_Para
	* 功能：获取扩展IO参数信息。
	* id：之前分配给控制器的id号。
	* exioPara：传入的字符数组指针，由external_io_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Ex_IO_Para(int id, unsigned char* exioPara);
	/*函数名称：FMC006GE31_Set_Ex_IO_Para
	* 功能：设置扩展IO参数信息。
	* id：之前分配给控制器的id号。
	* exioPara：传入的字符数组指针，由external_io_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Set_Ex_IO_Para(int id, unsigned char* exioPara);
	/*函数名称：FMC006GE31_Get_Version_Info
	* 功能：获取版本号。
	* id：之前分配给控制器的id号。
	* version：传入的字符数组指针，由machine_version定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Version_Info(int id, unsigned char* version);
	/*函数名称：FMC006GE31_Get_Emergency_IO_Para
	* 功能：获取急停IO的参数。
	* id：之前分配给控制器的id号。
	* emergencyPara：传入的字符数组指针，由emergency_io_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
	int FMC006GE31_API FMC006GE31_Get_Emergency_IO_Para(int id, unsigned char* emergencyPara);
	/*函数名称：FMC006GE31_Set_Emergency_IO_Para
	* 功能：设置急停IO的参数。
	* id：之前分配给控制器的id号。
	* emergencyPara：传入的字符数组指针，由emergency_io_para定义的结构体组成，调用完成后，数组将会填充数据
	* 返回：为0成功，其它失败
	*/
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