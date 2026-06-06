from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
import pandas as pd
from pydub import AudioSegment
from snownlp import SnowNLP
from pydub.silence import detect_silence
from pydub.silence import split_on_silence
from zhconv import convert
import whisper
## import opencc
import warnings
import requests
import re
import sys
import subprocess
import os


host=os.getenv('DB_HOLOGRES_HOST')
name=os.getenv('DB_HOLOGRES_DATABASE')
user=os.getenv('DB_HOLOGRES_USER')
password=os.getenv('DB_HOLOGRES_PASSWORD')

conn = psycopg2.connect(
    host=host,  # Hologres实例连接地址
    port=80,  # 通常是80（默认）或443
    user=user,
    password=password,
    database='db')  # 设置SQL语句超时为60秒（单位毫秒）

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
day=(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

sql=f"""
SELECT 
id,
msg_type,
msg_time,
from_id,
receive_id,
content,
coalesce(voice_id,sdk_file_id) as voice_id,
coalesce(voice_url,meet_url) as voice_url,
coalesce(voice_length, cast(EXTRACT(EPOCH FROM (TO_TIMESTAMP(end_time) - msg_time::timestamp)) as char)) as voice_length
from (
select 
id,
msg_type,
msg_time,
from_id,
receive_id,
content,
content::jsonb -> 'voice' ->> 'sdkFileId' AS voice_id,
content::jsonb -> 'voice' ->> 'ossUrl' as voice_url,
content::jsonb -> 'voice' ->> 'playLength' as voice_length,

content::jsonb -> 'meetingVoiceCall' ->> 'sdkFileId' AS sdk_file_id,
content::jsonb -> 'meetingVoiceCall' ->> 'ossUrl' AS meet_url,
(content::jsonb -> 'meetingVoiceCall' ->> 'endTime')::bigint as end_time

from book.we_chat_data
WHERE  msg_type in ('meeting_voice_call','voice')
and date(msg_time)='{day}'
)t
"""
df = pd.read_sql_query(sql, conn)


def downloaded_file(url,host):
    # 发送GET请求
    response = requests.get(url, stream=True)

    if response.status_code == 200:
        # 打开一个本地文件用于写入
        with open(host, 'wb') as f:
            # 循环下载文件
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:  # 过滤掉保持连接的chunk
                    f.write(chunk)
        print("文件下载成功")
    else:
        print("请求失败，状态码：", response.status_code)


def silence_range(silence_ranges,role):     
    split_time=[]
    #检查说话时间范围
    for i in range(len(silence_ranges)-1):
        start=silence_ranges[i][0]
        split_start=silence_ranges[i][1]#静音的结束时间就是说话的开始时间
        split_end=silence_ranges[i+1][0]#静音开始时间就是说话结束时间
        split_time.append((split_start,split_end,role))
    return(split_time)

def merge_segments(left_segs, right_segs):
    """内部函数：合并排序两个声道的话段"""
    merged = []
    # 标记声道
    for seg in left_segs:
        seg_copy = seg.copy()
        seg_copy['channel'] = '左'
        merged.append(seg_copy)
    for seg in right_segs:
        seg_copy = seg.copy()
        seg_copy['channel'] = '右'
        merged.append(seg_copy)
    merged.sort(key=lambda x: x['start'])
    return merged

def format_segments(result):
    texts_list = [f"[{segment['start']:.1f}s - {segment['end']:.1f}s]: {segment['text']}" for segment in result["segments"]]
    return  "\n".join(texts_list)

def format_merged_segments_list(result_list):
    lines = []
    for seg in result_list:
        # 注意：合并后的话段多了 'channel' 字段
        lines.append(f"[{seg['start']:.1f}s-{seg['end']:.1f}s] [{seg['channel']}]: {seg['text']}")
    return "\n".join(lines)

def result_texts(audios,min_silence_len,silence_thresh,seek_step):
    host_path = 'MP3/'

    file_name = 'left_channel.wav'
    file_path = os.path.join(host_path, file_name)
    left_channel = audios.split_to_mono()[0]#左声道
    left_channel.export(file_path, format="wav")
    audio1 = AudioSegment.from_file(file_path, format="wav") # 加载音频文件
    result1 = model.transcribe(file_path)
    texts1 = format_segments(result1)    

    if audios.channels > 1:
        file_name = 'right_channel.wav'
        file_path = os.path.join(host_path, file_name)
        right_channel = audios.split_to_mono()[1]
        right_channel.export(file_path, format="wav")
        audio2 = AudioSegment.from_file(file_path, format="wav")# 加载音频文件
        result2 = model.transcribe(file_path)
        texts2 = format_segments(result2)

        merged_segments_list = merge_segments(result1["segments"], result2["segments"])  
        texts = format_merged_segments_list(merged_segments_list)  # 用格式化函数生成文本

    else:
        texts2='单声道'
        texts=texts1

    return texts1,texts2,texts



# 忽略所有警告
import ssl
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings('ignore')
model = whisper.load_model("base")


# 设置分割参数
min_silence_len = 400  # 最小静音长度，单位毫秒
silence_thresh = -50  # 静音阈值，分贝值，越小越严格
keep_silence = 300  # 保留静音长度，单位毫秒

#数据集
for index, row in df.iterrows():
    id = row['id']
    url = row['voice_url']
    msg_type = row['msg_type']
    msg_time = row['msg_time']
    from_id = row['from_id']
    receive_id = row['receive_id']
    content = row['content']
    voice_id = row['voice_id']  
    voice_length = row['voice_length']
    print('正在处理:',url)

    host=r'/home/wenba/laiqiqi/call_to_text/MP3/downloaded_file.mp3'
    downloaded_file(url,host)
    stereo_audio = AudioSegment.from_file(host, format="mp3")
    transcribe_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    text1,text2,text=result_texts(stereo_audio,300,-40,1)
    transcribe_end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
   

    insert_sql = """
    INSERT INTO bi.call_to_text
    (id, voice_id, from_id, receive_id, msg_type, msg_time, voice_length,
     content, voice_url, left_channel_text, right_channel_text,
     all_channel_text, transcribe_start_time, transcribe_end_time)
    VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

    # 准备参数列表
    params = (
    id, voice_id, from_id, receive_id, msg_type, msg_time,
    voice_length, content, url, text1, text2, text,
    transcribe_start_time, transcribe_end_time
    )

    cursor.execute(insert_sql, params)
    conn.commit()
cursor.close()
conn.close()
print('处理条数：',index)
