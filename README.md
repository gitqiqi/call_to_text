# call_to_text

微信语音/通话录音转写脚本，支持左右声道拆分后分别识别，再按时间合并写入数据库。

## ASR 模型

通过 `.env` 或运行环境设置 `ASR_MODEL`：

```bash
ASR_MODEL=whisper
ASR_MODEL=paraformer
ASR_MODEL=sensevoice
```

`ASR_MODEL=sensevoice` 会使用 `iic/SenseVoiceSmall`。默认 `ASR_SEGMENT_MODE=fast`，直接用 FunASR 的 VAD/时间戳输出重组成句段，吞吐会比 Whisper 逐句切段高很多；如果需要更细的时间戳，可以切回 `ASR_SEGMENT_MODE=whisper`，但速度会明显下降。

## 常用配置

```bash
# Whisper 分支使用，默认 base
WHISPER_MODEL=base

# SenseVoiceSmall 语言，中文建议 zh
SENSEVOICE_LANGUAGE=zh

# fast：高吞吐，用 FunASR 时间戳重组句段；whisper：更细粒度，速度慢
ASR_SEGMENT_MODE=fast

# fast 模式默认保留句段时间戳，方便左右声道按时间合并
ASR_OUTPUT_TIMESTAMP=1
ASR_MAX_SEGMENT_CHARS=80
ASR_MAX_SEGMENT_SECONDS=20

# 可选：指定设备，例如 cpu、cuda:0、mps
ASR_DEVICE=cpu

# 批量写库，减少 commit 次数
INSERT_BATCH_SIZE=100

# 处理完成后删除下载的 mp3/wav，避免 MP3 目录越积越大
KEEP_AUDIO=0

# 单次最多处理多少条，0 表示不限
RUN_LIMIT=0

# 每次扫描最近几个完整自然日的未处理数据，避免某天任务没跑完导致漏补
LOOKBACK_DAYS=3

# 运行日志。默认不输出每条记录/进度；需要观察时再打开
LOG_RECORDS=0
LOG_STAGES=1
PROGRESS_EVERY=0

# Paraformer 是否加载标点模型。设为 0 可更快启动，但输出标点会差一些
PARAFORMER_PUNC=1

# 分片并行。单进程保持 1/0；多进程时 SHARD_INDEX 从 0 到 SHARD_COUNT-1
SHARD_COUNT=1
SHARD_INDEX=0

# 热词。Paraformer 会使用 hotword；SenseVoiceSmall 会透传 hotwords，
# 但 SenseVoiceSmall 当前实现不一定做显式热词偏置。
ASR_HOTWORDS="退费 退款 退课 暑假集训 奥数 数学思维 小课 答题卡 正确率 六道题 三道题 做对 订正 讲解视频 坚持 畏难 急于求成 线上课 休息时间 八月份 秋季课 四年级 小升初"
```

## 运行

```bash
./run.sh
```

定时/批处理脚本：

```bash
./run_daily.sh
```

`run_daily.sh` 会自动进入脚本所在目录，优先使用 `venv/bin/python`，其次使用 `.venv/bin/python`，并把日志写到 `logs/call_to_text_YYYY-MM-DD.log`。本地或服务器差异建议放到 `.env` 或运行环境里；如果要写完全私有的脚本，可以用 `run_daily.local.sh`，该文件不会提交到 git。

## 大批量处理

每天接近 9 万条时，建议使用 `sensevoice + fast`，并按机器资源开分片并行。例如开 4 个进程：

```bash
SHARD_COUNT=4 SHARD_INDEX=0 ./run_daily.sh &
SHARD_COUNT=4 SHARD_INDEX=1 ./run_daily.sh &
SHARD_COUNT=4 SHARD_INDEX=2 ./run_daily.sh &
SHARD_COUNT=4 SHARD_INDEX=3 ./run_daily.sh &
wait
```

如果是 CPU 跑，先从 2 个分片开始；如果是 GPU 跑，通常单进程或少量分片更稳，避免显存被多进程抢满。

## 数据库字段

脚本会写入每条音频的转写耗时，单位毫秒：

```sql
ALTER TABLE bi.call_to_text
ADD COLUMN IF NOT EXISTS transcribe_duration_ms bigint;
```
