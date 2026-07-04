import ssl
import certifi

def create_certifi_https_context():
    return ssl.create_default_context(cafile=certifi.where())


ssl._create_default_https_context = create_certifi_https_context

from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
import pandas as pd
from pydub import AudioSegment
import whisper
from funasr import AutoModel
import warnings
import requests
import tempfile
import os
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings('ignore')

model_dir = os.getenv('ASR_MODEL_DIR', '')
if model_dir:
    os.environ.setdefault('MODELSCOPE_CACHE', model_dir)

db_host = os.getenv('DB_HOLOGRES_HOST')
db_name = os.getenv('DB_HOLOGRES_DATABASE', 'db')
db_user = os.getenv('DB_HOLOGRES_USER')
db_password = os.getenv('DB_HOLOGRES_PASSWORD')
db_port = int(os.getenv('DB_HOLOGRES_PORT', 80))

try:
    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name,
    )
except psycopg2.OperationalError as e:
    raise SystemExit(f"数据库连接失败: {e}")

cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
day=(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

sql="""
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

from book.we_chat_data w
WHERE  msg_type in ('meeting_voice_call','voice')
and date(msg_time)=%s
and not exists (
    select 1
    from bi.call_to_text ctt
    where ctt.id = w.id
)
)t
"""
df = pd.read_sql_query(sql, conn, params=(day,))


def downloaded_file(url,host):
    response = requests.get(url, stream=True, timeout=300)
    response.raise_for_status()
    with open(host, 'wb') as f:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
    print("文件下载成功")


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

def result_texts(audios, record_id):
    host_path = 'MP3/'

    file_name = f'{record_id}_left.wav'
    file_path = os.path.join(host_path, file_name)
    left_channel = audios.split_to_mono()[0]
    left_channel.export(file_path, format="wav")
    result1 = transcribe(file_path)
    texts1 = format_segments(result1)    

    if audios.channels > 1:
        file_name = f'{record_id}_right.wav'
        file_path = os.path.join(host_path, file_name)
        right_channel = audios.split_to_mono()[1]
        right_channel.export(file_path, format="wav")
        result2 = transcribe(file_path)
        texts2 = format_segments(result2)

        merged_segments_list = merge_segments(result1["segments"], result2["segments"])  
        texts = format_merged_segments_list(merged_segments_list)  # 用格式化函数生成文本

    else:
        texts2='单声道'
        texts=texts1

    return texts1,texts2,texts



ASR_MODEL = os.getenv('ASR_MODEL', 'whisper').lower()

if ASR_MODEL == 'whisper':
    model = whisper.load_model("base", download_root=os.getenv('ASR_MODEL_DIR'))
    model_name = 'whisper-base'
    def transcribe(audio_path):
        return model.transcribe(audio_path)
elif ASR_MODEL == 'paraformer':
    paraformer_model = AutoModel(
        model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        disable_update=True,
    )
    whisper_timestamp_model = whisper.load_model("tiny", download_root=os.getenv('ASR_MODEL_DIR'))
    model_name = 'paraformer-large'
    def transcribe(audio_path):
        ws_result = whisper_timestamp_model.transcribe(audio_path)
        ws_segments = ws_result.get('segments', [])
        if not ws_segments:
            pf_result = paraformer_model.generate(input=audio_path)
            if not pf_result:
                return {"segments": []}
            text = pf_result[0].get('text', '')
            if text:
                return {"segments": [{"start": 0, "end": 0, "text": text}]}
            return {"segments": []}

        audio = AudioSegment.from_file(audio_path, format="wav")
        segments = []
        for seg in ws_segments:
            start_ms = int(seg['start'] * 1000)
            end_ms = int(seg['end'] * 1000)
            if end_ms - start_ms < 300:
                continue
            seg_audio = audio[start_ms:end_ms]
            if len(seg_audio) < 100:
                continue
            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            seg_audio.export(tmp.name, format="wav")
            try:
                pf_result = paraformer_model.generate(input=tmp.name)
                text = pf_result[0].get('text', '') if pf_result else ''
            except Exception:
                text = ''
            os.unlink(tmp.name)
            segments.append({'start': seg['start'], 'end': seg['end'], 'text': text})

        segments = [s for s in segments if s['text'].strip()]
        if segments:
            return {"segments": segments}
        return {"segments": []}

processed = 0
for _, row in df.iterrows():
    row_id = row['id']
    try:
        url = row['voice_url']
        msg_type = row['msg_type']
        msg_time = row['msg_time']
        from_id = row['from_id']
        receive_id = row['receive_id']
        content = row['content']
        voice_id = row['voice_id']
        voice_length = row['voice_length']
        print('正在处理:', url)

        mp3_dir = 'MP3'
        os.makedirs(mp3_dir, exist_ok=True)
        audio_path = os.path.join(mp3_dir, f'{row_id}.mp3')
        downloaded_file(url, audio_path)
        stereo_audio = AudioSegment.from_file(audio_path, format="mp3")
        transcribe_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        text1, text2, text = result_texts(stereo_audio, row_id)
        transcribe_end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        insert_sql = """
        INSERT INTO bi.call_to_text
        (id, voice_id, from_id, receive_id, msg_type, msg_time, voice_length,
         content, voice_url, left_channel_text, right_channel_text,
         all_channel_text, transcribe_start_time, transcribe_end_time, model)
        VALUES
        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

        params = (
            row_id, voice_id, from_id, receive_id, msg_type, msg_time,
            voice_length, content, url, text1, text2, text,
            transcribe_start_time, transcribe_end_time, model_name
        )

        cursor.execute(insert_sql, params)
        conn.commit()
        if cursor.rowcount:
            processed += 1
    except Exception as e:
        print(f"处理失败 id={row_id}: {e}")
        conn.rollback()

cursor.close()
conn.close()
print('处理条数：', processed)
