import asyncio
import json
import cv2
import numpy as np
import threading
import subprocess
import os
import websockets
import time
from gpiozero import MCP3008

# --- 설정 섹션 ---
PI_1_IP = "192.168.0.23"  # Pi 1 서버 IP
URI = f"ws://{PI_1_IP}:8000"

# [중요] index.html 경로 (현재 폴더 기준)
HTML_FILE_PATH = os.path.abspath("index.html") 
FIREFOX_URL = f"file://{HTML_FILE_PATH}"

# --- 화면 해상도 설정 ---
SCREEN_W = 1024
SCREEN_H = 600

# --- 조이스틱 설정 ---
try:
    swt_input = MCP3008(channel=0)
    vrx_input = MCP3008(channel=1)
    vry_input = MCP3008(channel=2)
    print("조이스틱 초기화 완료")
except:
    print("조이스틱 없음 (테스트 모드)")
    swt_input = None

# --- 전역 변수 ---
firefox_process = None

# --- 모터 제어 (백그라운드) ---
def map_speed(value):
    speed = (value - 0.5) * 200
    if abs(speed) < 15: return 0
    return int(max(min(speed, 100), -100))

async def run_motor_control():
    print("모터 제어 세션 시작")
    
    last_left = -999; last_right = -999; last_send_time = 0

    try:
        async with websockets.connect(URI) as ws:
            print("웹소켓 연결됨")
            
            while True:
                # 종료 버튼 체크
                if swt_input and swt_input.value < 0.1:
                    print("종료 버튼 감지")
                    break

                # 조이스틱 읽기
                speed_val = -map_speed(vry_input.value) 
                turn_val = map_speed(vrx_input.value)
                
                left = max(min(speed_val + turn_val, 100), -100)
                right = max(min(speed_val - turn_val, 100), -100)
                
                # 전송
                curr_time = time.time()
                if (left != last_left or right != last_right) or (curr_time - last_send_time > 1.0):
                    try:
                        await ws.send(json.dumps({
                            "source": "pi3_controller",
                            "command": "motor_control",
                            "payload": {"left": int(left), "right": int(right)}
                        }))
                        last_left = left; last_right = right; last_send_time = curr_time
                    except: break
                
                await asyncio.sleep(0.05)
    except Exception as e:
        print(f"모터 제어 에러: {e}")

# --- 메인 실행부 ---
def main():
    global firefox_process
    
    window_name = "Controller Idle"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    
    # [수정됨] 1024x600 해상도에 맞는 배경 생성 (검은색)
    bg_image = np.zeros((SCREEN_H, SCREEN_W, 3), dtype=np.uint8)
    
    # [수정됨] 글자 크기와 위치 조정 (화면 중앙 정렬)
    # "Ready to Connect" (위쪽 큰 글씨)
    cv2.putText(bg_image, "Ready to Connect", (220, 250), 
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 3)
    
    # "Press Joystick Button" (아래쪽 작은 글씨)
    cv2.putText(bg_image, "Press Joystick Button", (300, 350), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)

    print(f"시스템 시작 (해상도: {SCREEN_W}x{SCREEN_H})")

    while True:
        cv2.imshow(window_name, bg_image)
        key = cv2.waitKey(100)

        # 시작 버튼 감지
        if swt_input and swt_input.value < 0.1:
            print("시작 (Firefox 실행)")
            
            # 1. OpenCV 창 닫기
            cv2.destroyWindow(window_name)

            # 2. Firefox 실행
            try:
                firefox_process = subprocess.Popen([
                    "firefox","--kiosk", FIREFOX_URL
                ])
            except Exception as e:
                print(f"Firefox 에러: {e}")
                cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                continue
            
            time.sleep(1.5) # 버튼 뗄 시간
            
            # 3. 모터 제어 (블로킹)
            asyncio.run(run_motor_control())
            
            # 4. 종료 처리
            print("종료 (Firefox 닫기)")
            if firefox_process:
                firefox_process.terminate()
                try: firefox_process.wait(timeout=2)
                except: firefox_process.kill()
                firefox_process = None
            
            time.sleep(1.5)
            
            # 5. 대기 화면 복구
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        
        if key & 0xFF == ord('q'):
            break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        if firefox_process:
            firefox_process.terminate()
        cv2.destroyAllWindows()
