import asyncio
import json
import logging
import websockets
import numpy as np
import threading
import queue
import time
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRecorder
from gpiozero import MCP3008
from time import sleep

# --- [SDL2 라이브러리 추가] ---
import sdl2
import sdl2.ext
import ctypes

# --- 설정 섹션 ---
PI_1_IP = "mypi1.local"  # Pi 1 IP 주소
URI = f"ws://{PI_1_IP}:8000"

# --- GPIOZero 조이스틱 설정 ---
try:
    swt_input = MCP3008(channel=0)
    vrx_input = MCP3008(channel=1)
    vry_input = MCP3008(channel=2)
    print("MCP3008(Gpiozero) 초기화 완료.")
except Exception as e:
    print(f"MCP3008 초기화 실패: {e}")
    swt_input = None

# --- 전역 변수 ---
frame_queue = queue.Queue()
is_connected = False
rtc_config = RTCConfiguration(
    iceServers=[RTCIceServer("stun:stun.l.google.com:19302")]
)

# --- 헬퍼 함수 ---
def map_speed(value):
    speed = (value - 0.5) * 200 
    if abs(speed) < 15: return 0
    return int(max(min(speed, 100), -100))

# --- WebRTC 세션 로직 ---
async def run_connection_session():
    global is_connected
    pc = RTCPeerConnection(configuration=rtc_config)
    
    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            async def receive_video():
                while is_connected:
                    try:
                        frame = await track.recv()
                        
                        # [핵심 변경] OpenCV 변환 제거! 
                        # YUV 데이터를 그대로 큐에 넣습니다. (CPU 부하 최소화)
                        # aiortc 프레임 자체를 넘깁니다.
                        
                        if frame_queue.qsize() < 2:
                            frame_queue.put(frame)
                        else:
                            try:
                                frame_queue.get_nowait()
                                frame_queue.put(frame)
                            except: pass
                    except:
                        break
            asyncio.create_task(receive_video())

        elif track.kind == "audio":
            async def play_audio():
                try:
                    recorder = MediaRecorder("default", format="pulse", options={"fflags": "nobuffer"})
                    recorder.addTrack(track)
                    await recorder.start()
                    while is_connected: await asyncio.sleep(1)
                except: pass
            asyncio.create_task(play_audio())

    try:
        print(f"서버({URI}) 연결 시도...")
        async with websockets.connect(URI) as websocket:
            print("Websocket 연결 성공!")
            
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            while pc.iceGatheringState != "complete": await asyncio.sleep(0.1)

            await websocket.send(json.dumps({
                "source": "pi_3_controller",
                "command": "video_offer",
                "payload": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            }))

            response = await websocket.recv()
            data = json.loads(response)
            if data['command'] == 'video_answer':
                desc = RTCSessionDescription(sdp=data['payload']['sdp'], type=data['payload']['type'])
                await pc.setRemoteDescription(desc)
                print("P2P 연결 수립 완료.")

            last_left = -999; last_right = -999; last_send_time = 0
            
            while is_connected:
                if swt_input and swt_input.value < 0.1:
                    print("종료 버튼 눌림."); is_connected = False; await asyncio.sleep(1.0); break

                speed_val = -map_speed(vry_input.value)
                turn_val = map_speed(vrx_input.value)
                
                left = max(min(speed_val + turn_val, 100), -100)
                right = max(min(speed_val - turn_val, 100), -100)
                
                curr_time = time.time()
                if (left != last_left or right != last_right) or (curr_time - last_send_time > 1.0):
                    try:
                        await websocket.send(json.dumps({
                            "source": "pi3_controller", 
                            "command": "motor_control", 
                            "payload": {"left": int(left), "right": int(right)}
                        }))
                        last_left = left; last_right = right; last_send_time = curr_time
                    except: break
                await asyncio.sleep(0.02) # 반응속도 UP

    except Exception as e: print(f"세션 에러: {e}")
    finally:
        await pc.close()
        is_connected = False
        while not frame_queue.empty(): 
            try: frame_queue.get_nowait()
            except: pass

async def main_state_loop():
    global is_connected
    while True:
        if not is_connected:
            if swt_input and swt_input.value < 0.1:
                print("연결 시작..."); is_connected = True; await asyncio.sleep(1.0)
                await run_connection_session()
            await asyncio.sleep(0.1)
        else: await asyncio.sleep(0.1)

def start_network_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_state_loop())

