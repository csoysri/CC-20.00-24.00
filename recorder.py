import os
import subprocess
import glob
import time
import asyncio
import edge_tts
import shutil
import re  # เพิ่มการใช้งาน Regex จัดการชื่อโฟลเดอร์
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai

TARGET_URL = "https://cdn-fr1-eu.lncoperations.ee/hls/cnbc_live/index.m3u8"

# 🛠️ ตั้งเวลา: อัด 3 ชั่วโมง (10800 วินาที) / ตัดท่อนละ 7 นาที (420 วินาที)
RECORD_DURATION = 14400  
SEGMENT_DURATION = 420

# 🔑 ดึง Key จาก GitHub Secret อัตโนมัติ
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def record_stream(output_filename, duration):
    """บันทึกเสียงสดจาก CNBC เป็นไฟล์ .mp3"""
    print("🤖 เริ่มต้นทำงานระบบบันทึกเสียงอัตโนมัติ...")
    print(f"🎙️ กำลังบันทึกเสียงเป็นไฟล์ MP3 เป็นเวลา {duration} วินาที...")

    headers = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
        "Referer: https://livenewschat.eu/\r\n"
    )

    cmd = [
        'ffmpeg', '-y',
        '-headers', headers,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', TARGET_URL,
        '-t', str(duration),
        '-vn',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        os.path.abspath(output_filename) # บังคับใช้ Absolute Path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ FFmpeg Error:\n{result.stderr}")
        return False

    return os.path.exists(output_filename) and os.path.getsize(output_filename) > 0

def split_audio(input_file, date_prefix, folder_name, segment_time=420):
    """ตัดแบ่งไฟล์เสียง .mp3 พร้อมจัดเรียง timestamp รอยต่อให้สะอาด"""
    print(f"\n✂️ กำลังตัดแบ่งไฟล์ '{input_file}' เป็นท่อนละ {segment_time} วินาที...")
    
    output_pattern = os.path.join(folder_name, f"part_{date_prefix}_%03d.mp3")

    cmd = [
        'ffmpeg', '-y',
        '-i', os.path.abspath(input_file),
        '-f', 'segment',
        '-segment_time', str(segment_time),
        '-avoid_negative_ts', 'make_zero',
        '-c', 'copy',
        os.path.abspath(output_pattern) # บังคับใช้ Absolute Path
    ]
    subprocess.run(cmd, check=True)
    
    segments = sorted(glob.glob(os.path.join(folder_name, f"part_{date_prefix}_*.mp3")))
    print(f"🎉 ตัดไฟล์สำเร็จ! ได้ทั้งหมด {len(segments)} ไฟล์\n")
    return segments

def transcribe_and_translate(audio_path, max_retries=3):
    """ส่งไฟล์เสียงไปแปลไทยด้วย Gemini พร้อมควบคุมอาการหลอน/พูดซ้ำ"""
    if not client:
        print("  ⚠️ ไม่พบ GEMINI_API_KEY ข้ามการแปลภาษา")
        return None

    print(f"  🤖 [1/3] กำลังส่งเสียงให้ Gemini ฟังและแปลไทย...")

    for attempt in range(1, max_retries + 1):
        try:
            audio_file = client.files.upload(file=audio_path)

            prompt = """
            คำสั่งสำคัญที่สุด: ผลลัพธ์ของคุณต้องเป็น "ภาษาไทยล้วน 100%" เท่านั้น
            1. ฟังเสียงพูดภาษาอังกฤษทั้งหมด แล้วแปลบทพูดทุกประโยคออกมาเป็นภาษาไทยโดยตรง
            2. ห้ามพิมพ์ภาษาอังกฤษต้นฉบับออกมาเด็ดขาด
            3. ห้ามทำรูปแบบประโยคภาษาอังกฤษสลับกับภาษาไทย (Bilingual)
            4. แปลถ่ายทอดเนื้อหาคำพูดและบทวิเคราะห์ให้ครบถ้วนทุกประโยคตั้งแต่ต้นจนจบ
            5. ไม่ต้องใส่ตัวเลขเวลา (Timestamp)
            6. ให้ส่งออกเฉพาะข้อความภาษาไทยที่อ่านได้อย่างต่อเนื่อง สละสลวย เท่านั้น
            7. กฎเหล็กป้องกันอาการวนลูป: หากไฟล์เสียงช่วงใดมีเฉพาะเสียงดนตรี ดนตรีคั่นรายการ หรือเป็นความเงียบโดยไม่มีเสียงคนพูด ให้ข้ามไป ห้ามแต่งเรื่อง ห้ามเดาข้อความ และห้ามทวนประโยคเดิมซ้ำโดยเด็ดขาด
            8. หากทั้งไฟล์ไม่มีเสียงพูดเลย ให้ตอบกลับมาเพียงสั้นๆ ว่า "ไม่มีเสียงบรรยายข่าว"
            """

            response = client.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=[audio_file, prompt]
            )

            client.files.delete(name=audio_file.name)
            text_result = response.text.strip() if response.text else ""
            
            if "ไม่มีเสียงบรรยายข่าว" in text_result:
                return None
                
            return text_result

        except Exception as e:
            print(f"  ⚠️ ครั้งที่ {attempt} พบปัญหา ({e})")
            if attempt < max_retries:
                time.sleep(attempt * 5)
            else:
                return None

