#!/bin/bash
# run_history_daemon.sh - 守护进程：主任务完成后自动恢复历史任务

cd /home/wenba/laiqiqi/call_to_text
HISTORY_LOG="logs/history_daemon.log"

echo "$(date '+%F %T') 守护进程启动" >> $HISTORY_LOG

# 检查是否已有守护进程在运行
if pgrep -f "run_history_daemon.sh" | grep -v $$ > /dev/null; then
    echo "$(date '+%F %T') 已有守护进程在运行，退出" >> $HISTORY_LOG
    exit 0
fi

while true; do
    # 检查历史任务是否在运行
    if ! pgrep -f "run_history_smart.sh" > /dev/null; then
        # 检查是否有剩余数据需要处理
        if [ -f "/tmp/history_progress.txt" ]; then
            REMAIN=$(cat /tmp/history_progress.txt 2>/dev/null || echo "0")
            if [ "$REMAIN" -gt "0" ]; then
                # 检查锁是否空闲
                if flock -n /tmp/call_to_text.lock -c "echo 锁已获取" 2>/dev/null; then
                    echo "$(date '+%F %T') 主任务已完成，恢复历史任务 (剩余 $REMAIN 天)" >> $HISTORY_LOG
                    nohup ./run_history_smart.sh >> logs/history_smart.log 2>&1 &
                else
                    echo "$(date '+%F %T') 主任务正在运行，等待..." >> $HISTORY_LOG
                fi
            else
                # 没有剩余数据，退出守护进程
                echo "$(date '+%F %T') 所有历史数据已处理完成，守护进程退出" >> $HISTORY_LOG
                rm -f /tmp/history_progress.txt
                exit 0
            fi
        fi
    fi
    
    sleep 300  # 每5分钟检查一次
done
