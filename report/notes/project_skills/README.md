# 项目经验记录（原技能包中只适用于本系统的部分）

下面 6 项按技能格式整理，但只对 AD9361 / SDR 或本作品的系统有用，不属于通用技能，因此作为开发记录放在这里。
触摸与离屏截图两项的通用部分见 `skill/pynq-local-display-gui/`。

| 记录 | 领域 | 内容 | 附带文件 |
|---|---|---|---|
| [`uvc-camera-controls`](uvc-camera-controls/SKILL.md) | PYNQ / Linux | 无 v4l-utils 时用 V4L2 ioctl 列出、在线修改、固化 UVC 摄像头参数 | `v4l2ctl.py` |
| [`touch-without-multitouch`](touch-without-multitouch/SKILL.md) | PYNQ + Qt | 内核缺 hid-multitouch 时，直接读 evdev 实现长按与拖动 | `evdev_touch.py` |
| [`qt-offscreen-layout-check`](qt-offscreen-layout-check/SKILL.md) | Qt GUI | 在板上离屏渲染 Qt GUI，逐页截图、量文字宽度，再请人上屏确认；无显示器时压测 | `offscreen_shots.py` |
| [`sdr-spur-hunting`](sdr-spur-hunting/SKILL.md) | SDR | 带内杂散排查：增益缩放、频率换算、开关源、挪频，以显示像素时钟谐波为例 | — |
| [`ad9361-ps-control-agc`](ad9361-ps-control-agc/SKILL.md) | AD9361 | 初始化从 PL 查找表迁到 PS；快速 AGC 的配置、帧中台阶排查（“或”逻辑）、不依赖绝对门限的卡住看门狗 | 引用 `src/sw/` |
| [`pl-counter-latency`](pl-counter-latency/SKILL.md) | Zynq | 轮询 PL 计数器 + 跨板时钟偏差 + 因果窗口配对，测分段时延 CDF，排除 PS 抖动 | 引用 `board/tests/` |

---

## 踩坑清单（汇总）

按领域列出开发中实际踩过的坑，括号内为出处（`report/notes/`、`report/llm_collab/cases.md` 或对应技能）。

### Vivado

1. 改了 IP 配置（如系数文件）后，`generate_target` + `reset_run synth_1` **不会重跑 IP 的 OOC 综合**，比特流里仍是旧网表，而行为仿真用的是新模型；
   曾因此出现“仿真通过、板上误帧 70 %”。改 IP 后要 `reset_run <ip>_synth_1` 并检查 dcp 时间（ofdm_jscc_integration.md §9）。
2. Vivado batch 会话不会重新解析外部修改过的源文件；要 `remove_files` + `add_files` + `update_compile_order`，否则新加的实例可能被标成 `IS_AUTO_DISABLED`。
3. ROM 推断深度由函数 / case 的**输入位宽**决定，只在调用处截断地址无效，要把函数输入本身改窄（13 位输入 → 8192 深）。
4. `$readmemh` 的文件加进 Vivado 工程后，RTL 里直接写**文件名**，不要写绝对或相对路径。
5. URAM 没有初始化内容，不能当 ROM；只读数据放 BRAM / LUT，或上电后写入。
6. 跨时钟域 FIFO 直接用 XPM 原语（`xpm_fifo_axis`），不要自己写（cases.md 案例 10）。
7. 同时有 VIO 时 ILA 上传报 "corrupted"：debug hub 挂在 20 MHz 时钟上，JTAG 频率要 ≤ 6 MHz；`-trigger_now` 会忽略存储条件。
8. 硬件管理器连接时生成的内存工程会让 `launch_runs` 失败：编译前先 `close_project` 再 `open_project`。

### AD9361 / 射频

9. SPI 状态机不等请求就锁存命令 → 应答错位一拍，所有“等待校准完成”立即通过；先确认配置**真的执行了**再调参数（ad9361_debug_log.md 问题 2）。
10. Evaluation Software 脚本的 TX 正交校准用固定 NCO 相位，可能落在收敛窗口外；收敛窗口随 FIR 群时延、频点变化，要在 PL / PS 里自动扫描。
11. 2 倍插值时 TX 可编程 FIR 的抽头数须为 32 的倍数（48 抽头发出乱码）；芯片 FIR 的冲激响应必须落在 CP 以内。
12. OFDM 的数字回退至少留 12 dB（PAPR 10–12 dB），否则 IFFT 输出被削顶。
13. 通过 VIO 读寄存器的第一次可能拿到旧值：先做一次无关的读。
14. 快速 AGC 状态在寄存器 `0x2B3[2:0]`（快速 AGC 状态机），不是 `0x0A7`（TX 正交校准状态）；多个解锁条件是“或”的关系，要组合测试（ad9361-ps-control-agc）。
15. 显示像素时钟的谐波可能正好落在射频带内（51.2 MHz × 18 = 921.6 MHz），换显示模式即可（sdr-spur-hunting）。

### PYNQ / 板上软件

16. AXI DMA 接收挂起后 `stop()` 撤不掉，要写 DMACR（DMA 控制寄存器）的软复位位；进程退出时复位所有接收 DMA（pynq-dma-stale-transfer）。
17. ssh 在后台启动板上进程时要把 stdin 重定向到 `/dev/null`，否则 ssh 会等到进程结束才返回。
18. `pkill -f xxx` 可能匹配到执行它的 shell 本身；用锚定模式 `pkill -f "^python3 xxx.py"`。
19. PYNQ 内核没有 hid-multitouch，多点触控屏只有点击（touch-without-multitouch）；触摸控制器反复掉线多为 USB 供电 / 线材问题。
20. TX overlay 加载后偶尔 AD9361 初始化不完成（射频就绪信号一直为 0，约每几次加载一次）：用 PRBS 检查 DAC 是否在取数据，不正常就重新加载（根因未查，cases.md 案例 12）。

### 测量

21. AGC 跟随信号**变强**快、变弱慢：衰减扫描从大往小扫，发射端增大衰减用斜坡。
22. SSCC 无法解码时 GUI 若冻结显示旧帧，PSNR 会被高估；无法解码统一按中灰图计分。
23. 静止图像下每帧符号相同，可用“最强点的平均判决”作参考星座算数据辅助 SNR；要剔除判决不稳定的位置和内容不同的帧。
24. 32 位字节计数器会回绕，不要用“值 ÷ 帧长”判断帧号（pl-counter-latency）。
25. 不要把自己临时定的经验阈值当规格机械执行（例如恢复时间 2.5 s）；判断前先问“这个阈值是谁定的、依据是什么”（cases.md 案例 14）。