def split_text_into_chunks(text, max_chars=1200):
    """แบ่งข้อความเป็นก้อนย่อยๆ เพื่อป้องกัน Edge-TTS Timeout หรือปฏิเสธการประมวลผล"""
    lines = text.splitlines()
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        while len(line) > max_chars:
            cut_idx = line.rfind(' ', 0, max_chars)
            if cut_idx == -1:
                cut_idx = max_chars
            part = line[:cut_idx].strip()
            if part:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                chunks.append(part)
            line = line[cut_idx:].strip()

        if current_len + len(line) + 1 <= max_chars:
            current_chunk.append(line)
            current_len += len(line) + 1
        else:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks if chunks else [text]

async def text_to_speech_thai(text, output_audio_path, max_retries=4):
    """สร้างไฟล์เสียงอ่านข่าวไทย พร้อมแบ่งท่อนและ Retry ป้องกัน No audio was received"""
    print(f"  🗣️ [3/3] กำลังสร้างไฟล์เสียงอ่านข่าวไทย: {output_audio_path}...")
    
    clean_text = text.replace('*', '').replace('#', '').strip()
    if not clean_text:
        print("  ⚠️ ข้อความว่างเปล่า ข้ามการแปลงเป็นเสียง")
        return False

    chunks = split_text_into_chunks(clean_text, max_chars=1200)
    voice = "th-TH-PremwadeeNeural"
    temp_output = output_audio_path + ".tmp"

    try:
        with open(temp_output, "wb") as f_out:
            for idx, chunk in enumerate(chunks, 1):
                chunk_success = False
                for attempt in range(1, max_retries + 1):
                    try:
                        tts = edge_tts.Communicate(chunk, voice)
                        audio_received = False
                        
                        async for message in tts.stream():
                            if message["type"] == "audio":
                                f_out.write(message["data"])
                                audio_received = True

                        if audio_received:
                            chunk_success = True
                            break
                        else:
                            raise RuntimeError("No audio received")

                    except Exception as e:
                        if attempt < max_retries:
                            print(f"  ⚠️ Edge-TTS พบปัญหา (ท่อน {idx}/{len(chunks)} รอบ {attempt}): {e} กำลังลองใหม่...")
                            await asyncio.sleep(attempt * 2)
                        else:
                            print(f"  ❌ สังเคราะห์เสียงล้มเหลวที่ท่อน {idx}/{len(chunks)}: {e}")

                if not chunk_success:
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                    return False

        if os.path.exists(output_audio_path):
            os.remove(output_audio_path)
        os.rename(temp_output, output_audio_path)
        print(f"  ✅ บันทึกเสียงพากย์ไทยสำเร็จ!")
        return True

    except Exception as e:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except:
                pass
        print(f"  ❌ สังเคราะห์เสียงอ่านข่าวล้มเหลว: {e}")
        return False