# --- 메인 실행부 (SDL2 GUI) ---
if __name__ == "__main__":
    net_thread = threading.Thread(target=start_network_thread, daemon=True)
    net_thread.start()

    # 1. SDL2 초기화
    sdl2.ext.init()
    
    # 2. 창 생성 (전체 화면 설정 가능)
    # window = sdl2.ext.Window("CCTV Controller", size=(640, 480))
    window = sdl2.ext.Window("CCTV Controller", size=(640, 480), flags=sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP)
    window.show()

    # 3. 렌더러 생성 (하드웨어 가속 사용)
    renderer = sdl2.ext.Renderer(window, flags=sdl2.SDL_RENDERER_ACCELERATED)
    
    # 4. 텍스처 생성 (YUV 포맷: IYUV / YV12)
    # PiCamera2의 YUV420 포맷은 보통 IYUV(I420)와 호환됩니다.
    texture = sdl2.SDL_CreateTexture(
        renderer.sdlrenderer,
        sdl2.SDL_PIXELFORMAT_IYUV, 
        sdl2.SDL_TEXTUREACCESS_STREAMING,
        640, 480 # 해상도 (Pi 1 서버와 일치해야 함)
    )

    running = True
    event = sdl2.SDL_Event()

    print("SDL2 GUI 시작... (YUV 하드웨어 가속)")

    try:
        while running:
            # (1) 이벤트 처리 (종료 키 등)
            while sdl2.SDL_PollEvent(ctypes.byref(event)) != 0:
                if event.type == sdl2.SDL_QUIT:
                    running = False
                elif event.type == sdl2.SDL_KEYDOWN:
                    if event.key.keysym.sym == sdl2.SDLK_q:
                        running = False
            
            # (2) 영상 렌더링
            if is_connected and not frame_queue.empty():
                try:
                    frame = frame_queue.get(timeout=0.01)
                    
                    # aiortc frame -> YUV bytes 추출 (변환 과정 없음!)
                    # planes[0]: Y, planes[1]: U, planes[2]: V
                    # SDL_UpdateYUVTexture는 Y, U, V 데이터를 각각 받습니다.
                    
                    y_plane = frame.planes[0]
                    u_plane = frame.planes[1]
                    v_plane = frame.planes[2]

                    # 1. 파이썬 bytes로 데이터 추출
                    y_bytes = bytes(y_plane)
                    u_bytes = bytes(u_plane)
                    v_bytes = bytes(v_plane)
                    
                    # 2. [핵심 수정] bytes를 C언어 포인터(LP_c_ubyte)로 변환
                    # c_char_p로 감싼 뒤 -> POINTER(c_ubyte)로 캐스팅해야 PySDL2가 받아줍니다.
                    y_ptr = ctypes.cast(ctypes.c_char_p(y_bytes), ctypes.POINTER(ctypes.c_ubyte))
                    u_ptr = ctypes.cast(ctypes.c_char_p(u_bytes), ctypes.POINTER(ctypes.c_ubyte))
                    v_ptr = ctypes.cast(ctypes.c_char_p(v_bytes), ctypes.POINTER(ctypes.c_ubyte))
                    
                    # 텍스처 업데이트 (CPU 연산 없이 메모리 복사만 일어남)
                    sdl2.SDL_UpdateYUVTexture(
                        texture, None,
                        y_ptr, y_plane.line_size,
                        u_ptr, u_plane.line_size,
                        v_ptr, v_plane.line_size
                    )
                    
                    # 렌더러 초기화 및 그리기
                    sdl2.SDL_RenderClear(renderer.sdlrenderer)
                    sdl2.SDL_RenderCopy(renderer.sdlrenderer, texture, None, None)
                    sdl2.SDL_RenderPresent(renderer.sdlrenderer)
                    
                except queue.Empty: pass
                except Exception as e: print(f"Render Error: {e}")

            elif not is_connected:
                # 연결 끊김: 검은 화면
                sdl2.SDL_SetRenderDrawColor(renderer.sdlrenderer, 0, 0, 0, 255)
                sdl2.SDL_RenderClear(renderer.sdlrenderer)
                sdl2.SDL_RenderPresent(renderer.sdlrenderer)
            
            # CPU 사용량을 낮추기 위한 약간의 딜레이
            time.sleep(0.005)

    except KeyboardInterrupt: pass
    finally:
        is_connected = False
        sdl2.ext.quit()

