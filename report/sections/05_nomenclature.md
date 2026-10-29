## 术语与缩略语

| 缩略语 | 英文全称 | 说明 |
|---|---|---|
| ADC | Analog-to-Digital Converter | 模数转换器 |
| AGC | Automatic Gain Control | 自动增益控制 |
| APP | A Posteriori Probability | 后验概率；LDPC译码中指变量的后验对数似然比 |
| AWGN | Additive White Gaussian Noise | 加性高斯白噪声 |
| AXI | Advanced eXtensible Interface | ARM AMBA总线协议；AXI4-Lite用于寄存器读写，AXI-Stream是带valid / ready握手的无地址数据流 |
| BRAM | Block RAM | PL中的块存储器；BRAM36容量为36 Kb，可拆成两块BRAM18 |
| CDC | Clock Domain Crossing | 跨时钟域传输 |
| CFO | Carrier Frequency Offset | 载波频偏，两块板本振频率之差 |
| CNU | Check Node Unit | LDPC译码器中的校验节点处理单元 |
| CORDIC | COordinate Rotation DIgital Computer | 只用移位和加法迭代计算三角函数、幅度与相角的算法 |
| CP | Cyclic Prefix | 循环前缀，OFDM符号前复制的符号尾部采样 |
| CPE | Common Phase Error | 公共相位误差，一个OFDM符号内所有子载波共有的相位旋转 |
| CRC | Cyclic Redundancy Check | 循环冗余校验 |
| DAC | Digital-to-Analog Converter | 数模转换器 |
| DDR | Double Data Rate SDRAM | 板上的外部内存 |
| DDS | Direct Digital Synthesizer | 直接数字频率合成器 |
| DeepJSCC | Deep Joint Source-Channel Coding | 深度联合信源信道编码，用神经网络把图像直接映射为信道符号 |
| DeepJSCC-Q | Constellation-Constrained DeepJSCC | 信道符号限定在QAM星座点上的DeepJSCC[2] |
| DMA | Direct Memory Access | 直接存储器访问；MM2S为内存到数据流，S2MM为数据流到内存 |
| DSP | DSP48E2 slice | PL中的乘加单元，含一个27×18位乘法器 |
| EGL / GBM | Embedded-System Graphics Library / Generic Buffer Management | Linux下OpenGL ES与显示驱动之间的接口库 |
| ENSM | Enable State Machine | AD9361的收发状态机 |
| EVM | Error Vector Magnitude | 误差矢量幅度：接收星座点与理想星座点之差的均方根，相对信号功率以dB表示 |
| FDD | Frequency Division Duplex | 频分双工，收发通道同时工作 |
| FFT / IFFT | (Inverse) Fast Fourier Transform | （逆）快速傅里叶变换 |
| FIFO | First In, First Out | 先进先出缓冲 |
| FIR | Finite Impulse Response | 有限冲激响应滤波器 |
| FMC | FPGA Mezzanine Card | FPGA子卡连接器标准 |
| fps | frames per second | 每秒帧数 |
| GDN / IGDN | Generalized Divisive Normalization / Inverse GDN | 广义除法归一化[4]及其逆运算 |
| GLES | OpenGL for Embedded Systems | 嵌入式OpenGL图形接口 |
| GMAC | Giga Multiply-ACcumulate | 10⁹次乘累加 |
| GUI | Graphical User Interface | 图形用户界面 |
| I / Q | In-phase / Quadrature | 复基带信号的同相（实部）与正交（虚部）分量 |
| KL | Kullback–Leibler divergence | KL散度，衡量两个概率分布的差异 |
| LDPC | Low-Density Parity-Check | 低密度奇偶校验码 |
| LFSR | Linear Feedback Shift Register | 线性反馈移位寄存器，用于产生伪随机序列 |
| LLR | Log-Likelihood Ratio | 对数似然比，比特为0与为1的概率之比的对数 |
| LO | Local Oscillator | 本振 |
| LTF | Long Training Field | 长训练字段：前导的后半部分，用于精定时与信道估计 |
| LUT | Look-Up Table | PL中的查找表逻辑单元；LUTRAM为由LUT构成的分布式RAM |
| LVDS | Low-Voltage Differential Signaling | 低压差分信号 |
| MAC | Multiply-ACcumulate | 乘累加 |
| MJPEG | Motion JPEG | 逐帧JPEG编码的视频流 |
| MMCM | Mixed-Mode Clock Manager | PL中的时钟管理单元 |
| MMIO | Memory-Mapped I/O | 存储器映射的寄存器访问 |
| MSE | Mean Squared Error | 均方误差 |
| NCO | Numerically Controlled Oscillator | 数控振荡器 |
| NHWC | — | 张量的存放顺序：批次、高、宽、通道，同一像素的各通道连续存放 |
| OFDM | Orthogonal Frequency Division Multiplexing | 正交频分复用 |
| OOC | Out-Of-Context synthesis | 把一个模块脱离顶层单独综合，用于统计该模块的资源 |
| PAPR | Peak-to-Average Power Ratio | 峰均功率比 |
| PHY | Physical layer | 物理层 |
| PL | Programmable Logic | Zynq器件中的可编程逻辑（FPGA部分） |
| PN | Pseudo-Noise | 伪随机（序列） |
| PRBS | Pseudo-Random Binary Sequence | 伪随机二进制序列，用作测试数据源 |
| PS | Processing System | Zynq器件中的处理器系统（ARM处理器、内存控制器与外设） |
| PSNR | Peak Signal-to-Noise Ratio | 峰值信噪比，8位图像为10·lg(255²/MSE) |
| PWL | Piecewise Linear | 分段线性；本作品中指逐通道可学习的分段线性激活函数 |
| Q10 | — | 定点格式，小数部分10位，1.0表示为1024 |
| QAM | Quadrature Amplitude Modulation | 正交幅度调制；64QAM每个符号携带6比特 |
| RTL | Register Transfer Level | 寄存器传输级，指用硬件描述语言编写的电路 |
| SFO | Sampling Frequency Offset | 采样频偏，两块板采样时钟频率之差 |
| SNR | Signal-to-Noise Ratio | 信噪比 |
| SPI | Serial Peripheral Interface | 串行外设接口 |
| SSCC | Separate Source-Channel Coding | 分离信源信道编码，本作品中为JPEG + LDPC |
| SSIM | Structural Similarity | 结构相似性 |
| STF | Short Training Field | 短训练字段：前导的前半部分，用于帧检测与载波频偏估计 |
| TX / RX | Transmitter / Receiver | 发射端 / 接收端 |
| UDP | User Datagram Protocol | 用户数据报协议 |
| URAM | UltraRAM | PL中的大容量存储块，每块288 Kb |
| UVC | USB Video Class | USB摄像头的标准设备类 |
| W8A12 | — | 权重8位、激活12位的整数量化 |
| WNS / WHS | Worst Negative Slack / Worst Hold Slack | 建立时间 / 保持时间的最差时序裕量，非负表示时序收敛 |
| XPM | Xilinx Parameterized Macros | Vivado提供的参数化原语库（如异步FIFO） |