def process_single_file(seg_path, current_idx, total_files):
    print(f"==================================================")
    print(f"🔄 กำลังประมวลผลไฟล์ [{current_idx}/{total_files}]: {os.path.basename(seg_path)}")
    print(f"==================================================")

    th_text = transcribe_and_translate(seg_path)
    if not th_text:
        print(f"  ⏭️ ข้ามการสร้างเสียงสำหรับไฟล์ {os.path.basename(seg_path)} (ไม่มีเสียงพูดหรือแปลไม่สำเร็จ)")
        return None

    txt_filename = seg_path.replace(".mp3", "_แปลไทย.txt")
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(th_text)
    print(f"  💾 [2/3] บันทึกคำแปลข้อความ: {txt_filename}")

    tts_filename = seg_path.replace(".mp3", "_อ่านข่าวไทย.mp3")
    success = asyncio.run(text_to_speech_thai(th_text, tts_filename))
    
    if not success or not os.path.exists(tts_filename):
        print(f"  ⏭️ ข้ามไฟล์ {os.path.basename(seg_path)} เนื่องจากสร้างไฟล์เสียงไม่สำเร็จ")
        return None

    print(f"🎉 เสร็จสิ้นขั้นตอนของไฟล์ [{current_idx}/{total_files}]\n")
    return tts_filename

