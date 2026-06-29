import asyncio
from playwright.async_api import async_playwright
from core.meeting_runtime.participant_policy import should_leave
from core.browser_bot.connection_monitor import plan_reconnect
from core.recording.audio_recorder import AudioRecorder
from pathlib import Path
from datetime import datetime


class TelemostBot:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.page = None
        self.browser = None
        self.context = None
        self._playwright = None
        self.recorder = None
        self.session_id = None


    async def _mute_microphone_js(self):
        try:
            result = await self.page.evaluate('''() => {
                const selectors = [
                    '[data-testid="mute-audio"]',
                    '[data-testid="turn-off-mic-button"]',
                    'button[aria-label*="микрофон"]',
                    'button[aria-label*="Microphone"]'
                ];
                for (let sel of selectors) {
                    const btn = document.querySelector(sel);
                    if (btn) {
                        btn.click();
                        return 'clicked: ' + sel;
                    }
                }
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    const aria = btn.getAttribute('aria-label') || '';
                    const dataTestId = btn.getAttribute('data-testid') || '';
                    if (aria.includes('микрофон') || aria.includes('Microphone') || 
                        dataTestId.includes('mic') || dataTestId.includes('mute') || dataTestId.includes('audio')) {
                        btn.click();
                        return 'clicked fallback: ' + (aria || dataTestId);
                    }
                }
                return 'not found';
            }''')
            print(f"[Bot] Microphone toggle result: {result}")
        except Exception as e:
            print(f"[Bot] Mute error: {e}")

    
    async def join(self, meeting_url: str, session_id: str = None):
        self.session_id = session_id
    
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        self.context = await self.browser.new_context(
            permissions=["camera", "microphone"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()
        await self.context.grant_permissions(["camera", "microphone"], origin=meeting_url)
    
        await self.page.goto(meeting_url)
        print("[Bot] Navigated to meeting page")
    
        try:
            await self.page.evaluate('''() => {
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText.includes('Продолжить в браузере')) {
                        btn.click();
                        return;
                    }
                }
            }''')
            print("[Bot] Clicked 'Продолжить в браузере' via JS")
            await self.page.wait_for_selector('button:has-text("Подключиться")', timeout=10000)
        except Exception as e:
            print(f"[Bot] No 'Продолжить в браузере' button found, continuing: {e}")
    
        try:
            result = await self.page.evaluate('''() => {
                const buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    const text = btn.innerText.trim();
                    const aria = btn.getAttribute('aria-label') || '';
                    if (text.includes('Подключиться') || aria.includes('Подключиться')) {
                        btn.click();
                        return 'clicked button with text: ' + text;
                    }
                }
                const testBtn = document.querySelector('[data-testid="join-button"]');
                if (testBtn) {
                    testBtn.click();
                    return 'clicked via data-testid';
                }
                return 'not found';
            }''')
            print(f"[Bot] 'Подключиться' result: {result}")
        except Exception as e:
            print(f"[Bot] Could not click 'Подключиться': {e}")
    
        # Даём время на загрузку интерфейса встречи
        # await asyncio.sleep(3)
    
        try:
            await self.page.wait_for_selector('[data-testid="participant-item"], video, [class*="participant"]', timeout=5000)
            print("[Bot] Meeting page detected")
        except:
            print("[Bot] Meeting page not detected, but continuing")

        await self._mute_microphone_js()

        self._start_recording()




    def _start_recording(self):
        """Запускает запись аудио в фоновом потоке."""
        if self.recorder is None:
            self.recorder = AudioRecorder()
            self.recorder.start()
            print("[Bot] Audio recording started")

    # def _stop_recording(self):
    #     """Останавливает запись и сохраняет файл."""
    #     if self.recorder is None:
    #         return
    #     self.recorder.stop()
    #     if self.session_id:
    #         filename = f"recording_{self.session_id}.wav"
    #     else:
    #         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #         filename = f"recording_{timestamp}.wav"
    #     recordings_dir = Path.cwd() / "recordings"
    #     recordings_dir.mkdir(exist_ok=True)
    #     filepath = recordings_dir / filename
    #     self.recorder.save(str(filepath))
    #     self.recorder.close()
    #     self.recorder = None
    #     print("[Bot] Audio recording saved")

    def _stop_recording(self):
        if self.recorder is None:
            return
        self.recorder.stop()
        if self.session_id:
            # Создаём папку для встречи
            meeting_dir = Path.cwd() / "recordings" / self.session_id
            meeting_dir.mkdir(parents=True, exist_ok=True)
            filename = meeting_dir / f"recording_{self.session_id}.wav"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{timestamp}.wav"
        self.recorder.save(str(filename))
        self.recorder.close()
        self.recorder = None
        print(f"[Bot] Audio recording saved to {filename}")




    # async def get_participant_count(self) -> int:
    #     """Возвращает количество участников на странице встречи."""
    #     try:
    #         count = await self.page.evaluate('''() => {
    #             const items = document.querySelectorAll('[data-testid="participant-item"]');
    #             return items.length;
    #         }''')
    #         return count
    #     except Exception as e:
    #         print(f"[Bot] Could not get participant count: {e}")
    #         return 1



    async def get_participant_count(self) -> int:
        """Возвращает количество других участников на встрече (исключая бота)."""
        try:
            count = await self.page.evaluate('''() => {    
                // 3. Если ничего не нашли, считаем видео-элементы
                const videos = document.querySelectorAll('video');
                let count = 0;
                for (let vid of videos) {
                    // Исключаем видео бота (обычно оно имеет класс local или находится в элементе с class="self")
                    const parent = vid.closest('[class*="self"], [class*="local"]');
                    if (!parent) {
                        count++;
                    }
                }
                return count;
            }''')
            print(f"[Bot] Other participants count: {count}")
            return count
        except Exception as e:
            print(f"[Bot] Could not get participant count: {e}")
            return 0  # Возвращаем 0, чтобы бот начал отсчёт одиночества



    async def leave(self):
        """Закрывает браузер и завершает запись."""
        self._stop_recording()

        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        print("[Bot] Left meeting")

    
    async def run(self, meeting_url: str, config: dict):
        """Основной цикл работы бота: вход, мониторинг, выход."""
        session_id = config.get("session_id", None)
        await self.join(meeting_url, session_id)
    
        alone_seconds = 0
        attempt = 0
        max_participants = 0  # начинаем с 0, так как бот считает других участников
    
        while True:
            participants = await self.get_participant_count()
            print(f"[Bot] Participants: {participants}")
    
            # Сохраняем максимальное количество участников (исключая бота)
            if participants > max_participants:
                max_participants = participants
                print(f"[Bot] Max participants detected: {max_participants}")
    
            if participants == 0:
                alone_seconds += 5
                if should_leave(alone_seconds, config.get("alone_leave_threshold", 20)):
                    print("[Bot] Leaving due to being alone too long")
                    break
            else:
                alone_seconds = 0
    
            if self.page.is_closed():
                decision = plan_reconnect(
                    previous_participants=participants,
                    attempt=attempt,
                    max_attempts=config.get("max_reconnect_attempts", 3),
                    interval_sec=config.get("reconnect_interval_sec", 10),
                )
                if decision["action"] == "reconnect":
                    print(f"[Bot] Reconnecting after {decision['delay_sec']}s")
                    await asyncio.sleep(decision["delay_sec"])
                    await self.join(meeting_url, session_id)
                    attempt += 1
                    continue
                else:
                    print(f"[Bot] Giving up: {decision['reason']}")
                    break
            await asyncio.sleep(5)
    
        # Сохраняем максимальное количество участников в конфиг для транскрипции
        config["max_participants"] = max_participants
        await self.leave()
    
    
    # async def run(self, meeting_url: str, config: dict):
    #     """Основной цикл работы бота: вход, мониторинг, выход."""
    #     session_id = config.get("session_id", None)
    #     await self.join(meeting_url, session_id)

    #     alone_seconds = 0
    #     attempt = 0
    #     while True:
    #         participants = await self.get_participant_count()
    #         print(f"[Bot] Participants: {participants}")
    #         if participants < 1:   #=
    #             alone_seconds += 5
    #             if should_leave(alone_seconds, config.get("alone_leave_threshold", 20)): #120
    #                 print("[Bot] Leaving due to being alone too long")
    #                 break
    #         else:
    #             alone_seconds = 0

    #         if self.page.is_closed():
    #             decision = plan_reconnect(
    #                 previous_participants=participants,
    #                 attempt=attempt,
    #                 max_attempts=config.get("max_reconnect_attempts", 3),
    #                 interval_sec=config.get("reconnect_interval_sec", 10),
    #             )
    #             if decision["action"] == "reconnect":
    #                 print(f"[Bot] Reconnecting after {decision['delay_sec']}s")
    #                 await asyncio.sleep(decision["delay_sec"])
    #                 await self.join(meeting_url, session_id)
    #                 attempt += 1
    #                 continue
    #             else:
    #                 print(f"[Bot] Giving up: {decision['reason']}")
    #                 break
    #         await asyncio.sleep(5)

    #     await self.leave()