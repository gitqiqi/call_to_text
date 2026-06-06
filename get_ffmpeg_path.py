# ====== 替换或添加在你原来的 stereo_audio = AudioSegment.from_file(...) 的位置 ======
import subprocess
import os

print("=== 开始诊断 ===")

# 1. 再次确认路径
FFMPEG_PATH = "/home/wenba/laiqiqi/call_to_text/tools/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg"
FFPROBE_PATH = "/home/wenba/laiqiqi/call_to_text/tools/ffmpeg-master-latest-linux64-gpl/bin/ffprobe"
print(f"配置路径 - FFmpeg: {FFMPEG_PATH}")
print(f"配置路径 - FFprobe: {FFPROBE_PATH}")

host="/home/wenba/laiqiqi/call_to_text/MP3/downloaded_file.mp3"
# 2. 手动调用一次ffprobe，模拟pydub的行为
# pydub内部会先尝试用ffprobe探测文件信息
test_cmd = [FFPROBE_PATH, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', host]
print(f"执行命令: {' '.join(test_cmd)}")
try:
    result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
    print(f"ffprobe返回码: {result.returncode}")
    if result.returncode == 0:
        print("✅ ffprobe 命令执行成功！")
    else:
        print(f"❌ ffprobe 命令失败，错误信息: {result.stderr[:500]}")
except FileNotFoundError:
    print(f"❌ 错误：找不到命令 '{FFPROBE_PATH}'，请检查路径！")
except Exception as e:
    print(f"❌ 执行ffprobe时发生未知错误: {e}")

print("=== 诊断结束 ===")

# 3. 如果上面ffprobe成功，再尝试用pydub加载（可选）
# stereo_audio = AudioSegment.from_file(host, format="mp3")
