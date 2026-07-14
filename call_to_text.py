import ssl
import certifi

def create_certifi_https_context():
    return ssl.create_default_context(cafile=certifi.where())


ssl._create_default_https_context = create_certifi_https_context

from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

model_dir = os.getenv('ASR_MODEL_DIR', '')
if model_dir:
    os.environ.setdefault('MODELSCOPE_CACHE', model_dir)

import psycopg2
import psycopg2.extras
import pandas as pd
from pydub import AudioSegment
import whisper
from funasr import AutoModel
try:
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
except ImportError:
    rich_transcription_postprocess = None
import warnings
import requests
from requests.adapters import HTTPAdapter
import tempfile
import re
import time
import math
import shutil

warnings.filterwarnings('ignore')

def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'y', 'on')

RUN_LIMIT = env_int('RUN_LIMIT', 0)
LOOKBACK_DAYS = max(env_int('LOOKBACK_DAYS', 3), 1)
INSERT_BATCH_SIZE = max(env_int('INSERT_BATCH_SIZE', 100), 1)
DOWNLOAD_CHUNK_SIZE = max(env_int('DOWNLOAD_CHUNK_SIZE', 1024 * 1024), 1024)
DOWNLOAD_RETRIES = max(env_int('DOWNLOAD_RETRIES', 3), 1)
KEEP_AUDIO = env_bool('KEEP_AUDIO', False)
CLEANUP_AUDIO_HOURS = max(env_int('CLEANUP_AUDIO_HOURS', 24), 0)
PROGRESS_EVERY = max(env_int('PROGRESS_EVERY', 0), 0)
LOG_RECORDS = env_bool('LOG_RECORDS', False)
LOG_STAGES = env_bool('LOG_STAGES', True)
ASR_OUTPUT_TIMESTAMP = env_bool('ASR_OUTPUT_TIMESTAMP', True)
ASR_MAX_SEGMENT_CHARS = max(env_int('ASR_MAX_SEGMENT_CHARS', 80), 10)
ASR_MAX_SEGMENT_SECONDS = max(env_int('ASR_MAX_SEGMENT_SECONDS', 20), 5)
PARAFORMER_PUNC = env_bool('PARAFORMER_PUNC', True)
SHARD_COUNT = max(env_int('SHARD_COUNT', 1), 1)
SHARD_INDEX = env_int('SHARD_INDEX', 0)
if SHARD_COUNT > 1 and not 0 <= SHARD_INDEX < SHARD_COUNT:
    raise SystemExit(f"SHARD_INDEX 必须在 0 到 {SHARD_COUNT - 1} 之间")

def log_stage(message):
    if LOG_STAGES:
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}", flush=True)


AUDIO_ROOT = os.getenv('AUDIO_DIR', 'MP3')
RUN_AUDIO_DIR = os.path.join(AUDIO_ROOT, f'run_{os.getpid()}')


def record_audio_paths(record_id, host_path):
    return [
        os.path.join(host_path, f'{record_id}.mp3'),
        os.path.join(host_path, f'{record_id}_left.wav'),
        os.path.join(host_path, f'{record_id}_right.wav'),
    ]


def cleanup_paths(paths):
    for path in paths:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass


def cleanup_stale_audio(root_dir, max_age_hours):
    if max_age_hours <= 0 or not os.path.isdir(root_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for current_dir, _, files in os.walk(root_dir, topdown=False):
        for file_name in files:
            if not file_name.lower().endswith(('.mp3', '.wav')):
                continue
            path = os.path.join(current_dir, file_name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
                    removed += 1
            except OSError:
                pass
        if current_dir != root_dir:
            try:
                os.rmdir(current_dir)
            except OSError:
                pass
    if removed:
        log_stage(f'清理历史临时音频: {removed} 个文件')


if not KEEP_AUDIO:
    cleanup_stale_audio(AUDIO_ROOT, CLEANUP_AUDIO_HOURS)
os.makedirs(RUN_AUDIO_DIR, exist_ok=True)

db_host = os.getenv('DB_HOLOGRES_HOST')
db_name = os.getenv('DB_HOLOGRES_DATABASE', 'db')
db_user = os.getenv('DB_HOLOGRES_USER')
db_password = os.getenv('DB_HOLOGRES_PASSWORD')
db_port = int(os.getenv('DB_HOLOGRES_PORT', 80))

try:
    log_stage('连接数据库')
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
today = datetime.now().date()
start_day = (today - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
end_day = today.strftime('%Y-%m-%d')

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
coalesce(voice_length, EXTRACT(EPOCH FROM (TO_TIMESTAMP(end_time) - msg_time::timestamp))::text) as voice_length
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
and msg_time >= %s::date
and msg_time < %s::date
and (%s = 1 or mod(abs(hashtext(id::text)), %s) = %s)
and not exists (
    select 1
    from bi.call_to_text ctt
    where ctt.id = w.id
)
)t
order by msg_time
"""
params = [start_day, end_day, SHARD_COUNT, SHARD_COUNT, SHARD_INDEX]
if RUN_LIMIT > 0:
    sql += " limit %s"
    params.append(RUN_LIMIT)
log_stage(f'查询待处理音频: {start_day} <= msg_time < {end_day}')
df = pd.read_sql_query(sql, conn, params=tuple(params))
log_stage(f'待处理音频数量: {len(df)}')

http_session = requests.Session()
http_session.mount('http://', HTTPAdapter(pool_connections=16, pool_maxsize=16))
http_session.mount('https://', HTTPAdapter(pool_connections=16, pool_maxsize=16))

def downloaded_file(url,host):
    last_error = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            response = http_session.get(url, stream=True, timeout=300)
            response.raise_for_status()
            with open(host, 'wb') as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
            return
        except Exception as e:
            last_error = e
            if attempt < DOWNLOAD_RETRIES:
                time.sleep(min(2 ** attempt, 10))
    raise last_error


def normalize_int(value):
    if value is None:
        return None
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value == '-':
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number)


def audio_duration_seconds(audio):
    duration_ms = len(audio)
    if duration_ms <= 0:
        return None
    return max(int(round(duration_ms / 1000)), 1)


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

def clean_sensevoice_text(text):
    if not text:
        return ''
    if rich_transcription_postprocess is None:
        return text.strip()
    return rich_transcription_postprocess(text).strip()

def hotword_list():
    if not ASR_HOTWORDS:
        return []
    return [word for word in re.split(r'[\s,，]+', ASR_HOTWORDS) if word]

def format_plain_text_as_single_segment(text):
    text = text.strip()
    if not text:
        return {"segments": []}
    return {"segments": [{"start": 0, "end": 0, "text": text}]}

def ms_to_seconds(value):
    return round(float(value) / 1000, 3)

def funasr_text_from_result(result, clean_text=lambda text: text.strip()):
    if not result:
        return ''
    texts = []
    for item in result:
        text = clean_text(item.get('text', ''))
        if text:
            texts.append(text)
    return "\n".join(texts)

def append_segment(segments, start_ms, end_ms, text):
    text = text.strip()
    if not text:
        return
    segments.append({
        'start': ms_to_seconds(start_ms),
        'end': ms_to_seconds(max(end_ms, start_ms)),
        'text': text,
    })

def segments_from_timestamp_words(item, clean_text=lambda text: text.strip()):
    timestamps = item.get('timestamp') or []
    words = item.get('words') or []
    if not timestamps or not words or len(timestamps) != len(words):
        return []

    segments = []
    sentence_enders = set('。！？!?；;')
    soft_enders = set('，,、')
    max_duration_ms = ASR_MAX_SEGMENT_SECONDS * 1000
    start_ms = None
    end_ms = None
    text_parts = []

    for word, timestamp in zip(words, timestamps):
        if not word or not isinstance(timestamp, (list, tuple)) or len(timestamp) < 2:
            continue
        token_start_ms, token_end_ms = timestamp[0], timestamp[1]
        if start_ms is None:
            start_ms = token_start_ms
        end_ms = token_end_ms
        text_parts.append(word)
        text = ''.join(text_parts)
        should_flush = (
            word in sentence_enders
            or (word in soft_enders and len(text) >= ASR_MAX_SEGMENT_CHARS // 2)
            or len(text) >= ASR_MAX_SEGMENT_CHARS
            or end_ms - start_ms >= max_duration_ms
        )
        if should_flush:
            append_segment(segments, start_ms, end_ms, clean_text(text))
            start_ms = None
            end_ms = None
            text_parts = []

    if text_parts and start_ms is not None and end_ms is not None:
        append_segment(segments, start_ms, end_ms, clean_text(''.join(text_parts)))
    return segments

def segments_from_sentence_info(item, clean_text=lambda text: text.strip()):
    segments = []
    for seg in item.get('sentence_info') or []:
        text = clean_text(seg.get('sentence') or seg.get('text') or '')
        start = seg.get('start', 0)
        end = seg.get('end', start)
        append_segment(segments, start, end, text)
    return segments

def funasr_segments_from_result(result, clean_text=lambda text: text.strip()):
    segments = []
    for item in result or []:
        item_segments = segments_from_sentence_info(item, clean_text)
        if not item_segments:
            item_segments = segments_from_timestamp_words(item, clean_text)
        if item_segments:
            segments.extend(item_segments)
        else:
            text = clean_text(item.get('text', ''))
            if text:
                append_segment(segments, 0, 0, text)
    return {"segments": segments}

def generate_with_timestamp_fallback(model, generate_kwargs):
    try:
        return model.generate(**generate_kwargs)
    except KeyError as e:
        missing_key = str(e).strip("'\"")
        if missing_key != 'timestamp' or not generate_kwargs.get('output_timestamp'):
            raise
        fallback_kwargs = generate_kwargs.copy()
        fallback_kwargs.pop('output_timestamp', None)
        fallback_kwargs.pop('sentence_timestamp', None)
        return model.generate(**fallback_kwargs)

def transcribe_by_whisper_segments(audio_path, segment_transcriber):
    ws_result = whisper_timestamp_model.transcribe(audio_path)
    ws_segments = ws_result.get('segments', [])
    if not ws_segments:
        text = segment_transcriber(audio_path)
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
        tmp_path = tmp.name
        tmp.close()
        seg_audio.export(tmp_path, format="wav")
        try:
            text = segment_transcriber(tmp_path)
        except Exception:
            text = ''
        finally:
            os.unlink(tmp_path)
        if text.strip():
            segments.append({'start': seg['start'], 'end': seg['end'], 'text': text})

    return {"segments": segments}

def is_audio_usable(audio):
    return audio is not None and len(audio) >= 300 and audio.rms > 0

def safe_transcribe(audio_path):
    try:
        return transcribe(audio_path)
    except Exception as e:
        print(f"声道转写失败 {audio_path}: {e}", flush=True)
        return {"segments": []}

def result_texts(audios, record_id, host_path):
    os.makedirs(host_path, exist_ok=True)
    try:
        mono_channels = audios.split_to_mono()
    except Exception as e:
        print(f"音频拆分声道失败 id={record_id}: {e}", flush=True)
        mono_channels = []

    file_name = f'{record_id}_left.wav'
    file_path = os.path.join(host_path, file_name)
    left_channel = mono_channels[0] if mono_channels else audios
    if is_audio_usable(left_channel):
        left_channel.export(file_path, format="wav")
        result1 = safe_transcribe(file_path)
    else:
        result1 = {"segments": []}
    texts1 = format_segments(result1)    

    if len(mono_channels) > 1:
        file_name = f'{record_id}_right.wav'
        file_path = os.path.join(host_path, file_name)
        right_channel = mono_channels[1]
        if is_audio_usable(right_channel):
            right_channel.export(file_path, format="wav")
            result2 = safe_transcribe(file_path)
        else:
            result2 = {"segments": []}
        texts2 = format_segments(result2)

        merged_segments_list = merge_segments(result1["segments"], result2["segments"])  
        texts = format_merged_segments_list(merged_segments_list)  # 用格式化函数生成文本

    else:
        texts2='单声道'
        texts=texts1

    return texts1,texts2,texts



ASR_MODEL = os.getenv('ASR_MODEL', 'whisper').lower()
ASR_HOTWORDS = os.getenv('ASR_HOTWORDS', '').strip()
ASR_HOTWORD_LIST = hotword_list()
ASR_DEVICE = os.getenv('ASR_DEVICE', '').strip()
ASR_SEGMENT_MODE = os.getenv('ASR_SEGMENT_MODE', 'fast').lower()
SENSEVOICE_LANGUAGE = os.getenv('SENSEVOICE_LANGUAGE', 'zh').strip() or 'zh'

def model_kwargs_with_optional_device(**kwargs):
    if ASR_DEVICE:
        kwargs['device'] = ASR_DEVICE
    return kwargs

if ASR_MODEL == 'whisper':
    whisper_model_name = os.getenv('WHISPER_MODEL', 'base')
    model = whisper.load_model(whisper_model_name, download_root=os.getenv('ASR_MODEL_DIR'))
    model_name = f'whisper-{whisper_model_name}'
    def transcribe(audio_path):
        return model.transcribe(audio_path)
elif ASR_MODEL == 'paraformer':
    log_stage('加载 Paraformer 模型')
    paraformer_kwargs = {
        'model': "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        'vad_model': "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        'disable_update': True,
        'disable_pbar': True,
    }
    if PARAFORMER_PUNC:
        paraformer_kwargs['punc_model'] = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
    paraformer_kwargs = model_kwargs_with_optional_device(**paraformer_kwargs)
    paraformer_model = AutoModel(**paraformer_kwargs)
    log_stage('Paraformer 模型加载完成')
    whisper_timestamp_model = None
    if ASR_SEGMENT_MODE == 'whisper':
        whisper_timestamp_model = whisper.load_model("tiny", download_root=os.getenv('ASR_MODEL_DIR'))
    model_name = 'paraformer-large'
    def transcribe_paraformer_segment(segment_path):
        generate_kwargs = {'input': segment_path}
        if ASR_HOTWORDS:
            generate_kwargs['hotword'] = ASR_HOTWORDS
        pf_result = paraformer_model.generate(**generate_kwargs)
        return pf_result[0].get('text', '') if pf_result else ''

    def transcribe(audio_path):
        if ASR_SEGMENT_MODE == 'whisper':
            return transcribe_by_whisper_segments(audio_path, transcribe_paraformer_segment)
        generate_kwargs = {'input': audio_path, 'batch_size_s': 300}
        if ASR_HOTWORDS:
            generate_kwargs['hotword'] = ASR_HOTWORDS
        if ASR_OUTPUT_TIMESTAMP:
            generate_kwargs['output_timestamp'] = True
        pf_result = generate_with_timestamp_fallback(paraformer_model, generate_kwargs)
        if ASR_OUTPUT_TIMESTAMP:
            return funasr_segments_from_result(pf_result)
        return format_plain_text_as_single_segment(funasr_text_from_result(pf_result))
elif ASR_MODEL in ('sensevoice', 'sensevoice-small', 'sensevoicesmall'):
    log_stage('加载 SenseVoiceSmall 模型')
    sensevoice_model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        disable_update=True,
        disable_pbar=True,
        **model_kwargs_with_optional_device(),
    )
    log_stage('SenseVoiceSmall 模型加载完成')
    whisper_timestamp_model = None
    if ASR_SEGMENT_MODE == 'whisper':
        whisper_timestamp_model = whisper.load_model("tiny", download_root=os.getenv('ASR_MODEL_DIR'))
    model_name = 'sensevoice-small'
    def transcribe_sensevoice_segment(segment_path):
        generate_kwargs = {
            'input': segment_path,
            'cache': {},
            'language': SENSEVOICE_LANGUAGE,
            'use_itn': True,
            'batch_size_s': 60,
            'merge_vad': True,
            'merge_length_s': 15,
        }
        if ASR_HOTWORD_LIST:
            generate_kwargs['hotwords'] = ASR_HOTWORD_LIST
        sv_result = sensevoice_model.generate(**generate_kwargs)
        return clean_sensevoice_text(sv_result[0].get('text', '')) if sv_result else ''

    def transcribe(audio_path):
        if ASR_SEGMENT_MODE == 'whisper':
            return transcribe_by_whisper_segments(audio_path, transcribe_sensevoice_segment)
        generate_kwargs = {
            'input': audio_path,
            'cache': {},
            'language': SENSEVOICE_LANGUAGE,
            'use_itn': True,
            'batch_size_s': 300,
            'merge_vad': True,
            'merge_length_s': 15,
        }
        if ASR_HOTWORD_LIST:
            generate_kwargs['hotwords'] = ASR_HOTWORD_LIST
        if ASR_OUTPUT_TIMESTAMP:
            generate_kwargs['output_timestamp'] = True
        sv_result = generate_with_timestamp_fallback(sensevoice_model, generate_kwargs)
        if ASR_OUTPUT_TIMESTAMP:
            return funasr_segments_from_result(sv_result, clean_sensevoice_text)
        text = funasr_text_from_result(sv_result, clean_sensevoice_text)
        return format_plain_text_as_single_segment(text)
else:
    raise SystemExit(f"不支持的 ASR_MODEL: {ASR_MODEL}")

processed = 0
failed = 0
pending_rows = []
log_stage('开始处理音频')

insert_sql = """
INSERT INTO bi.call_to_text
(id, voice_id, from_id, receive_id, msg_type, msg_time, voice_length,
 content, voice_url, left_channel_text, right_channel_text,
 all_channel_text, transcribe_start_time, transcribe_end_time,
 transcribe_duration_ms, model)
VALUES
(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    voice_id = EXCLUDED.voice_id,
    from_id = EXCLUDED.from_id,
    receive_id = EXCLUDED.receive_id,
    msg_type = EXCLUDED.msg_type,
    msg_time = EXCLUDED.msg_time,
    voice_length = EXCLUDED.voice_length,
    content = EXCLUDED.content,
    voice_url = EXCLUDED.voice_url,
    left_channel_text = EXCLUDED.left_channel_text,
    right_channel_text = EXCLUDED.right_channel_text,
    all_channel_text = EXCLUDED.all_channel_text,
    transcribe_start_time = EXCLUDED.transcribe_start_time,
    transcribe_end_time = EXCLUDED.transcribe_end_time,
    transcribe_duration_ms = EXCLUDED.transcribe_duration_ms,
    model = EXCLUDED.model
"""

def flush_pending_rows():
    global processed, failed, pending_rows
    if not pending_rows:
        return
    rows = pending_rows
    pending_rows = []
    try:
        psycopg2.extras.execute_batch(cursor, insert_sql, rows, page_size=INSERT_BATCH_SIZE)
        conn.commit()
        processed += len(rows)
    except Exception as batch_error:
        conn.rollback()
        print(f"批量写入失败，回退单条写入: {batch_error}")
        for params in rows:
            try:
                cursor.execute(insert_sql, params)
                conn.commit()
                processed += 1
            except Exception as row_error:
                failed += 1
                print(f"单条写入失败 id={params[0]}: {row_error}")
                conn.rollback()

for _, row in df.iterrows():
    row_id = row['id']
    record_paths = record_audio_paths(row_id, RUN_AUDIO_DIR)
    try:
        url = row['voice_url']
        msg_type = row['msg_type']
        msg_time = row['msg_time']
        from_id = row['from_id']
        receive_id = row['receive_id']
        content = row['content']
        voice_id = row['voice_id']
        voice_length = normalize_int(row['voice_length'])
        if LOG_RECORDS:
            print('正在处理:', url)

        os.makedirs(RUN_AUDIO_DIR, exist_ok=True)
        audio_path = record_paths[0]
        downloaded_file(url, audio_path)
        stereo_audio = AudioSegment.from_file(audio_path, format="mp3")
        voice_length = audio_duration_seconds(stereo_audio) or voice_length
        transcribe_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        transcribe_perf_start = time.perf_counter()
        text1, text2, text = result_texts(stereo_audio, row_id, RUN_AUDIO_DIR)
        transcribe_duration_ms = int((time.perf_counter() - transcribe_perf_start) * 1000)
        transcribe_end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        params = (
            row_id, voice_id, from_id, receive_id, msg_type, msg_time,
            voice_length, content, url, text1, text2, text,
            transcribe_start_time, transcribe_end_time, transcribe_duration_ms, model_name
        )

        pending_rows.append(params)
        if len(pending_rows) >= INSERT_BATCH_SIZE:
            flush_pending_rows()
        if PROGRESS_EVERY and (processed + len(pending_rows)) % PROGRESS_EVERY == 0:
            print(f"进度: 已处理 {processed + len(pending_rows)}/{len(df)}")
    except Exception as e:
        failed += 1
        print(f"处理失败 id={row_id}: {e}")
        conn.rollback()
    finally:
        if not KEEP_AUDIO:
            cleanup_paths(record_paths)

flush_pending_rows()
cursor.close()
conn.close()
if not KEEP_AUDIO:
    shutil.rmtree(RUN_AUDIO_DIR, ignore_errors=True)
print('处理条数：', processed, '失败条数：', failed)
