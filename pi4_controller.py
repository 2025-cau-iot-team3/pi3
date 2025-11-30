import asyncio
import websockets
import json

# Pi 1 서버의 IP 주소를 입력하세요
SERVER_IP = "192.168.0.x"  # 예: "192.168.0.10"
SERVER_PORT = 8000
URI = f"ws://{SERVER_IP}:{SERVER_PORT}"

class Pi4Controller:
    def __init__(self):
        self.websocket = None

    async def connect(self):
        """서버에 연결하고 역할(pi4_controller)을 등록합니다."""
        try:
            print(f"서버({URI})에 연결 시도 중...")
            self.websocket = await websockets.connect(URI)
            print("서버 연결 성공!")

            # 1. 역할 등록 (Register)
            register_msg = {
                "command": "register",
                "payload": {
                    "role": "pi4_controller"
                }
            }
            await self.websocket.send(json.dumps(register_msg))
            print("역할 등록 요청 전송 완료.")
            
        except Exception as e:
            print(f"연결 실패: {e}")

    async def close(self):
        """연결 종료"""
        if self.websocket:
            await self.websocket.close()
            print("연결이 종료되었습니다.")

    async def _send_json(self, command, payload=None):
        """내부 메시지 전송 헬퍼 함수"""
        if self.websocket:
            msg = {"command": command}
            if payload:
                msg["payload"] = payload
            
            await self.websocket.send(json.dumps(msg))
            print(f"전송됨 -> Command: {command}, Payload: {payload}")
        else:
            print("오류: 서버에 연결되어 있지 않습니다.")

    # ==========================================
    # 1. 날씨 요청 함수
    # ==========================================
    async def get_weather_by_city(self, city_name):
        await self._send_json("get_weather_by_city", {"city_code": city_name})

    # ==========================================
    # 2. 현재 시간 요청 함수
    # ==========================================
    async def get_current_time(self):
        await self._send_json("get_current_time", {})

    # ==========================================
    # 3. 타이머 시작 함수
    # ==========================================
    async def start_timer(self, seconds):
        await self._send_json("start_timer", {"seconds": seconds})


# 사용 예시 (터미널에서 테스트용)
async def main():
    controller = Pi4Controller()
    
    # 1. 연결 시작
    await controller.connect()

    # 연결 유지를 위해 잠시 대기 (실제 로직에서는 필요에 따라 조정)
    await asyncio.sleep(1)

    print("\n--- 명령어 테스트 시작 ---")
    
    while True:
        print("\n1: 날씨 요청 (Seoul)")
        print("2: 현재 시간 요청")
        print("3: 타이머 설정 (10초)")
        print("q: 종료")
        
        # 비동기 환경에서 input을 받기 위해 executor 사용 (간단한 구현)
        choice = await asyncio.to_thread(input, "명령을 선택하세요: ")

        if choice == '1':
            await controller.get_weather_by_city("Seoul")
        elif choice == '2':
            await controller.get_current_time()
        elif choice == '3':
            await controller.start_timer(10)
        elif choice == 'q':
            break
        else:
            print("잘못된 입력입니다.")
        
        # 서버 반응 기다림 (옵션)
        await asyncio.sleep(0.1)

    await controller.close()

if __name__ == "__main__":
    # 라이브러리 설치 필요: pip install websockets
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("프로그램 종료")