# --- 🛠️ ฟังก์ชันสำหรับต่อไฟล์เสียงแบบบังคับ Path ป้องกันปัญหาไฟล์ซ่อน ---
def concat_audio_files(input_files, output_filename):
    """ฟังก์ชันย่อยสำหรับรวมไฟล์เสียงด้วย FFmpeg"""
    if len(input_files) == 1:
        shutil.copy(os.path.abspath(input_files[0]), os.path.abspath(output_filename))
        return True

    cmd = ['ffmpeg', '-y']
    for f in input_files:
        # 🔑 บังคับให้ FFmpeg อ่านไฟล์จากที่อยู่จริง (Absolute Path) ป้องกันบั๊กใน Sub folder
        cmd.extend(['-i', os.path.abspath(f)])

    n = len(input_files)
    filter_inputs = "".join([f"[{i}:a]" for i in range(n)])
    filter_str = f"{filter_inputs}concat=n={n}:v=0:a=1[outa]"

    cmd.extend([
        '-filter_complex', filter_str,
        '-map', '[outa]',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        '-ar', '44100',
        '-ac', '2',
        '-map_metadata', '-1',
        os.path.abspath(output_filename) # 🔑 บังคับ Output เป็น Absolute Path
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Error merging files:\n{result.stderr}")
    return result.returncode == 0 and os.path.exists(output_filename)

def merge_and_cleanup_tts(tts_files, final_output_filename, folder_name):
    """รวมไฟล์เสียงอ่านข่าวโดยแบ่งทำทีละ 10 ไฟล์"""
    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์เสียงทั้งหมด {len(tts_files)} ไฟล์ (แบ่งทำทีละ 10 ไฟล์)...")

    if not tts_files:
        print("⚠️ ไม่มีไฟล์เสียงสำหรับรวม")
        return

    if len(tts_files) == 1:
        shutil.move(os.path.abspath(tts_files[0]), os.path.abspath(final_output_filename))
        print(f"✅ มีเพียงไฟล์เดียว บันทึกสำเร็จ: {final_output_filename}")
        return

    batch_size = 10
    intermediate_files = []

    for i in range(0, len(tts_files), batch_size):
        batch = tts_files[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        temp_output = os.path.join(folder_name, f"temp_batch_{batch_num}.mp3")

        print(f"  ⏳ กำลังรวมกลุ่มที่ {batch_num} ({len(batch)} ไฟล์) -> {os.path.basename(temp_output)} ...")
        success = concat_audio_files(batch, temp_output)

        if success:
            intermediate_files.append(temp_output)
            for f in batch:
                try:
                    os.remove(f)
                except Exception as e:
                    pass
        else:
            print(f"  ❌ รวมกลุ่มที่ {batch_num} ล้มเหลว!")

    if not intermediate_files:
        print("❌ ไม่สามารถสร้างไฟล์ชั่วคราวได้เลย ยกเลิกการรวมไฟล์")
        return

    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์กลุ่มย่อยทั้งหมด {len(intermediate_files)} ไฟล์ เป็นไฟล์สุดท้าย...")
    
    if len(intermediate_files) == 1:
        shutil.move(os.path.abspath(intermediate_files[0]), os.path.abspath(final_output_filename))
        print(f"✅ รวมไฟล์สำเร็จสมบูรณ์: {final_output_filename}")
    else:
        final_success = concat_audio_files(intermediate_files, final_output_filename)
        
        if final_success:
            print(f"✅ รวมไฟล์สำเร็จสมบูรณ์: {final_output_filename}")
            for f in intermediate_files:
                try:
                    os.remove(f)
                    print(f"  🗑️ ลบไฟล์กลุ่มย่อย: {os.path.basename(f)}")
                except:
                    pass
        else:
            print("❌ การรวมไฟล์ขั้นสุดท้ายล้มเหลว")

if __name__ == "__main__":
    th_time = datetime.now(ZoneInfo("Asia/Bangkok"))
    date_str = th_time.strftime('%Y%m%d_%H%M%S')
    
    # 📁 1. ระบุชื่อโฟลเดอร์หลัก "CNBC"
    base_dir = "CNBC"
    
    # 📁 2. ดึงชื่อไฟล์ yml จาก Github Actions (เช่น CC 20.00-24.00)
    yml_name = os.getenv("GITHUB_WORKFLOW", "CNBC_Workflow")
    
    # ลบอักขระพิเศษ (เช่น / \ : * ? " < > |) เพื่อป้องกันไม่ให้เผลอสร้างโฟลเดอร์ซ้อนกันแบบผิดปกติ
    yml_name = re.sub(r'[\\/*?:"<>|]', "", yml_name) 
    yml_name = yml_name.replace(" ", "_")
    
    # 📁 3. นำชื่อ yml มาต่อด้วย เวลา-นาที (HH-MM)
    folder_time = th_time.strftime('%H-%M') 
    
    # 📁 4. สร้าง Path โฟลเดอร์เป้าหมาย (ซับโฟลเดอร์แค่ 1 ชั้น) -> CNBC/CC_20.00-24.00_HH-MM
    folder_name = os.path.join(base_dir, f"{yml_name}_{folder_time}")
    os.makedirs(folder_name, exist_ok=True)
    print(f"📁 สร้างโฟลเดอร์สำหรับเก็บผลลัพธ์: {folder_name}\n")

    main_file = os.path.join(folder_name, f"raw_cnbc_{date_str}.mp3")

    success = record_stream(main_file, RECORD_DURATION)

    if success:
        print(f"✅ บันทึกไฟล์หลักสำเร็จ: {main_file}")
        segment_files = split_audio(main_file, date_str, folder_name, SEGMENT_DURATION)
        total_segments = len(segment_files)
        
        generated_tts_files = []

        for idx, seg in enumerate(segment_files, start=1):
            tts_file = process_single_file(seg, idx, total_segments)
            if tts_file and os.path.exists(tts_file):
                generated_tts_files.append(tts_file)
            time.sleep(2)

        print("✨ ประมวลผลและแปลครบทุกไฟล์เรียบร้อยแล้ว!")
        
        # ดำเนินการรวมไฟล์เสียงอ่านข่าวทั้งหมดและลบไฟล์ย่อย
        if generated_tts_files:
            final_audio = os.path.join(folder_name, f"final_thai_news_{date_str}.mp3")
            merge_and_cleanup_tts(generated_tts_files, final_audio, folder_name)
            
    else:
        print("❌ การบันทึกเสียงล้มเหลว")
