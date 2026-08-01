#!/bin/bash
# run_history_smart.sh - 智能历史任务（自动让路给主任务）

cd /home/wenba/laiqiqi/call_to_text
source venv/bin/activate

BATCH_DAYS=${1:-10}              # 每批10天
TOTAL_DAYS=${2:-1000}            # 总共1000天
STATE_FILE="/tmp/history_progress.txt"  # 记录进度
HISTORY_LOG="logs/history_smart.log"

# 检查是否已有运行中的实例
if [ -f "$STATE_FILE" ]; then
    echo "$(date '+%F %T') 检测到已有历史任务运行中，退出" >> $HISTORY_LOG
    exit 0
fi

echo "$(date '+%F %T') ========== 智能历史任务启动 ==========" >> $HISTORY_LOG

# 初始化进度
if [ ! -f "$STATE_FILE" ]; then
    echo "$TOTAL_DAYS" > "$STATE_FILE"
fi

while true; do
    # 读取当前剩余天数
    CURRENT_DAYS=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
    
    if [ "$CURRENT_DAYS" -le 0 ]; then
        echo "$(date '+%F %T') ✅ 所有历史数据处理完成！" >> $HISTORY_LOG
        rm -f "$STATE_FILE"
        break
    fi
    
    # 计算本次批次
    BATCH=$((CURRENT_DAYS > BATCH_DAYS ? BATCH_DAYS : CURRENT_DAYS))
    
    echo "$(date '+%F %T') 📦 开始处理批次: $BATCH 天 (剩余 $CURRENT_DAYS 天)" >> $HISTORY_LOG
    
    # 尝试获取锁，只等60秒
    /usr/bin/flock -w 60 /tmp/call_to_text.lock \
        /usr/bin/env LOOKBACK_DAYS=$BATCH \
        ASR_MODEL=sensevoice \
        ASR_DEVICE=cuda:0 \
        /home/wenba/laiqiqi/call_to_text/run_daily.sh
    
    # 检查执行结果
    if [ $? -eq 0 ]; then
        # 成功完成一批，更新进度
        NEW_DAYS=$((CURRENT_DAYS - BATCH))
        echo "$NEW_DAYS" > "$STATE_FILE"
        echo "$(date '+%F %T') ✅ 批次完成，剩余 $NEW_DAYS 天" >> $HISTORY_LOG
    else
        # 如果获取锁失败（主任务正在运行），等待并继续
        echo "$(date '+%F %T') ⏳ 主任务正在运行，等待30分钟后重试..." >> $HISTORY_LOG
        sleep 1800  # 等待30分钟
        continue
    fi
    
    # 每批完成后休息一下，让系统喘口气
    sleep 10
    
    # 检查现在几点了，如果在主任务执行前1小时内，主动等待
    CURRENT_HOUR=$(date +%H)
    CURRENT_MIN=$(date +%M)
    if [ "$CURRENT_HOUR" = "23" ] || [ "$CURRENT_HOUR" = "00" ]; then
        # 接近凌晨0点，主动等待到00:30
        echo "$(date '+%F %T') 🌙 接近主任务执行时间，等待到00:30..." >> $HISTORY_LOG
        # 计算到00:30的秒数
        NOW_SEC=$(date +%s)
        TARGET=$(date -d "00:30:00" +%s 2>/dev/null || echo $(($(date +%s) + 1800)))
        if [ $NOW_SEC -lt $TARGET ]; then
            SLEEP_SEC=$((TARGET - NOW_SEC + 10))
            echo "$(date '+%F %T') 等待 $((SLEEP_SEC / 60)) 分钟" >> $HISTORY_LOG
            sleep $SLEEP_SEC
        fi
    fi
done

echo "$(date '+%F %T') ========== 智能历史任务完成 ==========" >> $HISTORY_LOG
