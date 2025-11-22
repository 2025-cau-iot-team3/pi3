import asyncio
import websockets
import json

# [중요] Pi 2와 유선으로 연결된 IP 주소 입력
# (예: 크로스 케이블 연결 시 Pi 1의 고정 IP)
PI_1_IP = "192.168.0.22" 
URI = f"ws://{PI_1_IP}:8000"

async def run_hardware_client():
    print(f"Pi 1 서버({URI})에 연결 시도 중...")

    async with websockets.connect(URI) as websocket:
        print("Pi 1 서버 연결 성공!")

        # 1. [등록] "나 Pi 1이야"라고 알림
        register_msg = {
            "command": "register",
            "payload": {"role": "pi2_hardware"}
        }
        await websocket.send(json.dumps(register_msg))
        print("등록 요청 전송 완료")

        # 2. [수신 대기] 명령 기다림
        async for message in websocket:
            try:
                data = json.loads(message)
                cmd = data.get("command")
                payload = data.get("payload")

                # --- A. 모터 제어 명령 (from Pi 3) ---
                if cmd == "motor_control":
                    left = payload.get("left")
                    right = payload.get("right")
                    print(f"모터 구동: L={left}, R={right}")
                    # TODO: 실제 모터 드라이버 코드 연결

                # --- B. YOLO 결과 수신 (from Pi 2) ---
                elif cmd == "yolo_detection":
                    objects = payload.get("objects", [])
                    print(f"YOLO 감지됨! ({len(objects)}개)")
                    for obj in objects:
                        print(f"   - {obj['label']} ({obj['confidence']})")
                    # TODO: LCD에 표시하거나 스피커로 알림

            except Exception as e:
                print(f"데이터 처리 에러: {e}")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run_hardware_client())
        except Exception as e:
            print(f"연결 실패/끊김: {e}")
            import time
            time.sleep(3)
